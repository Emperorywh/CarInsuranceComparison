"""补传合并的预览与逐项解决（SPEC §2.9、§2.10；TASK-05 范围 7）。

业务不变量：
- 预览只读：MERGE_REVIEW 期间报价数据保持旧值（可查看可对比），变更
  清单展示旧值、新值、来源与“用户已编辑”标识；
- 解决是原子操作：请求必须覆盖全部 PENDING 变更；ACCEPT 在单个事务内
  合入新值，KEEP 保留旧值，全部处理完成后重算价格/校验/净支出并回到
  CONFIRMED；任何中途失败整体回滚，绝不形成半合并状态；
- 用户裁决过的数据一律按“用户已确认”口径保护（editedByUser=true、
  confidence=HIGH），后续重解析不再静默覆盖；
- merge_change 行保留 resolution 记录（审计），已解决的变更不再参与
  后续预览。

隐私边界：变更快照来自 merge_writer 的脱敏数据；本模块错误文案为固定
中文提示，不携带原文。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, QuoteStateError, ValidationError
from app.models import (
    MergeChange,
    PackageCoverage,
    Quote,
    QuoteCoverage,
    QuoteService,
    SupplementalPackage,
)
from app.models.enums import (
    ConfidenceLevel,
    CoverageCategory,
    ItemStatus,
    MergeChangeKind,
    MergeResolution,
    OfficialTotalStatus,
    PackageUnit,
    PriceItemStatus,
    QuoteStatus,
    ServiceType,
)
from app.schemas.split_merge import (
    MergeChangeRead,
    MergePreviewRead,
    MergeResolveRequest,
)
from app.services.normalization.engine import clean_name
from app.services.parser.merge_writer import (
    ENTITY_COVERAGE,
    ENTITY_PACKAGE,
    ENTITY_SCALAR,
    ENTITY_SERVICE,
    ENTITY_UNRECOGNIZED,
    FIELD_PACKAGE,
    FIELD_ROWS,
    SCALAR_LABELS,
    load_current_snapshot,
)
from app.services.quote_service import recalculate_quote_prices, touch_scalar_evidence

# 标量字段 → (值属性, 状态属性)；官方总价的状态枚举不同
_PRICE_SCALAR_ATTRS = {
    "commercialPremium": ("commercial_premium", "commercial_status", PriceItemStatus),
    "compulsoryPremium": ("compulsory_premium", "compulsory_status", PriceItemStatus),
    "vehicleTax": ("vehicle_tax", "vehicle_tax_status", PriceItemStatus),
    "packageTotal": ("package_total", "package_status", PriceItemStatus),
    "otherFees": ("other_fees", "other_fees_status", PriceItemStatus),
    "officialTotal": ("official_total", "official_total_status", OfficialTotalStatus),
}
_VEHICLE_SCALAR_ATTRS = {
    "vehicleModel": "vehicle_model",
    "vehicleSeats": "vehicle_seats",
    "firstRegDate": "first_reg_date",
    "isNev": "is_nev",
}

# 行级语义字段 → (ORM 属性, 取值类型)
_COVERAGE_FIELD_ATTRS = {
    "status": ("status", "status"),
    "coverageAmount": ("coverage_amount", "money"),
    "perSeatAmount": ("per_seat_amount", "money"),
    "seatCount": ("seat_count", "int"),
    "sharedCoverage": ("shared_coverage", "bool"),
    "premium": ("premium", "money"),
    "multiplier": ("multiplier", "money"),
    "condition": ("condition", "text"),
    "description": ("description", "text"),
}
_SERVICE_FIELD_ATTRS = {
    "status": ("status", "status"),
    "count": ("count", "int"),
    "cost": ("cost", "money"),
    "description": ("description", "text"),
}


def _dec(value: Any) -> Decimal | None:
    """JSON 数值 → 两位小数 Decimal（JSONB 中金额已序列化为 float）。"""
    return None if value is None else Decimal(str(value)).quantize(Decimal("0.01"))


def _coerce(value: Any, kind: str) -> Any:
    """按字段类型把 JSON 值转换为 ORM 值。"""
    if value is None:
        return None
    if kind == "money":
        return _dec(value)
    if kind == "int":
        return int(value)
    if kind == "bool":
        return bool(value)
    if kind == "status":
        return ItemStatus(value)
    return value  # text


# ---- 预览 ----


def _change_source(new_value: Any) -> tuple[int | None, int | None, str | None]:
    """从新值快照中提取证据定位：字段级在顶层，行/组取首个新行。"""
    if isinstance(new_value, dict):
        if "sourceFileId" in new_value:
            return (
                new_value.get("sourceFileId"),
                new_value.get("sourcePage"),
                new_value.get("sourceText"),
            )
        rows = new_value.get("rows") or new_value.get("coverages") or []
        if rows and isinstance(rows[0], dict) and "sourceFileId" in rows[0]:
            return (
                rows[0].get("sourceFileId"),
                rows[0].get("sourcePage"),
                rows[0].get("sourceText"),
            )
        if "coverages" not in new_value and "rows" not in new_value:
            return (
                new_value.get("sourceFileId"),
                new_value.get("sourcePage"),
                new_value.get("sourceText"),
            )
    return (None, None, None)


def _user_edited(current, change: MergeChange) -> bool:  # noqa: ANN001 - CurrentSnapshot
    """旧值是否被用户编辑过（该标识驱动默认 KEEP，用户编辑永不静默覆盖）。"""
    if change.entity_type == ENTITY_SCALAR:
        return current.scalar_edited.get(change.entity_key, False)
    groups_by_type = {
        ENTITY_COVERAGE: current.coverage_groups,
        ENTITY_UNRECOGNIZED: current.coverage_groups,
        ENTITY_SERVICE: current.service_groups,
        ENTITY_PACKAGE: current.package_groups,
    }
    groups = groups_by_type.get(change.entity_type, {})
    old_rows = groups.get((change.entity_type, change.entity_key), [])
    return any(row.get("editedByUser") for row in old_rows)


async def get_merge_preview(db: AsyncSession, quote_id: int) -> MergePreviewRead:
    """待确认变更清单（只读；MERGE_REVIEW 之外的状态 409）。"""
    quote = await db.get(Quote, quote_id)
    if quote is None:
        raise NotFoundError(code="QUOTE_NOT_FOUND", message="报价不存在或已被删除")
    if quote.status != QuoteStatus.MERGE_REVIEW:
        raise QuoteStateError(message="该报价当前没有待确认的合并变更")
    changes = (
        (
            await db.execute(
                select(MergeChange)
                .where(MergeChange.quote_id == quote_id)
                .order_by(MergeChange.id.asc())
            )
        )
        .scalars()
        .all()
    )
    current = await load_current_snapshot(db, quote)
    reads = [
        MergeChangeRead(
            id=change.id,
            entity_type=change.entity_type,
            entity_key=change.entity_key,
            entity_label=_entity_label(change),
            field_name=change.field_name,
            kind=change.kind,
            old_value=change.old_value,
            new_value=change.new_value,
            source_file_id=_change_source(change.new_value)[0],
            source_page=_change_source(change.new_value)[1],
            source_text=_change_source(change.new_value)[2],
            user_edited=_user_edited(current, change),
            resolution=change.resolution,
            # 用户编辑项默认保留旧值；其余默认采纳新值（前端预选，可改）
            default_resolution="KEEP" if _user_edited(current, change) else "ACCEPT",
        )
        for change in changes
    ]
    return MergePreviewRead(
        quote_id=quote.id,
        quote_status=quote.status,
        task_id=changes[0].parse_task_id if changes else 0,
        changes=reads,
        pending_count=sum(1 for change in changes if change.resolution == MergeResolution.PENDING),
    )


def _entity_label(change: MergeChange) -> str:
    if change.entity_type == ENTITY_SCALAR:
        return SCALAR_LABELS.get(change.entity_key, change.entity_key)
    if change.entity_type == ENTITY_PACKAGE:
        # 包名即业务键的清洗形态；直接展示键（生成时 label 已随行校验）
        return change.entity_key
    return change.entity_key


# ---- 解决 ----


async def resolve_merge(
    db: AsyncSession, quote_id: int, payload: MergeResolveRequest, tolerance: Decimal
) -> Quote:
    """逐项 ACCEPT/KEEP；全部解决后原子合并并回到 CONFIRMED（SPEC §2.9）。

    未覆盖全部 PENDING 变更、引用不存在的变更或状态不对都以语义化错误
    停止；应用过程任何异常整体回滚。
    """
    quote = await db.get(Quote, quote_id)
    if quote is None:
        raise NotFoundError(code="QUOTE_NOT_FOUND", message="报价不存在或已被删除")
    if quote.status != QuoteStatus.MERGE_REVIEW:
        raise QuoteStateError(message="该报价当前没有待确认的合并变更")

    pending = (
        (
            await db.execute(
                select(MergeChange)
                .where(MergeChange.quote_id == quote_id, MergeChange.resolution == MergeResolution.PENDING)
                .order_by(MergeChange.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if not pending:
        raise ConflictError(code="NO_PENDING_MERGE_CHANGES", message="该报价没有待确认的合并变更")

    provided = {item.change_id: item.resolution for item in payload.resolutions}
    by_id = {change.id: change for change in pending}
    missing = [change.id for change in pending if change.id not in provided]
    if missing:
        raise ValidationError(
            code="MERGE_CHANGES_UNRESOLVED",
            message=f"还有 {len(missing)} 项变更未裁决，请逐项选择“采纳新值”或“保留旧值”",
        )
    unknown = [change_id for change_id in provided if change_id not in by_id]
    if unknown:
        raise ValidationError(
            code="MERGE_CHANGE_NOT_FOUND", message="合并变更不存在或已被处理，请刷新后重试"
        )

    try:
        for change in pending:
            resolution = provided[change.id]
            # 裁决结果落库保留（审计）；KEEP 不改旧值，ACCEPT 合入新值
            change.resolution = MergeResolution(resolution)
            if resolution == MergeResolution.ACCEPT:
                await _apply_change(db, quote, change)
        quote.status = QuoteStatus.CONFIRMED
        await recalculate_quote_prices(db, quote, tolerance)
        await db.commit()
    except Exception:
        # 原子合并：任何中途失败回滚，报价保持 MERGE_REVIEW 与完整变更集
        await db.rollback()
        raise
    return quote


async def _apply_change(db: AsyncSession, quote: Quote, change: MergeChange) -> None:
    """把单条 ACCEPT 的变更合入报价（调用方事务内执行）。"""
    if change.entity_type == ENTITY_SCALAR:
        await _apply_scalar_change(db, quote, change.field_name, change.new_value)
        return
    if change.entity_type in (ENTITY_COVERAGE, ENTITY_UNRECOGNIZED):
        await _apply_row_change(
            db, quote, change, ENTITY_COVERAGE if change.entity_type == ENTITY_COVERAGE else None
        )
        return
    if change.entity_type == ENTITY_SERVICE:
        await _apply_service_change(db, quote, change)
        return
    if change.entity_type == ENTITY_PACKAGE:
        await _apply_package_change(db, quote, change)
        return
    raise ValidationError(code="MERGE_ENTITY_UNKNOWN", message="未知的合并变更实体类型")


async def _apply_scalar_change(
    db: AsyncSession, quote: Quote, field_name: str, new_value: Any
) -> None:
    """标量 ACCEPT：写入值/状态并把证据置为“用户已确认”。"""
    if field_name in _PRICE_SCALAR_ATTRS:
        value_attr, status_attr, status_enum = _PRICE_SCALAR_ATTRS[field_name]
        value = new_value.get("value") if isinstance(new_value, dict) else None
        setattr(quote, value_attr, _dec(value))
        setattr(quote, status_attr, status_enum(new_value.get("status") or "UNKNOWN"))
        await touch_scalar_evidence(
            db, quote.id, field_name, None if value is None else str(value)
        )
        return
    if field_name in _VEHICLE_SCALAR_ATTRS:
        value = new_value.get("value") if isinstance(new_value, dict) else None
        if field_name == "vehicleSeats" and value is not None:
            value = int(value)
        if field_name == "isNev" and value is not None:
            value = bool(value)
        setattr(quote, _VEHICLE_SCALAR_ATTRS[field_name], value)
        await touch_scalar_evidence(
            db, quote.id, field_name, None if value is None else str(value)
        )
        return
    raise ValidationError(code="MERGE_FIELD_UNKNOWN", message="未知的合并变更字段")


def _coverage_row_from_dict(quote_id: int, data: dict) -> QuoteCoverage:
    """从快照重建险种行（用户裁决采纳 → 编辑保护 + HIGH）。"""
    return QuoteCoverage(
        quote_id=quote_id,
        code=data.get("code"),
        category=(
            CoverageCategory(data["category"])
            if data.get("category")
            else CoverageCategory.UNRECOGNIZED
        ),
        raw_name=data.get("rawName") or "",
        raw_value=data.get("rawValue"),
        name=data.get("name") or data.get("rawName") or "",
        status=ItemStatus(data.get("status") or ItemStatus.UNKNOWN.value),
        coverage_amount=_dec(data.get("coverageAmount")),
        per_seat_amount=_dec(data.get("perSeatAmount")),
        seat_count=data.get("seatCount"),
        shared_coverage=data.get("sharedCoverage"),
        premium=_dec(data.get("premium")),
        multiplier=_dec(data.get("multiplier")),
        condition=data.get("condition"),
        description=data.get("description"),
        source_file_id=data.get("sourceFileId"),
        source_page=data.get("sourcePage"),
        source_text=data.get("sourceText"),
        confidence_level=ConfidenceLevel.HIGH,
        edited_by_user=True,
    )


def _service_row_from_dict(quote_id: int, data: dict) -> QuoteService:
    return QuoteService(
        quote_id=quote_id,
        service_type=ServiceType(data.get("serviceType") or ServiceType.OTHER.value),
        status=ItemStatus(data.get("status") or ItemStatus.UNKNOWN.value),
        count=data.get("count"),
        cost=_dec(data.get("cost")),
        description=data.get("description"),
        raw_name=data.get("rawName"),
        raw_value=data.get("rawValue"),
        source_file_id=data.get("sourceFileId"),
        source_page=data.get("sourcePage"),
        source_text=data.get("sourceText"),
        confidence_level=ConfidenceLevel.HIGH,
        edited_by_user=True,
    )


def _package_from_dict(quote_id: int, data: dict) -> SupplementalPackage:
    package = SupplementalPackage(
        quote_id=quote_id,
        name=data.get("name") or "",
        provider=data.get("provider"),
        raw_name=data.get("rawName"),
        raw_value=data.get("rawValue"),
        premium=_dec(data.get("premium")),
        description=data.get("description"),
        source_file_id=data.get("sourceFileId"),
        source_page=data.get("sourcePage"),
        source_text=data.get("sourceText"),
        confidence_level=ConfidenceLevel.HIGH,
        edited_by_user=True,
    )
    for item in data.get("coverages", []):
        package.coverages.append(
            PackageCoverage(
                type=item.get("type") or "OTHER",
                name=item.get("name"),
                status=ItemStatus(item.get("status") or ItemStatus.UNKNOWN.value),
                coverage_amount=_dec(item.get("coverageAmount")),
                unit=PackageUnit(item["unit"]) if item.get("unit") else None,
                per_seat_amount=_dec(item.get("perSeatAmount")),
                seat_count=item.get("seatCount"),
                shared=item.get("shared"),
                multiplier=_dec(item.get("multiplier")),
                condition=item.get("condition"),
                description=item.get("description"),
                raw_text=item.get("rawText"),
                source_file_id=item.get("sourceFileId"),
                source_page=item.get("sourcePage"),
                source_text=item.get("sourceText"),
                confidence_level=ConfidenceLevel.HIGH,
                edited_by_user=True,
            )
        )
    return package


async def _load_old_coverages(db: AsyncSession, quote_id: int, *, code: str | None, key: str):
    """按业务键加载旧险种行（未识别键用清洗后 rawName 匹配，Python 侧过滤）。"""
    rows = (
        await db.execute(
            select(QuoteCoverage).where(QuoteCoverage.quote_id == quote_id)
        )
    ).scalars().all()
    if code is not None:
        return [row for row in rows if row.code == key]
    return [
        row for row in rows if row.code is None and clean_name(row.raw_name) == key
    ]


async def _apply_row_change(
    db: AsyncSession, quote: Quote, change: MergeChange, code: str | None
) -> None:
    """险种/未识别项 ACCEPT：整行插入、整组替换或字段级更新。"""
    key = change.entity_key
    old_rows = await _load_old_coverages(db, quote.id, code=code, key=key)
    if change.kind == MergeChangeKind.ADD:
        # ADD 无旧行；防御性校验避免重复插入
        if old_rows:
            raise ConflictError(
                code="MERGE_CONFLICT_STALE", message="报价数据已变化，请刷新合并预览后重试"
            )
        if change.field_name == FIELD_ROWS:
            for data in change.new_value["rows"]:
                db.add(_coverage_row_from_dict(quote.id, data))
        else:
            db.add(_coverage_row_from_dict(quote.id, change.new_value))
        return
    if change.field_name == FIELD_ROWS:
        # 整组替换：删除旧组再插入新组（用户裁决不逐行猜测）
        for row in old_rows:
            await db.delete(row)
        for data in change.new_value["rows"]:
            db.add(_coverage_row_from_dict(quote.id, data))
        return
    # 字段级更新：单行语义（生成端保证）；行缺失说明数据已漂移
    if len(old_rows) != 1:
        raise ConflictError(
            code="MERGE_CONFLICT_STALE", message="报价数据已变化，请刷新合并预览后重试"
        )
    _apply_coverage_fields(old_rows[0], change.field_name, change.new_value)


def _apply_coverage_fields(row: QuoteCoverage, field_name: str, new_value: Any) -> None:
    """把字段级新值写入旧险种行，并同步更新该行来源定位。"""
    if field_name not in _COVERAGE_FIELD_ATTRS:
        raise ValidationError(code="MERGE_FIELD_UNKNOWN", message="未知的合并变更字段")
    attr, kind = _COVERAGE_FIELD_ATTRS[field_name]
    value = new_value.get("value") if isinstance(new_value, dict) else new_value
    setattr(row, attr, _coerce(value, kind))
    if isinstance(new_value, dict):
        row.source_file_id = new_value.get("sourceFileId")
        row.source_page = new_value.get("sourcePage")
        row.source_text = new_value.get("sourceText")
    # 用户裁决采纳的字段按“用户已确认”口径保护
    row.edited_by_user = True
    row.confidence_level = ConfidenceLevel.HIGH


async def _apply_service_change(db: AsyncSession, quote: Quote, change: MergeChange) -> None:
    key = ServiceType(change.entity_key)
    old_rows = (
        (
            await db.execute(
                select(QuoteService).where(
                    QuoteService.quote_id == quote.id, QuoteService.service_type == key
                )
            )
        )
        .scalars()
        .all()
    )
    if change.kind == MergeChangeKind.ADD:
        if old_rows:
            raise ConflictError(
                code="MERGE_CONFLICT_STALE", message="报价数据已变化，请刷新合并预览后重试"
            )
        if change.field_name == FIELD_ROWS:
            for data in change.new_value["rows"]:
                db.add(_service_row_from_dict(quote.id, data))
        else:
            db.add(_service_row_from_dict(quote.id, change.new_value))
        return
    if change.field_name == FIELD_ROWS:
        for row in old_rows:
            await db.delete(row)
        for data in change.new_value["rows"]:
            db.add(_service_row_from_dict(quote.id, data))
        return
    if len(old_rows) != 1:
        raise ConflictError(
            code="MERGE_CONFLICT_STALE", message="报价数据已变化，请刷新合并预览后重试"
        )
    row = old_rows[0]
    if change.field_name not in _SERVICE_FIELD_ATTRS:
        raise ValidationError(code="MERGE_FIELD_UNKNOWN", message="未知的合并变更字段")
    attr, kind = _SERVICE_FIELD_ATTRS[change.field_name]
    value = change.new_value.get("value") if isinstance(change.new_value, dict) else None
    setattr(row, attr, _coerce(value, kind))
    if isinstance(change.new_value, dict):
        row.source_file_id = change.new_value.get("sourceFileId")
        row.source_page = change.new_value.get("sourcePage")
        row.source_text = change.new_value.get("sourceText")
    row.edited_by_user = True
    row.confidence_level = ConfidenceLevel.HIGH


async def _apply_package_change(db: AsyncSession, quote: Quote, change: MergeChange) -> None:
    """保障包 ACCEPT：整包插入、整组替换、内部保障替换或包字段更新。"""
    all_packages = (
        (
            await db.execute(
                select(SupplementalPackage).where(SupplementalPackage.quote_id == quote.id)
            )
        )
        .scalars()
        .all()
    )
    old_packages = [
        package for package in all_packages if clean_name(package.name) == change.entity_key
    ]
    if change.kind == MergeChangeKind.ADD:
        if old_packages:
            raise ConflictError(
                code="MERGE_CONFLICT_STALE", message="报价数据已变化，请刷新合并预览后重试"
            )
        if change.field_name == FIELD_ROWS:
            for data in change.new_value["rows"]:
                db.add(_package_from_dict(quote.id, data))
        else:
            db.add(_package_from_dict(quote.id, change.new_value))
        return
    if change.field_name == FIELD_ROWS:
        for package in old_packages:
            await db.delete(package)
        for data in change.new_value["rows"]:
            db.add(_package_from_dict(quote.id, data))
        return
    if len(old_packages) != 1:
        raise ConflictError(
            code="MERGE_CONFLICT_STALE", message="报价数据已变化，请刷新合并预览后重试"
        )
    package = old_packages[0]
    if change.field_name == FIELD_PACKAGE:
        # 内部保障整组替换：包级字段不动，内部行删除重建
        await db.execute(delete(PackageCoverage).where(PackageCoverage.package_id == package.id))
        for item in change.new_value["coverages"]:
            payload = dict(item)
            payload.setdefault("name", None)
            db.add(
                _package_coverage_from_dict(package.id, payload)
            )
        package.edited_by_user = True
        package.confidence_level = ConfidenceLevel.HIGH
        return
    if change.field_name == "premium":
        package.premium = _dec(change.new_value.get("value") if isinstance(change.new_value, dict) else None)
    elif change.field_name == "description":
        package.description = (
            change.new_value.get("value") if isinstance(change.new_value, dict) else None
        )
    else:
        raise ValidationError(code="MERGE_FIELD_UNKNOWN", message="未知的合并变更字段")
    if isinstance(change.new_value, dict):
        package.source_file_id = change.new_value.get("sourceFileId")
        package.source_page = change.new_value.get("sourcePage")
        package.source_text = change.new_value.get("sourceText")
    package.edited_by_user = True
    package.confidence_level = ConfidenceLevel.HIGH


def _package_coverage_from_dict(package_id: int, data: dict) -> PackageCoverage:
    return PackageCoverage(
        package_id=package_id,
        type=data.get("type") or "OTHER",
        name=data.get("name"),
        status=ItemStatus(data.get("status") or ItemStatus.UNKNOWN.value),
        coverage_amount=_dec(data.get("coverageAmount")),
        unit=PackageUnit(data["unit"]) if data.get("unit") else None,
        per_seat_amount=_dec(data.get("perSeatAmount")),
        seat_count=data.get("seatCount"),
        shared=data.get("shared"),
        multiplier=_dec(data.get("multiplier")),
        condition=data.get("condition"),
        description=data.get("description"),
        raw_text=data.get("rawText"),
        source_file_id=data.get("sourceFileId"),
        source_page=data.get("sourcePage"),
        source_text=data.get("sourceText"),
        confidence_level=ConfidenceLevel.HIGH,
        edited_by_user=True,
    )
