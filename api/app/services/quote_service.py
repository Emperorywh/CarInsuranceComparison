"""报价领域服务（TASK-02）：容器 CRUD、各层明细增删改、状态守卫与确认。

业务不变量：
- 编辑只允许 PENDING_CONFIRM / CONFIRMED（SPEC §2.10：DRAFT/PARSING/
  PARSE_FAILED 属于上传解析流程，转手动后再编辑）；
- 每个写操作在同一事务内完成校验、脱敏、重算价格与净支出；
- 所有自由文本（公司名、保险员、原始名称、描述、标注、优惠说明等）入库前
  统一经过 app.core.privacy.sanitize_text，路由层不得自行拼正则；
- 用户创建/修改的行一律 editedByUser=true、confidenceLevel=HIGH（SPEC §5.3）；
- 价格分项“值⟺INCLUDED”不变量：值非空必为 INCLUDED，
  NOT_INCLUDED/UNKNOWN 必无值；系统绝不把 null 当 0。
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import (
    QuoteDetailNotFoundError,
    QuoteNotFoundError,
    QuoteStateError,
    ValidationError,
)
from app.core.privacy import sanitize_text
from app.models import (
    ComparisonProject,
    Discount,
    FieldEvidence,
    PackageCoverage,
    Quote,
    QuoteCoverage,
    QuoteService,
    SalesAnnotation,
    SupplementalPackage,
)
from app.models.enums import (
    ConfidenceLevel,
    CoverageCategory,
    OfficialTotalStatus,
    PriceItemStatus,
    QuoteSource,
    QuoteStatus,
)
from app.schemas.file import FileRead
from app.schemas.quote import (
    AnnotationCreate,
    AnnotationRead,
    AnnotationUpdate,
    CoverageCreate,
    CoverageRead,
    CoverageUpdate,
    DiscountCreate,
    DiscountRead,
    DiscountUpdate,
    FieldEvidenceRead,
    PackageCoverageCreate,
    PackageCoverageRead,
    PackageCoverageUpdate,
    PackageCreate,
    PackageRead,
    PackageUpdate,
    QuoteConfirm,
    QuoteCreate,
    QuoteRead,
    QuoteUpdate,
    ServiceCreate,
    ServiceRead,
    ServiceUpdate,
    VehicleConflictInfo,
)
from app.services import parse_service
from app.services.normalization.alias_map import (
    CATEGORY_ADDITIONAL,
    CATEGORY_CORE,
    INSURER_DEFINITIONS,
    PACKAGE_COVERAGE_DEFINITIONS,
    get_coverage_definition,
)
from app.services.normalization.amounts import (
    check_amount_range,
    resolve_seat_amounts,
)
from app.services.pricing import (
    CoveragePriceRow,
    DiscountValueRow,
    QuotePriceInput,
    _QuotePriceWriter,
    effective_price_item,
    recalculate_quote,
)
from app.services.project_service import get_project

# 可编辑状态：其余状态一律 409（守卫见 ensure_editable）
EDITABLE_STATUSES = frozenset({QuoteStatus.PENDING_CONFIRM, QuoteStatus.CONFIRMED})

# 价格分项（值字段, 状态字段, 中文名）；官方总价单列（状态枚举不同）
_PRICE_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("commercial_premium", "commercial_status", "商业险"),
    ("compulsory_premium", "compulsory_status", "交强险"),
    ("vehicle_tax", "vehicle_tax_status", "车船税"),
    ("package_total", "package_status", "独立保障包"),
    ("other_fees", "other_fees_status", "其他费用"),
)

# 车辆快照字段：确认时用于冲突检测与项目摘要回填（ORM 属性名）
_SNAPSHOT_FIELDS = ("vehicle_model", "vehicle_seats", "first_reg_date", "is_nev")

# 确认时的价格分项完整性检查表：(中文名, 值字段, 状态字段, 计算值字段或 None)
_CONFIRM_CHECKS = (
    ("商业险", "commercial_premium", "commercial_status", "computed_commercial_premium"),
    ("交强险", "compulsory_premium", "compulsory_status", None),
    ("车船税", "vehicle_tax", "vehicle_tax_status", None),
    ("独立保障包", "package_total", "package_status", "computed_package_total"),
    ("其他费用", "other_fees", "other_fees_status", None),
)

# field_evidence 使用的标量字段名（camelCase，与对外 JSON 一致）
_EVIDENCE_NAMES = {
    "commercial_premium": "commercialPremium",
    "compulsory_premium": "compulsoryPremium",
    "vehicle_tax": "vehicleTax",
    "package_total": "packageTotal",
    "other_fees": "otherFees",
    "official_total": "officialTotal",
    "vehicle_model": "vehicleModel",
    "vehicle_seats": "vehicleSeats",
    "first_reg_date": "firstRegDate",
    "is_nev": "isNev",
    "insurer_code": "insurerCode",
}

_STATUS_LABELS = {
    QuoteStatus.DRAFT: "草稿",
    QuoteStatus.PARSING: "解析中",
    QuoteStatus.PENDING_CONFIRM: "待确认",
    QuoteStatus.CONFIRMED: "已确认",
    QuoteStatus.PARSE_FAILED: "解析失败",
    QuoteStatus.MERGE_REVIEW: "合并确认中",
}


# ---- 加载与守卫 ----


async def get_quote_or_404(db: AsyncSession, quote_id: int) -> Quote:
    quote = await db.get(Quote, quote_id)
    if quote is None:
        raise QuoteNotFoundError()
    return quote


async def load_quote_full(db: AsyncSession, quote_id: int) -> Quote:
    """一次性带出全部明细与项目（读模型构建专用，避免异步懒加载）。"""
    stmt = (
        select(Quote)
        .where(Quote.id == quote_id)
        .options(
            selectinload(Quote.project),
            selectinload(Quote.coverages),
            selectinload(Quote.services),
            selectinload(Quote.packages).selectinload(SupplementalPackage.coverages),
            selectinload(Quote.annotations),
            selectinload(Quote.discounts),
            selectinload(Quote.evidences),
            # 关联文件按 sortOrder 排序（relationship order_by），供文件预览条
            selectinload(Quote.files),
        )
    )
    quote = (await db.execute(stmt)).scalar_one_or_none()
    if quote is None:
        raise QuoteNotFoundError()
    return quote


def ensure_editable(quote: Quote) -> None:
    """状态守卫：非可编辑状态一律 409，不落任何半修改数据。"""
    if quote.status not in EDITABLE_STATUSES:
        label = _STATUS_LABELS.get(quote.status, quote.status.value)
        raise QuoteStateError(
            f"「{label}」状态的报价不允许此操作；请先完成或等待解析流程"
        )


async def _get_editable_quote(db: AsyncSession, quote_id: int) -> Quote:
    quote = await get_quote_or_404(db, quote_id)
    ensure_editable(quote)
    return quote


async def _get_owned_row(db: AsyncSession, model, quote_id: int, row_id: int):  # noqa: ANN001
    """取属于指定报价的明细行；跨报价访问一律按不存在处理（不泄露存在性）。"""
    row = await db.get(model, row_id)
    if row is None or row.quote_id != quote_id:
        raise QuoteDetailNotFoundError()
    return row


# ---- 车辆冲突检测 ----


def detect_vehicle_conflict(project: ComparisonProject, quote: Quote) -> VehicleConflictInfo:
    """报价快照与项目摘要对比（SPEC §6.10）。

    只有双方都非空才构成冲突；初登日期差异仅提示，不参与阻断。
    """
    fields: list[str] = []
    if (
        project.vehicle_model
        and quote.vehicle_model
        and project.vehicle_model != quote.vehicle_model
    ):
        fields.append("vehicleModel")
    if (
        project.vehicle_seats is not None
        and quote.vehicle_seats is not None
        and project.vehicle_seats != quote.vehicle_seats
    ):
        fields.append("vehicleSeats")
    if (
        project.is_nev is not None
        and quote.is_nev is not None
        and project.is_nev != quote.is_nev
    ):
        fields.append("isNev")
    first_reg_differs = bool(
        project.first_reg_date
        and quote.first_reg_date
        and project.first_reg_date != quote.first_reg_date
    )
    return VehicleConflictInfo(
        fields=fields,
        first_reg_date_differs=first_reg_differs,
        resolution_required=bool(fields),
    )


# ---- 重算与 evidence ----


async def _recalculate(db: AsyncSession, quote: Quote, tolerance: Decimal) -> None:
    """读取全部价格输入并重算（写操作必须在同一事务内调用本函数）。"""
    coverages = (
        (await db.execute(select(QuoteCoverage).where(QuoteCoverage.quote_id == quote.id)))
        .scalars()
        .all()
    )
    package_premiums = (
        (
            await db.execute(
                select(SupplementalPackage.premium).where(
                    SupplementalPackage.quote_id == quote.id
                )
            )
        )
        .scalars()
        .all()
    )
    discounts = (
        (await db.execute(select(Discount).where(Discount.quote_id == quote.id)))
        .scalars()
        .all()
    )
    recalculate_quote(
        QuotePriceInput(
            commercial_status=quote.commercial_status,
            commercial_premium=quote.commercial_premium,
            compulsory_status=quote.compulsory_status,
            compulsory_premium=quote.compulsory_premium,
            vehicle_tax_status=quote.vehicle_tax_status,
            vehicle_tax=quote.vehicle_tax,
            package_status=quote.package_status,
            package_total=quote.package_total,
            other_fees_status=quote.other_fees_status,
            other_fees=quote.other_fees,
            official_total=quote.official_total,
        ),
        coverages=[
            CoveragePriceRow(
                category=row.category,
                code=row.code,
                status=row.status,
                premium=row.premium,
                coverage_amount=row.coverage_amount,
                per_seat_amount=row.per_seat_amount,
            )
            for row in coverages
        ],
        package_premiums=list(package_premiums),
        discounts=[
            DiscountValueRow(row.include_in_net, row.cash_equivalent) for row in discounts
        ],
        tolerance=tolerance,
        writer=_QuotePriceWriter(quote),
    )


async def _touch_evidence(
    db: AsyncSession, quote_id: int, field_name: str, raw_value: str | None
) -> None:
    """用户修改标量字段后 upsert field_evidence（editedByUser=true、HIGH）。"""
    stmt = select(FieldEvidence).where(
        FieldEvidence.quote_id == quote_id, FieldEvidence.field_name == field_name
    )
    evidence = (await db.execute(stmt)).scalar_one_or_none()
    if evidence is None:
        db.add(
            FieldEvidence(
                quote_id=quote_id,
                field_name=field_name,
                raw_value=raw_value,
                confidence_level=ConfidenceLevel.HIGH,
                edited_by_user=True,
            )
        )
    else:
        evidence.raw_value = raw_value
        evidence.confidence_level = ConfidenceLevel.HIGH
        evidence.edited_by_user = True


# ---- 报价容器 ----


async def create_quote(db: AsyncSession, project_id: int, payload: QuoteCreate) -> Quote:
    """创建报价容器（SPEC §2.10）：

    - MANUAL 创建即进入 PENDING_CONFIRM（空表单等待完整手动录入）；
    - UPLOADED 只创建 DRAFT 容器，等待 TASK-03 的上传流程；
    - 预置公司显示名固定为标准名；OTHER 必须带自由输入公司名。
    """
    project = await get_project(db, project_id)
    code = payload.insurer_code.strip()
    if code not in INSURER_DEFINITIONS:
        raise ValidationError(f"未知保险公司码：{code}，请从预置公司列表中选择")
    if code == "OTHER":
        if not payload.insurer_name or not payload.insurer_name.strip():
            raise ValidationError("选择“其他”公司时必须填写公司名称")
        insurer_name = sanitize_text(payload.insurer_name.strip())
    else:
        insurer_name = INSURER_DEFINITIONS[code]

    quote = Quote(
        project_id=project.id,
        insurer_code=code,
        insurer_name=insurer_name,
        agent_name=sanitize_text(payload.agent_name.strip()) if payload.agent_name else None,
        source=payload.source,
        status=(
            QuoteStatus.PENDING_CONFIRM
            if payload.source == QuoteSource.MANUAL
            else QuoteStatus.DRAFT
        ),
    )
    db.add(quote)
    await db.flush()
    # 公司码是用户显式选择，写入标量证据（用户录入口径）
    await _touch_evidence(db, quote.id, "insurerCode", code)
    await db.commit()
    return quote


async def update_quote(
    db: AsyncSession, quote_id: int, payload: QuoteUpdate, tolerance: Decimal
) -> Quote:
    """编辑基本信息与价格分项；价格变更后同事务重算。"""
    quote = await _get_editable_quote(db, quote_id)
    provided = payload.model_fields_set

    # ---- 基本信息自由文本（统一脱敏）----
    if "agent_name" in provided:
        quote.agent_name = (
            sanitize_text(payload.agent_name.strip()) if payload.agent_name else None
        )
    if "plan_label" in provided:
        quote.plan_label = (
            sanitize_text(payload.plan_label.strip()) if payload.plan_label else None
        )
    if "note" in provided:
        quote.note = sanitize_text(payload.note) if payload.note else None

    # ---- 车辆快照 ----
    if "vehicle_model" in provided:
        quote.vehicle_model = (
            sanitize_text(payload.vehicle_model.strip()) if payload.vehicle_model else None
        )
        await _touch_evidence(db, quote.id, "vehicleModel", quote.vehicle_model)
    if "vehicle_seats" in provided:
        quote.vehicle_seats = payload.vehicle_seats
        await _touch_evidence(
            db, quote.id, "vehicleSeats", str(payload.vehicle_seats)
            if payload.vehicle_seats is not None
            else None
        )
    if "first_reg_date" in provided:
        quote.first_reg_date = payload.first_reg_date
        await _touch_evidence(db, quote.id, "firstRegDate", payload.first_reg_date)
    if "is_nev" in provided:
        quote.is_nev = payload.is_nev
        await _touch_evidence(
            db, quote.id, "isNev", str(payload.is_nev) if payload.is_nev is not None else None
        )

    # ---- 五个价格分项 ----
    # 规则（SPEC §2.2“显示值优先、计算值回退”）：
    # - 提供非空金额 → 分项即 INCLUDED；
    # - 金额与 NOT_INCLUDED 同时出现 → 矛盾，422；
    # - 金额清空或仅改状态 → 值置空；INCLUDED 允许无用户值（回退计算值），
    #   但确认时若“值与计算值都缺”会被 confirm 阻断，系统绝不把 null 当 0。
    for value_field, status_field, label in _PRICE_ITEMS:
        value_provided = value_field in provided
        status_provided = status_field in provided
        value = getattr(payload, value_field)
        status = getattr(payload, status_field)
        if value_provided and value is not None:
            if status_provided and status == PriceItemStatus.NOT_INCLUDED:
                raise ValidationError(f"{label}：金额与“不包含”状态互相矛盾，请二选一")
            setattr(quote, value_field, value)
            setattr(quote, status_field, PriceItemStatus.INCLUDED)
            await _touch_evidence(db, quote.id, _EVIDENCE_NAMES[value_field], str(value))
        elif value_provided or status_provided:
            # 仅清空金额时保留现有状态意图（INCLUDED 时回退计算值）
            new_status = status if status_provided else getattr(quote, status_field)
            setattr(quote, value_field, None)
            setattr(quote, status_field, new_status)
            await _touch_evidence(db, quote.id, _EVIDENCE_NAMES[value_field], None)

    # ---- 官方总价（状态枚举只有 INCLUDED / UNKNOWN）----
    if "official_total" in provided:
        if payload.official_total is not None:
            quote.official_total = payload.official_total
            quote.official_total_status = OfficialTotalStatus.INCLUDED
            await _touch_evidence(db, quote.id, "officialTotal", str(payload.official_total))
        else:
            quote.official_total = None
            quote.official_total_status = OfficialTotalStatus.UNKNOWN
            await _touch_evidence(db, quote.id, "officialTotal", None)

    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def delete_quote(db: AsyncSession, quote_id: int, settings) -> None:  # noqa: ANN001
    """删除报价及全部明细；提交后按无引用规则清理文件资产（TASK-03）。

    删除只移除该报价的 quote_file_link；文件若仍被兄弟报价或解析任务
    引用则保留（SPEC §2.8），完全无引用时连数据库行与磁盘目录一起清理。
    """
    quote = await get_quote_or_404(db, quote_id)
    project_id = quote.project_id
    await db.delete(quote)
    await db.commit()
    await parse_service.purge_unreferenced_files(db, project_id, settings)


async def confirm_quote(
    db: AsyncSession, quote_id: int, payload: QuoteConfirm, tolerance: Decimal
) -> Quote:
    """手动/单方案确认（SPEC §2.10、§6.10）。

    - 仅 PENDING_CONFIRM 可确认；
    - 价格分项必须明确：INCLUDED 必须有有效金额（用户值或计算值）；
    - 车辆摘要冲突必须显式二选一：USE_QUOTE 回填/覆盖项目摘要，
      KEEP_PROJECT 保留摘要（两者都不改报价自身快照）；
    - 无冲突时按“回填空缺”处理项目摘要；初登日期只提示不阻断。
    """
    quote = await load_quote_full(db, quote_id)
    if quote.status != QuoteStatus.PENDING_CONFIRM:
        label = _STATUS_LABELS.get(quote.status, quote.status.value)
        raise QuoteStateError(f"「{label}」状态的报价不能确认，仅待确认报价可确认")

    project = quote.project

    # 价格分项完整性：INCLUDED 必须能取到有效金额（用户值或计算值）
    missing: list[str] = []
    for label, value_field, status_field, computed_field in _CONFIRM_CHECKS:
        status = getattr(quote, status_field)
        if status != PriceItemStatus.INCLUDED:
            continue
        effective = effective_price_item(
            status,
            getattr(quote, value_field),
            getattr(quote, computed_field) if computed_field else None,
        )
        if effective is None:
            missing.append(label)
    if missing:
        raise ValidationError(
            f"以下价格分项标记为已包含但缺少金额，请填写金额或改为“不包含/未知”：{'、'.join(missing)}"
        )

    conflict = detect_vehicle_conflict(project, quote)
    if conflict.resolution_required and payload.vehicle_conflict_resolution is None:
        raise ValidationError(
            "车辆信息与项目摘要不一致，请选择“以报价为准”或“以项目为准”",
            code="VEHICLE_CONFLICT_UNRESOLVED",
        )

    if payload.vehicle_conflict_resolution == "USE_QUOTE":
        # 以报价为准：快照非空字段覆盖/回填项目摘要
        for field in _SNAPSHOT_FIELDS:
            value = getattr(quote, field)
            if value is not None:
                setattr(project, field, value)
    else:
        # 以项目为准 / 无冲突：只回填项目摘要中的空缺字段
        for field in _SNAPSHOT_FIELDS:
            if getattr(project, field) is None:
                setattr(project, field, getattr(quote, field))

    quote.status = QuoteStatus.CONFIRMED
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


# ---- 险种行 ----


def _category_from_definition(category: str) -> CoverageCategory:
    """字典类别 → 行级枚举；未知类别按开发期错误处理。"""
    if category == CATEGORY_CORE:
        return CoverageCategory.CORE
    if category == CATEGORY_ADDITIONAL:
        return CoverageCategory.ADDITIONAL
    raise ValidationError(f"险种类别不合法：{category}")


def _apply_coverage_code(row: QuoteCoverage, code: str | None, raw_name: str) -> None:
    """按标准码归类险种行；code=null 退回未识别区（手动映射/撤销映射）。

    交强险不允许作为险种行（SPEC §3.1）：只落价格分项与 field_evidence。
    """
    if code is None:
        row.code = None
        row.category = CoverageCategory.UNRECOGNIZED
        row.name = raw_name  # UNRECOGNIZED 时显示名 = 原始名称
        return
    definition = get_coverage_definition(code)
    if definition is None:
        raise ValidationError(f"未知险种码：{code}，请从标准险种列表中选择")
    if not definition.row_selectable:
        raise ValidationError(
            "交强险不作为险种行录入，请在“价格”页填写交强险保费"
        )
    row.code = definition.code
    row.category = _category_from_definition(definition.category)
    row.name = definition.label


async def create_coverage(
    db: AsyncSession, quote_id: int, payload: CoverageCreate, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    raw_name = sanitize_text(payload.raw_name.strip())
    row = QuoteCoverage(
        quote_id=quote_id,
        raw_name=raw_name,
        raw_value=None,
        status=payload.status,
        per_seat_amount=payload.per_seat_amount,
        seat_count=payload.seat_count,
        shared_coverage=payload.shared_coverage,
        premium=payload.premium,
        multiplier=payload.multiplier,
        condition=sanitize_text(payload.condition) if payload.condition else None,
        description=sanitize_text(payload.description) if payload.description else None,
        # 用户录入行：编辑保护与置信度口径（SPEC §5.3）
        confidence_level=ConfidenceLevel.HIGH,
        edited_by_user=True,
    )
    _apply_coverage_code(row, payload.code, raw_name)
    # 座位总额规则（SPEC §6.3）：总额 = 单座 × 座位，矛盾即拒绝
    row.coverage_amount = resolve_seat_amounts(
        payload.per_seat_amount, payload.seat_count, payload.coverage_amount
    )
    db.add(row)
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def update_coverage(
    db: AsyncSession,
    quote_id: int,
    row_id: int,
    payload: CoverageUpdate,
    tolerance: Decimal,
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    row = await _get_owned_row(db, QuoteCoverage, quote_id, row_id)
    provided = payload.model_fields_set

    if "raw_name" in provided and payload.raw_name is not None:
        row.raw_name = sanitize_text(payload.raw_name.strip())
        if row.category == CoverageCategory.UNRECOGNIZED:
            row.name = row.raw_name
    if "code" in provided:
        _apply_coverage_code(row, payload.code, row.raw_name)
    if "status" in provided and payload.status is not None:
        row.status = payload.status
    if "per_seat_amount" in provided:
        row.per_seat_amount = payload.per_seat_amount
    if "seat_count" in provided:
        row.seat_count = payload.seat_count
    if "coverage_amount" in provided:
        row.coverage_amount = payload.coverage_amount
    # 座位总额规则对合并后的最终值重新校验/推导
    row.coverage_amount = resolve_seat_amounts(
        row.per_seat_amount, row.seat_count, row.coverage_amount
    )
    if "shared_coverage" in provided:
        row.shared_coverage = payload.shared_coverage
    if "premium" in provided:
        row.premium = payload.premium
    if "multiplier" in provided:
        row.multiplier = payload.multiplier
    if "condition" in provided:
        row.condition = sanitize_text(payload.condition) if payload.condition else None
    if "description" in provided:
        row.description = sanitize_text(payload.description) if payload.description else None
    # 任何用户修改都进入编辑保护
    row.edited_by_user = True
    row.confidence_level = ConfidenceLevel.HIGH

    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def delete_coverage(
    db: AsyncSession, quote_id: int, row_id: int, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    row = await _get_owned_row(db, QuoteCoverage, quote_id, row_id)
    await db.delete(row)
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


# ---- 增值服务 ----


async def create_service(
    db: AsyncSession, quote_id: int, payload: ServiceCreate, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    db.add(
        QuoteService(
            quote_id=quote_id,
            service_type=payload.service_type,
            status=payload.status,
            count=payload.count,
            cost=payload.cost,
            description=sanitize_text(payload.description) if payload.description else None,
            confidence_level=ConfidenceLevel.HIGH,
            edited_by_user=True,
        )
    )
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def update_service(
    db: AsyncSession, quote_id: int, row_id: int, payload: ServiceUpdate, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    row = await _get_owned_row(db, QuoteService, quote_id, row_id)
    provided = payload.model_fields_set
    if "service_type" in provided and payload.service_type is not None:
        row.service_type = payload.service_type
    if "status" in provided and payload.status is not None:
        row.status = payload.status
    if "count" in provided:
        row.count = payload.count
    if "cost" in provided:
        row.cost = payload.cost
    if "description" in provided:
        row.description = sanitize_text(payload.description) if payload.description else None
    row.edited_by_user = True
    row.confidence_level = ConfidenceLevel.HIGH
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def delete_service(
    db: AsyncSession, quote_id: int, row_id: int, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    row = await _get_owned_row(db, QuoteService, quote_id, row_id)
    await db.delete(row)
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


# ---- 独立保障包及内部保障 ----


def _validate_package_coverage_type(type_code: str) -> str:
    """保障包内部类型只接受 §3.3 码表；手动选择非法码直接 422。"""
    if type_code not in PACKAGE_COVERAGE_DEFINITIONS:
        raise ValidationError(f"未知保障包内部类型：{type_code}")
    return type_code


async def create_package(
    db: AsyncSession, quote_id: int, payload: PackageCreate, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    package = SupplementalPackage(
        quote_id=quote_id,
        name=sanitize_text(payload.name.strip()),
        provider=sanitize_text(payload.provider.strip()) if payload.provider else None,
        premium=payload.premium,
        description=sanitize_text(payload.description) if payload.description else None,
        confidence_level=ConfidenceLevel.HIGH,
        edited_by_user=True,
    )
    for item in payload.coverages:
        package.coverages.append(
            PackageCoverage(
                type=_validate_package_coverage_type(item.type),
                name=sanitize_text(item.name.strip()) if item.name else None,
                status=item.status,
                coverage_amount=item.coverage_amount,
                unit=item.unit,
                per_seat_amount=item.per_seat_amount,
                seat_count=item.seat_count,
                shared=item.shared,
                multiplier=item.multiplier,
                condition=sanitize_text(item.condition) if item.condition else None,
                description=sanitize_text(item.description) if item.description else None,
                raw_text=sanitize_text(item.raw_text) if item.raw_text else None,
                confidence_level=ConfidenceLevel.HIGH,
                edited_by_user=True,
            )
        )
    db.add(package)
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def update_package(
    db: AsyncSession, quote_id: int, package_id: int, payload: PackageUpdate, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    package = await _get_owned_row(db, SupplementalPackage, quote_id, package_id)
    provided = payload.model_fields_set
    if "name" in provided and payload.name is not None:
        package.name = sanitize_text(payload.name.strip())
    if "provider" in provided:
        package.provider = (
            sanitize_text(payload.provider.strip()) if payload.provider else None
        )
    if "premium" in provided:
        package.premium = payload.premium
    if "description" in provided:
        package.description = (
            sanitize_text(payload.description) if payload.description else None
        )
    package.edited_by_user = True
    package.confidence_level = ConfidenceLevel.HIGH
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def delete_package(
    db: AsyncSession, quote_id: int, package_id: int, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    package = await _get_owned_row(db, SupplementalPackage, quote_id, package_id)
    await db.delete(package)
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def _get_owned_package(
    db: AsyncSession, quote_id: int, package_id: int
) -> SupplementalPackage:
    package = await db.get(SupplementalPackage, package_id)
    if package is None or package.quote_id != quote_id:
        raise QuoteDetailNotFoundError()
    return package


async def create_package_coverage(
    db: AsyncSession,
    quote_id: int,
    package_id: int,
    payload: PackageCoverageCreate,
    tolerance: Decimal,
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    await _get_owned_package(db, quote_id, package_id)
    # 直接按 package_id 建行：避免 append 触发集合的异步懒加载
    db.add(
        PackageCoverage(
            package_id=package_id,
            type=_validate_package_coverage_type(payload.type),
            name=sanitize_text(payload.name.strip()) if payload.name else None,
            status=payload.status,
            coverage_amount=payload.coverage_amount,
            unit=payload.unit,
            per_seat_amount=payload.per_seat_amount,
            seat_count=payload.seat_count,
            shared=payload.shared,
            multiplier=payload.multiplier,
            condition=sanitize_text(payload.condition) if payload.condition else None,
            description=sanitize_text(payload.description) if payload.description else None,
            raw_text=sanitize_text(payload.raw_text) if payload.raw_text else None,
            confidence_level=ConfidenceLevel.HIGH,
            edited_by_user=True,
        )
    )
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def update_package_coverage(
    db: AsyncSession,
    quote_id: int,
    package_id: int,
    coverage_id: int,
    payload: PackageCoverageUpdate,
    tolerance: Decimal,
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    await _get_owned_package(db, quote_id, package_id)
    row = await db.get(PackageCoverage, coverage_id)
    if row is None or row.package_id != package_id:
        raise QuoteDetailNotFoundError()
    provided = payload.model_fields_set
    if "type" in provided and payload.type is not None:
        row.type = _validate_package_coverage_type(payload.type)
    if "name" in provided:
        row.name = sanitize_text(payload.name.strip()) if payload.name else None
    if "status" in provided and payload.status is not None:
        row.status = payload.status
    if "coverage_amount" in provided:
        row.coverage_amount = payload.coverage_amount
    if "unit" in provided:
        row.unit = payload.unit
    if "per_seat_amount" in provided:
        row.per_seat_amount = payload.per_seat_amount
    if "seat_count" in provided:
        row.seat_count = payload.seat_count
    if "shared" in provided:
        row.shared = payload.shared
    if "multiplier" in provided:
        row.multiplier = payload.multiplier
    if "condition" in provided:
        row.condition = sanitize_text(payload.condition) if payload.condition else None
    if "description" in provided:
        row.description = sanitize_text(payload.description) if payload.description else None
    if "raw_text" in provided:
        row.raw_text = sanitize_text(payload.raw_text) if payload.raw_text else None
    row.edited_by_user = True
    row.confidence_level = ConfidenceLevel.HIGH
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def delete_package_coverage(
    db: AsyncSession, quote_id: int, package_id: int, coverage_id: int, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    await _get_owned_package(db, quote_id, package_id)
    row = await db.get(PackageCoverage, coverage_id)
    if row is None or row.package_id != package_id:
        raise QuoteDetailNotFoundError()
    await db.delete(row)
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


# ---- 销售/用户标注 ----


async def create_annotation(
    db: AsyncSession, quote_id: int, payload: AnnotationCreate, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    db.add(
        SalesAnnotation(
            quote_id=quote_id,
            content=sanitize_text(payload.content.strip()),
            kind=payload.kind,
            source_type=payload.source_type,
            edited_by_user=True,
        )
    )
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def update_annotation(
    db: AsyncSession, quote_id: int, row_id: int, payload: AnnotationUpdate, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    row = await _get_owned_row(db, SalesAnnotation, quote_id, row_id)
    provided = payload.model_fields_set
    if "content" in provided and payload.content is not None:
        row.content = sanitize_text(payload.content.strip())
    if "kind" in provided and payload.kind is not None:
        row.kind = payload.kind
    row.edited_by_user = True
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def delete_annotation(
    db: AsyncSession, quote_id: int, row_id: int, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    row = await _get_owned_row(db, SalesAnnotation, quote_id, row_id)
    await db.delete(row)
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


# ---- 优惠 ----


async def create_discount(
    db: AsyncSession, quote_id: int, payload: DiscountCreate, tolerance: Decimal
) -> Quote:
    """新增优惠：SERVICE 类默认无折现值（UI 不默认填），无折现值不减钱。"""
    quote = await _get_editable_quote(db, quote_id)
    db.add(
        Discount(
            quote_id=quote_id,
            discount_type=payload.discount_type,
            description=sanitize_text(payload.description.strip())
            if payload.description
            else None,
            amount=payload.amount,
            cash_equivalent=payload.cash_equivalent,
            include_in_net=payload.include_in_net,
        )
    )
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def update_discount(
    db: AsyncSession, quote_id: int, row_id: int, payload: DiscountUpdate, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    row = await _get_owned_row(db, Discount, quote_id, row_id)
    provided = payload.model_fields_set
    if "discount_type" in provided and payload.discount_type is not None:
        row.discount_type = payload.discount_type
    if "description" in provided:
        row.description = (
            sanitize_text(payload.description.strip()) if payload.description else None
        )
    if "amount" in provided:
        row.amount = payload.amount
    if "cash_equivalent" in provided:
        row.cash_equivalent = payload.cash_equivalent
    if "include_in_net" in provided and payload.include_in_net is not None:
        row.include_in_net = payload.include_in_net
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


async def delete_discount(
    db: AsyncSession, quote_id: int, row_id: int, tolerance: Decimal
) -> Quote:
    quote = await _get_editable_quote(db, quote_id)
    row = await _get_owned_row(db, Discount, quote_id, row_id)
    await db.delete(row)
    await _recalculate(db, quote, tolerance)
    await db.commit()
    return quote


# ---- 保额档位提示（读模型组装时使用）----


def coverage_range_hint(row: QuoteCoverage) -> str | None:
    return check_amount_range(row.code, row.coverage_amount)


# ---- 读模型组装 ----


def build_quote_read(quote: Quote) -> QuoteRead:
    """把已完整加载的 ORM 报价组装为对外读模型。

    要求 quote 经 load_quote_full 加载（project 与各层明细齐全），
    行顺序按 id 稳定排序，避免响应顺序漂移。
    """
    conflict = detect_vehicle_conflict(quote.project, quote)

    coverage_reads = []
    for row in sorted(quote.coverages, key=lambda item: item.id):
        read = CoverageRead.model_validate(row)
        read.amount_range_hint = coverage_range_hint(row)
        coverage_reads.append(read)

    read = QuoteRead.model_validate(quote)
    # model_copy(update=...) 不做二次校验：注入的对象均已按各自模型校验
    return read.model_copy(
        update={
            "vehicle_conflict": conflict,
            # 关联文件展示信息：QuoteFile 的 file_name/raw_url 只读属性
            # 使 FileRead 可直接按 from_attributes 构造（含受控预览地址）
            "files": [FileRead.model_validate(f) for f in quote.files],
            "coverages": coverage_reads,
            "services": [
                ServiceRead.model_validate(row) for row in sorted(quote.services, key=lambda r: r.id)
            ],
            "packages": [
                PackageRead.model_validate(package).model_copy(
                    update={
                        "coverages": [
                            PackageCoverageRead.model_validate(item)
                            for item in sorted(package.coverages, key=lambda r: r.id)
                        ]
                    }
                )
                for package in sorted(quote.packages, key=lambda r: r.id)
            ],
            "annotations": [
                AnnotationRead.model_validate(row)
                for row in sorted(quote.annotations, key=lambda r: r.id)
            ],
            "discounts": [
                DiscountRead.model_validate(row) for row in sorted(quote.discounts, key=lambda r: r.id)
            ],
            "evidences": [
                FieldEvidenceRead.model_validate(row)
                for row in sorted(quote.evidences, key=lambda r: r.id)
            ],
        }
    )
