"""已确认报价的补传合并写入器（SPEC §2.9；TASK-05 范围 5-6）。

职责与不变量：
- 任务成功且目标报价处于 CONFIRMED/MERGE_REVIEW 时进入本模块：解析结果
  不直接写业务表，只生成 ADD/CONFLICT 待确认变更集（merge_change），并把
  报价置为 MERGE_REVIEW；用户逐项 ACCEPT/KEEP 后才合入（merge_service）；
- 已确认数据永不静默覆盖：不自动生成 DELETE（新解析缺少的旧实体保持
  不动），用户编辑过的内容只展示变更且默认 KEEP；
- 稳定业务键（SPEC §2.9）：险种用标准 code（未识别项用清洗后 rawName）、
  服务用 serviceType、保障包用名称、标量用字段名；同键多行整组标冲突，
  不猜测逐行合并；
- 信息不足不制造冲突：新解析结果为 null/UNKNOWN 的标量不产生变更，
  防止“模型没读到”把旧值抹掉；
- 变更生成、遗留 PENDING 变更清理与状态迁移都在调用方的同一事务内，
  中途失败整体回滚，绝不形成半合并状态。

隐私边界：候选数据来自脱敏后的流水线构建函数；oldValue/newValue 快照
中的文本均已脱敏；本模块错误文案为固定中文提示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.privacy import sanitize_text
from app.models import (
    FieldEvidence,
    MergeChange,
    PackageCoverage,
    Quote,
    QuoteCoverage,
    QuoteService,
    SupplementalPackage,
)
from app.models.enums import (
    MergeChangeKind,
    MergeResolution,
    OfficialTotalStatus,
    PriceItemStatus,
    QuoteStatus,
)
from app.services.dictionaries import STATUS_LABELS
from app.services.normalization.engine import clean_name, match_insurer
from app.services.parser.candidate_writer import (
    _PRICE_MERGES,
    _VEHICLE_MERGES,
    EvidenceResolver,
    _build_coverage_rows,
    _build_packages,
    _build_service_rows,
    _normalize_first_reg_date,
)
from app.services.parser.extraction_schema import ExtractionResult, PlanExtraction
from app.services.parser.pipeline import ParseTaskFailure

# ---- 稳定业务键与字段集合 ----

# 实体类型标识（merge_change.entity_type；前端按类型分组渲染）
ENTITY_SCALAR = "scalar"
ENTITY_COVERAGE = "coverage"
ENTITY_UNRECOGNIZED = "unrecognized"
ENTITY_SERVICE = "service"
ENTITY_PACKAGE = "package"

# 字段级冲突使用的特殊 fieldName（约定值，前端据此切换整行/整组渲染）
FIELD_ROW = "__row__"
FIELD_ROWS = "__rows__"
FIELD_PACKAGE = "__package__"

# 险种/服务行参与字段级比较的语义字段（来源/置信度不参与，避免证据文本
# 措辞差异制造噪声冲突）
COVERAGE_COMPARE_FIELDS = (
    "status",
    "coverageAmount",
    "perSeatAmount",
    "seatCount",
    "sharedCoverage",
    "premium",
    "multiplier",
    "condition",
    "description",
)
SERVICE_COMPARE_FIELDS = ("status", "count", "cost", "description")

# 标量字段中文展示名（merge-preview 的 entityLabel）
SCALAR_LABELS = {
    "commercialPremium": "商业险",
    "compulsoryPremium": "交强险",
    "vehicleTax": "车船税",
    "packageTotal": "独立保障包",
    "otherFees": "其他费用",
    "officialTotal": "官方总价",
    "vehicleModel": "车型",
    "vehicleSeats": "座位数",
    "firstRegDate": "初登日期",
    "isNev": "新能源",
}

_MULTI_PLAN_MERGE_ERROR = (
    "本次解析识别到多个方案；已确认报价的补传合并只支持单方案文件，"
    "请按方案分别上传，或把多方案文件上传到新报价后使用拆分功能"
)

_INSURER_MISMATCH_ERROR = (
    "补传文件识别到的保险公司与当前报价不一致；"
    "如需对比其他公司的报价，请返回项目新建报价后上传"
)


def _f(value: Decimal | None) -> float | None:
    """Decimal → JSON 安全的 float（金额两位小数，float 精度足够）。"""
    return None if value is None else float(value)


# ---- 行/包快照序列化（新旧两侧共用同一结构，ACCEPT 时可完整重建）----


def coverage_dict(row: QuoteCoverage) -> dict:
    """险种行快照（含来源与置信度）。"""
    return {
        "code": row.code,
        "category": row.category.value if row.category else None,
        "rawName": row.raw_name,
        "rawValue": row.raw_value,
        "name": row.name,
        "status": row.status.value,
        "coverageAmount": _f(row.coverage_amount),
        "perSeatAmount": _f(row.per_seat_amount),
        "seatCount": row.seat_count,
        "sharedCoverage": row.shared_coverage,
        "premium": _f(row.premium),
        "multiplier": _f(row.multiplier),
        "condition": row.condition,
        "description": row.description,
        "sourceFileId": row.source_file_id,
        "sourcePage": row.source_page,
        "sourceText": row.source_text,
        "confidenceLevel": row.confidence_level.value,
        "editedByUser": row.edited_by_user,
    }


def service_dict(row: QuoteService) -> dict:
    """增值服务行快照。"""
    return {
        "serviceType": row.service_type.value,
        "status": row.status.value,
        "count": row.count,
        "cost": _f(row.cost),
        "description": row.description,
        "rawName": row.raw_name,
        "rawValue": row.raw_value,
        "sourceFileId": row.source_file_id,
        "sourcePage": row.source_page,
        "sourceText": row.source_text,
        "confidenceLevel": row.confidence_level.value,
        "editedByUser": row.edited_by_user,
    }


def package_coverage_dict(item: PackageCoverage) -> dict:
    """保障包内部保障行快照。"""
    return {
        "type": item.type,
        "name": item.name,
        "status": item.status.value,
        "coverageAmount": _f(item.coverage_amount),
        "unit": item.unit.value if item.unit else None,
        "perSeatAmount": _f(item.per_seat_amount),
        "seatCount": item.seat_count,
        "shared": item.shared,
        "multiplier": _f(item.multiplier),
        "condition": item.condition,
        "description": item.description,
        "rawText": item.raw_text,
        "sourceFileId": item.source_file_id,
        "sourcePage": item.source_page,
        "sourceText": item.source_text,
        "confidenceLevel": item.confidence_level.value,
        "editedByUser": item.edited_by_user,
    }


def package_dict(package: SupplementalPackage, coverage_rows: list[PackageCoverage]) -> dict:
    """保障包快照（内部保障行按 id 顺序随包序列化）。"""
    return {
        "name": package.name,
        "provider": package.provider,
        "rawName": package.raw_name,
        "rawValue": package.raw_value,
        "premium": _f(package.premium),
        "description": package.description,
        "sourceFileId": package.source_file_id,
        "sourcePage": package.source_page,
        "sourceText": package.source_text,
        "confidenceLevel": package.confidence_level.value,
        "editedByUser": package.edited_by_user,
        "coverages": [package_coverage_dict(item) for item in coverage_rows],
    }


def coverage_entity(row_dict: dict) -> tuple[str, str]:
    """险种行的实体类型与业务键：有码走 coverage+code，无码走
    unrecognized+清洗后 rawName（SPEC §2.9 稳定业务键）。"""
    if row_dict["code"]:
        return ENTITY_COVERAGE, row_dict["code"]
    return ENTITY_UNRECOGNIZED, clean_name(row_dict["rawName"])


def _canonical_rows(rows: list[dict]) -> list[tuple]:
    """行组的稳定比较键：语义字段排序后比较，来源/置信度不参与。

    用 repr 作排序键：字段值可能混合 None 与字符串，直接排序会类型报错。
    """
    return sorted(
        (
            (
                (row.get("rawName"), row.get("rawValue")),
                tuple(row.get(name) for name in COVERAGE_COMPARE_FIELDS),
            )
            for row in rows
        ),
        key=repr,
    )


def _canonical_package_items(items: list[dict]) -> list[tuple]:
    """保障包内部行的稳定比较键。"""
    return sorted(
        (
            (
                item.get("type"),
                item.get("name"),
                item.get("rawText"),
                tuple(item.get(name) for name in COVERAGE_COMPARE_FIELDS),
            )
            for item in items
        ),
        key=repr,
    )


# ---- 旧值侧快照 ----


@dataclass(slots=True)
class CurrentSnapshot:
    """报价当前值的分组快照（diff 与 userEdited 判定的唯一数据源）。"""

    quote: Quote
    # 实体键 → 行快照列表（按 DB id 顺序，展示稳定）
    coverage_groups: dict[tuple[str, str], list[dict]] = field(default_factory=dict)
    service_groups: dict[tuple[str, str], list[dict]] = field(default_factory=dict)
    package_groups: dict[tuple[str, str], list[dict]] = field(default_factory=dict)
    # 标量字段名 → field_evidence.editedByUser（用户编辑判定）
    scalar_edited: dict[str, bool] = field(default_factory=dict)


async def load_current_snapshot(db: AsyncSession, quote: Quote) -> CurrentSnapshot:
    """把报价当前全部可比较实体装载为分组快照（读操作，不落库）。"""
    coverages = (
        await db.execute(
            select(QuoteCoverage)
            .where(QuoteCoverage.quote_id == quote.id)
            .order_by(QuoteCoverage.id.asc())
        )
    ).scalars().all()
    services = (
        await db.execute(
            select(QuoteService)
            .where(QuoteService.quote_id == quote.id)
            .order_by(QuoteService.id.asc())
        )
    ).scalars().all()
    packages = (
        await db.execute(
            select(SupplementalPackage)
            .where(SupplementalPackage.quote_id == quote.id)
            .order_by(SupplementalPackage.id.asc())
        )
    ).scalars().all()
    evidences = (
        await db.execute(select(FieldEvidence).where(FieldEvidence.quote_id == quote.id))
    ).scalars().all()

    snapshot = CurrentSnapshot(quote=quote)
    for row in coverages:
        row_dict = coverage_dict(row)
        snapshot.coverage_groups.setdefault(coverage_entity(row_dict), []).append(row_dict)
    for row in services:
        row_dict = service_dict(row)
        snapshot.service_groups.setdefault((ENTITY_SERVICE, row.service_type.value), []).append(
            row_dict
        )
    if packages:
        package_rows = (
            await db.execute(
                select(PackageCoverage)
                .where(PackageCoverage.package_id.in_([p.id for p in packages]))
                .order_by(PackageCoverage.id.asc())
            )
        ).scalars().all()
        rows_by_package: dict[int, list[PackageCoverage]] = {}
        for item in package_rows:
            rows_by_package.setdefault(item.package_id, []).append(item)
        for package in packages:
            view = package_dict(package, rows_by_package.get(package.id, []))
            snapshot.package_groups.setdefault(
                (ENTITY_PACKAGE, clean_name(package.name)), []
            ).append(view)
    snapshot.scalar_edited = {
        evidence.field_name: evidence.edited_by_user for evidence in evidences
    }
    return snapshot


# ---- 新值侧候选快照（全部在内存中，不触库）----


@dataclass(slots=True)
class CandidateSnapshot:
    """一次成功解析的候选值。scalars 值为 (值, 状态, 来源三元组)。"""

    coverage_rows: list[dict]
    service_rows: list[dict]
    package_rows: list[dict]
    scalars: dict[str, tuple[object, str | None, tuple[int | None, int | None, str | None]]]


def _source_of_resolved(resolved) -> tuple[int | None, int | None, str | None]:  # noqa: ANN001
    """合法证据才建立来源链接（非法绝不伪造，同 EvidenceResolver 语义）。"""
    if resolved.state != "ok":
        return (None, None, None)
    return (resolved.file_id, resolved.page, resolved.text)


def _source_of_row(row_dict: dict) -> tuple[int | None, int | None, str | None]:
    """从行快照中提取来源三元组（行快照统一携带 source* 键）。"""
    return (
        row_dict.get("sourceFileId"),
        row_dict.get("sourcePage"),
        row_dict.get("sourceText"),
    )


def build_candidate_snapshot(
    quote_id: int,
    plan: PlanExtraction,
    extraction: ExtractionResult,
    resolver: EvidenceResolver,
) -> CandidateSnapshot:
    """复用候选构建纯函数生成“新值侧”快照（不写库、不改报价状态）。

    构建口径与 candidate_writer.apply_single_plan 完全一致：同一套归一化、
    去重与置信度规则，保证“拆分/单方案写入”与“合并候选”零漂移。
    """
    coverage_orm, _compulsory_ev, _tax_ev = _build_coverage_rows(
        quote_id, plan, resolver, extraction.vehicle.isNev.value
    )
    service_orm = _build_service_rows(quote_id, plan, resolver)
    package_orm = _build_packages(quote_id, plan, resolver)

    scalars: dict[
        str, tuple[object, str | None, tuple[int | None, int | None, str | None]]
    ] = {}
    # 价格分项：值⟺INCLUDED 口径与 candidate_writer 一致
    for field_name, _value_attr, _status_attr in _PRICE_MERGES:
        item = getattr(plan.pricing, field_name)
        value = _f(item.value) if item.value is not None else None
        if value is not None:
            status = PriceItemStatus.INCLUDED.value
        elif item.status == "NOT_INCLUDED":
            status = PriceItemStatus.NOT_INCLUDED.value
        else:
            status = PriceItemStatus.UNKNOWN.value
        scalars[field_name] = (
            value,
            status,
            _source_of_resolved(resolver.resolve(item.evidence)),
        )
    official = plan.pricing.officialTotal
    official_value = _f(official.value) if official.value is not None else None
    scalars["officialTotal"] = (
        official_value,
        OfficialTotalStatus.INCLUDED.value
        if official_value is not None
        else OfficialTotalStatus.UNKNOWN.value,
        _source_of_resolved(resolver.resolve(official.evidence)),
    )
    # 车辆快照：与 candidate_writer 相同的脱敏/归一口径
    vehicle = extraction.vehicle
    vehicle_values: dict[str, object] = {
        "vehicleModel": (
            sanitize_text(vehicle.model.value.strip()) if vehicle.model.value else None
        ),
        "vehicleSeats": vehicle.seatCount.value,
        "firstRegDate": _normalize_first_reg_date(vehicle.firstRegDate.value),
        "isNev": vehicle.isNev.value,
    }
    for field_name, _attr, _model_field in _VEHICLE_MERGES:
        field_obj = getattr(vehicle, _model_field)
        scalars[field_name] = (
            vehicle_values[field_name],
            None,
            _source_of_resolved(resolver.resolve(field_obj.evidence)),
        )
    return CandidateSnapshot(
        coverage_rows=[coverage_dict(row) for row in coverage_orm],
        service_rows=[service_dict(row) for row in service_orm],
        package_rows=[package_dict(package, list(package.coverages)) for package in package_orm],
        scalars=scalars,
    )


# ---- diff：生成变更载荷 ----


@dataclass(slots=True)
class MergeChangePayload:
    """一条待写库的变更（先以载荷存在，由调用方统一转 ORM）。"""

    entity_type: str
    entity_key: str
    entity_label: str
    field_name: str
    kind: MergeChangeKind
    old_value: dict | float | int | bool | str | None
    new_value: dict | float | int | bool | str | None


def _with_source(value: object, source: tuple[int | None, int | None, str | None]) -> dict:
    """字段级新值统一携带证据定位，供预览展示“来源”与合入后更新来源。"""
    return {
        "value": value,
        "sourceFileId": source[0],
        "sourcePage": source[1],
        "sourceText": source[2],
    }


def _diff_row_group(
    *,
    old_groups: dict[tuple[str, str], list[dict]],
    new_items: list[tuple[tuple[str, str], dict]],
    label_of,  # noqa: ANN001 - (dict) -> str 的展示名函数
    compare_fields: tuple[str, ...],
    canonical_of,  # noqa: ANN001 - (list[dict]) -> list[tuple] 的稳定比较键函数
) -> list[MergeChangePayload]:
    """行级实体（险种/未识别/服务）的通用 diff。

    - 新键 → ADD（同键多行的新组整组 ADD，fieldName=__rows__）；
    - 双方同键但任一侧多行 → 内容不同才整组 CONFLICT（__rows__），
      不猜测逐行配对（SPEC §2.9）；
    - 双方单行 → 语义字段逐字段 CONFLICT；
    - 旧键在新解析中缺失 → 不自动生成 DELETE（旧数据保持不动）。
    """
    new_groups: dict[tuple[str, str], list[dict]] = {}
    for key, row_dict in new_items:
        new_groups.setdefault(key, []).append(row_dict)

    payloads: list[MergeChangePayload] = []
    for key, new_rows in new_groups.items():
        entity_type, entity_key = key
        label = label_of(new_rows[0])
        old_rows = old_groups.get(key, [])
        if not old_rows:
            if len(new_rows) > 1:
                payloads.append(
                    MergeChangePayload(
                        entity_type=entity_type,
                        entity_key=entity_key,
                        entity_label=label,
                        field_name=FIELD_ROWS,
                        kind=MergeChangeKind.ADD,
                        old_value=None,
                        new_value={"rows": new_rows},
                    )
                )
            else:
                payloads.append(
                    MergeChangePayload(
                        entity_type=entity_type,
                        entity_key=entity_key,
                        entity_label=label,
                        field_name=FIELD_ROW,
                        kind=MergeChangeKind.ADD,
                        old_value=None,
                        new_value=new_rows[0],
                    )
                )
            continue
        if len(old_rows) > 1 or len(new_rows) > 1:
            if canonical_of(old_rows) != canonical_of(new_rows):
                payloads.append(
                    MergeChangePayload(
                        entity_type=entity_type,
                        entity_key=entity_key,
                        entity_label=label,
                        field_name=FIELD_ROWS,
                        kind=MergeChangeKind.CONFLICT,
                        old_value={"rows": old_rows},
                        new_value={"rows": new_rows},
                    )
                )
            continue
        old_row, new_row = old_rows[0], new_rows[0]
        source = _source_of_row(new_row)
        for field_name in compare_fields:
            if old_row.get(field_name) != new_row.get(field_name):
                payloads.append(
                    MergeChangePayload(
                        entity_type=entity_type,
                        entity_key=entity_key,
                        entity_label=label,
                        field_name=field_name,
                        kind=MergeChangeKind.CONFLICT,
                        old_value=old_row.get(field_name),
                        new_value=_with_source(new_row.get(field_name), source),
                    )
                )
    return payloads


def _diff_packages(
    old_groups: dict[tuple[str, str], list[dict]],
    new_groups: dict[tuple[str, str], list[dict]],
) -> list[MergeChangePayload]:
    """保障包 diff：premium/description 字段级；内部保障内容整组比较。"""
    payloads: list[MergeChangePayload] = []

    def _package_canonical(rows: list[dict]) -> list[tuple]:
        return sorted(
            (
                (
                    row.get("premium"),
                    row.get("description"),
                    _canonical_package_items(row["coverages"]),
                )
                for row in rows
            ),
            key=repr,
        )

    for key, new_packages in new_groups.items():
        _entity_type, entity_key = key
        old_packages = old_groups.get(key, [])
        if not old_packages:
            if len(new_packages) > 1:
                payloads.append(
                    MergeChangePayload(
                        entity_type=ENTITY_PACKAGE,
                        entity_key=entity_key,
                        entity_label=new_packages[0]["name"],
                        field_name=FIELD_ROWS,
                        kind=MergeChangeKind.ADD,
                        old_value=None,
                        new_value={"rows": new_packages},
                    )
                )
            else:
                payloads.append(
                    MergeChangePayload(
                        entity_type=ENTITY_PACKAGE,
                        entity_key=entity_key,
                        entity_label=new_packages[0]["name"],
                        field_name=FIELD_ROW,
                        kind=MergeChangeKind.ADD,
                        old_value=None,
                        new_value=new_packages[0],
                    )
                )
            continue
        if len(old_packages) > 1 or len(new_packages) > 1:
            if _package_canonical(old_packages) != _package_canonical(new_packages):
                payloads.append(
                    MergeChangePayload(
                        entity_type=ENTITY_PACKAGE,
                        entity_key=entity_key,
                        entity_label=new_packages[0]["name"],
                        field_name=FIELD_ROWS,
                        kind=MergeChangeKind.CONFLICT,
                        old_value={"rows": old_packages},
                        new_value={"rows": new_packages},
                    )
                )
            continue
        old_package, new_package = old_packages[0], new_packages[0]
        source = _source_of_row(new_package)
        for field_name in ("premium", "description"):
            if old_package.get(field_name) != new_package.get(field_name):
                payloads.append(
                    MergeChangePayload(
                        entity_type=ENTITY_PACKAGE,
                        entity_key=entity_key,
                        entity_label=new_package["name"],
                        field_name=field_name,
                        kind=MergeChangeKind.CONFLICT,
                        old_value=old_package.get(field_name),
                        new_value=_with_source(new_package.get(field_name), source),
                    )
                )
        if _canonical_package_items(old_package["coverages"]) != _canonical_package_items(
            new_package["coverages"]
        ):
            payloads.append(
                MergeChangePayload(
                    entity_type=ENTITY_PACKAGE,
                    entity_key=entity_key,
                    entity_label=new_package["name"],
                    field_name=FIELD_PACKAGE,
                    kind=MergeChangeKind.CONFLICT,
                    old_value={"coverages": old_package["coverages"]},
                    new_value={"coverages": new_package["coverages"]},
                )
            )
    return payloads


def _diff_scalars(
    current: CurrentSnapshot, candidate: CandidateSnapshot
) -> list[MergeChangePayload]:
    """标量（价格分项/官方总价/车辆快照）diff。

    信息不足保护：新值为 null 且状态未知时跳过——模型没读到不代表旧值
    错误；新值明确 NOT_INCLUDED 且旧值存在时仍生成冲突交用户裁决。
    """
    quote = current.quote
    payloads: list[MergeChangePayload] = []
    scalar_specs = [
        (name, value_attr, status_attr)
        for name, value_attr, status_attr in _PRICE_MERGES
    ] + [("officialTotal", "official_total", "official_total_status")]
    for field_name, value_attr, status_attr in scalar_specs:
        new_value, new_status, source = candidate.scalars[field_name]
        if new_value is None and new_status in (
            PriceItemStatus.UNKNOWN.value,
            OfficialTotalStatus.UNKNOWN.value,
        ):
            continue
        old_value = _f(getattr(quote, value_attr))
        old_status = getattr(quote, status_attr).value
        if old_value == new_value and old_status == new_status:
            continue
        old_is_absent = old_value is None and old_status in (
            PriceItemStatus.UNKNOWN.value,
            OfficialTotalStatus.UNKNOWN.value,
        )
        payloads.append(
            MergeChangePayload(
                entity_type=ENTITY_SCALAR,
                entity_key=field_name,
                entity_label=SCALAR_LABELS[field_name],
                field_name=field_name,
                kind=MergeChangeKind.ADD if old_is_absent else MergeChangeKind.CONFLICT,
                old_value={"value": old_value, "status": old_status},
                new_value={
                    "value": new_value,
                    "status": new_status,
                    "sourceFileId": source[0],
                    "sourcePage": source[1],
                    "sourceText": source[2],
                },
            )
        )
    for field_name, attr, _model_field in _VEHICLE_MERGES:
        new_value, _new_status, source = candidate.scalars[field_name]
        if new_value is None:
            continue
        old_value = getattr(quote, attr)
        if isinstance(old_value, Decimal):
            old_value = _f(old_value)
        if old_value == new_value:
            continue
        payloads.append(
            MergeChangePayload(
                entity_type=ENTITY_SCALAR,
                entity_key=field_name,
                entity_label=SCALAR_LABELS[field_name],
                field_name=field_name,
                kind=MergeChangeKind.ADD if old_value is None else MergeChangeKind.CONFLICT,
                old_value={"value": old_value},
                new_value={
                    "value": new_value,
                    "sourceFileId": source[0],
                    "sourcePage": source[1],
                    "sourceText": source[2],
                },
            )
        )
    return payloads


def build_merge_change_payloads(
    current: CurrentSnapshot, candidate: CandidateSnapshot
) -> list[MergeChangePayload]:
    """新旧快照全量 diff（顺序：标量 → 险种 → 未识别 → 服务 → 保障包）。"""

    def _coverage_label(row_dict: dict) -> str:
        return row_dict.get("name") or row_dict.get("rawName") or ""

    def _service_label(row_dict: dict) -> str:
        return STATUS_LABELS["serviceType"].get(row_dict.get("serviceType"), "增值服务")

    def _package_label(row_dict: dict) -> str:
        return row_dict.get("name") or ""

    return [
        *_diff_scalars(current, candidate),
        *_diff_row_group(
            old_groups=current.coverage_groups,
            new_items=[(coverage_entity(row), row) for row in candidate.coverage_rows],
            label_of=_coverage_label,
            compare_fields=COVERAGE_COMPARE_FIELDS,
            canonical_of=_canonical_rows,
        ),
        *_diff_row_group(
            old_groups=current.service_groups,
            new_items=[
                ((ENTITY_SERVICE, row["serviceType"]), row) for row in candidate.service_rows
            ],
            label_of=_service_label,
            compare_fields=SERVICE_COMPARE_FIELDS,
            canonical_of=_canonical_rows,
        ),
        *_diff_packages(
            old_groups=current.package_groups,
            new_groups=_group_packages(candidate.package_rows),
        ),
    ]


def _group_packages(package_dicts: list[dict]) -> dict[tuple[str, str], list[dict]]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in package_dicts:
        groups.setdefault((ENTITY_PACKAGE, clean_name(row["name"])), []).append(row)
    return groups


# ---- 入口：已确认报价的解析成功分支 ----


def _ensure_insurer_matches(quote: Quote, extraction: ExtractionResult) -> None:
    """补传合并的公司一致性守卫：模型公司与报价公司不同时明确失败。

    用户应通过“新建报价”对比其他公司，而不是把别家单子合并进当前报价。
    """
    name = extraction.insurer.name
    if not name or not name.strip():
        return
    code = match_insurer(name)
    if code is not None:
        conflict = code != quote.insurer_code
    else:
        conflict = clean_name(name) != clean_name(quote.insurer_name)
    if conflict:
        raise ParseTaskFailure(_INSURER_MISMATCH_ERROR)


async def apply_confirmed_extraction(
    db: AsyncSession,
    *,
    task,
    quote: Quote,
    files: list,  # noqa: ANN001 - ParseTaskFileInput 列表
    extraction: ExtractionResult,
) -> None:
    """CONFIRMED/MERGE_REVIEW 报价的解析成功分支（调用方事务内执行）。

    - 同公司 planCount>1：明确失败（已确认报价无法承载多方案归属）；
    - 公司不一致：明确失败（业务保护，见 _ensure_insurer_matches）；
    - 正常路径：diff 生成变更 → 清理遗留 PENDING → 写 merge_change →
      报价进入 MERGE_REVIEW；旧数据全程未被改写。
    """
    if len(extraction.plans) > 1:
        raise ParseTaskFailure(_MULTI_PLAN_MERGE_ERROR)
    _ensure_insurer_matches(quote, extraction)

    plan = extraction.plans[0]
    resolver = EvidenceResolver(files)
    candidate = build_candidate_snapshot(quote.id, plan, extraction, resolver)
    current = await load_current_snapshot(db, quote)
    payloads = build_merge_change_payloads(current, candidate)

    # 与旧值完全一致（无任何 ADD/CONFLICT）：无变更可解决，若进入
    # MERGE_REVIEW 将没有出口（resolve 要求至少一个变更），保持原状态
    if not payloads:
        return

    # 防御性清理：正常流程 MERGE_REVIEW 期间禁止再次解析（409），理论上
    # 不存在遗留 PENDING；此处兜底避免异常路径下新旧变更叠加
    await db.execute(
        delete(MergeChange).where(
            MergeChange.quote_id == quote.id,
            MergeChange.resolution == MergeResolution.PENDING,
        )
    )
    for payload in payloads:
        db.add(
            MergeChange(
                quote_id=quote.id,
                parse_task_id=task.id,
                entity_type=payload.entity_type,
                entity_key=payload.entity_key,
                field_name=payload.field_name,
                old_value=payload.old_value,
                new_value=payload.new_value,
                kind=payload.kind,
            )
        )
    quote.status = QuoteStatus.MERGE_REVIEW
