"""候选数据落库（SPEC §2.10、§4 步骤 5-10、§5、§6；TASK-04 范围 5-9）。

事务与状态不变量：
- 落库由 pipeline 在独立会话的单个事务内完成；成功时写脱敏 rawResult、
  候选价格/车辆/明细/证据并把报价置为 PENDING_CONFIRM；
- 失败路径（provider / 流水线异常）不进入本模块，报价状态由 worker 按
  状态机迁移（PARSING → PARSE_FAILED，或 PENDING_CONFIRM 保留旧候选）；
- 重解析覆盖规则（SPEC §2.10）：只覆盖未被用户编辑的候选——价格/车辆
  标量按 field_evidence.editedByUser 判定，明细行按行 editedByUser 判定；
- 同公司 planCount > 1：只写脱敏 rawResult，报价回 PENDING_CONFIRM，
  不把任何 plan 明细写入报价（拆分视图属 TASK-05）；
- 一批含不同保险公司：以明确错误停止（不可重试），不进入拆分。

写入顺序（同一事务）：
  1) 证据行编辑保护判定 → 删除旧的非用户候选行（含旧证据行）；
  2) 合并价格/车辆标量（用户编辑字段跳过）并构建明细候选行；
  3) 以“保留行 + 候选行”的合并口径预演总额三态，用于价格字段置信度；
  4) 写标量证据行、插入候选行、用 pricing 服务重算并落 PENDING_CONFIRM。

隐私边界：
- rawResult 落库前整树脱敏（sanitize_raw_result）；
- evidence.text / 描述 / 标注 / 未识别原文等自由文本入库前统一脱敏，
  无法安全处理时改为 HIDDEN_TEXT；
- 本模块的异常文案均为固定中文提示，不携带原文或模型响应。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.privacy import sanitize_evidence_text, sanitize_raw_result, sanitize_text
from app.models import (
    FieldEvidence,
    PackageCoverage,
    ParseTask,
    Quote,
    QuoteCoverage,
    QuoteService,
    SalesAnnotation,
    SupplementalPackage,
)
from app.models.enums import (
    AnnotationKind,
    AnnotationSourceType,
    ConfidenceLevel,
    CoverageCategory,
    ItemStatus,
    OfficialTotalStatus,
    PackageUnit,
    PriceItemStatus,
    QuoteStatus,
    ServiceType,
    TotalCheckStatus,
)
from app.services.normalization.alias_map import (
    PACKAGE_COVERAGE_DEFINITIONS,
    get_coverage_definition,
)
from app.services.normalization.amounts import (
    RowIdentity,
    check_amount_range,
    parse_seat_expression,
)
from app.services.normalization.engine import (
    clean_name,
    match_coverage,
    match_insurer,
    match_package_type,
    match_service,
    normalize_condition,
)
from app.services.parser.extraction_schema import (
    EvidenceExtraction,
    ExtractionResult,
    PlanExtraction,
)
from app.services.parser.pipeline import ParseTaskFailure, ParseTaskFileInput
from app.services.pricing import (
    CoveragePriceRow,
    QuotePriceInput,
    _QuotePriceWriter,
    compute_commercial_premium,
    compute_computed_total,
    compute_package_total,
    recalculate_quote,
    resolve_total_check_status,
)
from app.services.validation.rules import (
    nev_inconsistent,
    resolve_service_status,
    synthesize_confidence,
)

# 价格分项：(证据字段名, 值属性, 状态属性)——与 TASK-02 的 evidence 命名一致
_PRICE_MERGES: tuple[tuple[str, str, str], ...] = (
    ("commercialPremium", "commercial_premium", "commercial_status"),
    ("compulsoryPremium", "compulsory_premium", "compulsory_status"),
    ("vehicleTax", "vehicle_tax", "vehicle_tax_status"),
    ("packageTotal", "package_total", "package_status"),
    ("otherFees", "other_fees", "other_fees_status"),
)

# 车辆快照：(证据字段名, 报价属性, 提取模型字段名)
_VEHICLE_MERGES: tuple[tuple[str, str, str], ...] = (
    ("vehicleModel", "vehicle_model", "model"),
    ("vehicleSeats", "vehicle_seats", "seatCount"),
    ("firstRegDate", "first_reg_date", "firstRegDate"),
    ("isNev", "is_nev", "isNev"),
)

# 模型识别公司写入 field_evidence 的字段名（确认页据此展示公司冲突）
INSURER_MODEL_FIELD = "insurerModelDetected"

# 服务费用/保障包保费的原文兜底解析：只认“明确 N 元”形态，绝不推断
_MONEY_IN_TEXT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*元")

# 初登日期可安全解析的形态：YYYY-MM / YYYY年M月 / YYYY/M
_DATE_PATTERNS = (
    re.compile(r"^(\d{4})-(\d{1,2})$"),
    re.compile(r"^(\d{4})年(\d{1,2})月?$"),
    re.compile(r"^(\d{4})/(\d{1,2})$"),
)

# “翻倍”文本 → 倍数 2（SPEC §4.1 示例的确定性兜底）
_DOUBLE_WORD = "翻倍"

_MIXED_INSURER_ERROR = (
    "同一批次识别到不同保险公司的报价，无法自动拆分；"
    "请按保险公司分别上传后再解析"
)


@dataclass(slots=True, frozen=True)
class ResolvedEvidence:
    """证据解析结果；file_id 仅在 state="ok" 时非空。"""

    file_id: int | None
    page: int | None
    text: str | None
    state: str  # "ok" | "missing" | "invalid"

    @property
    def key(self) -> tuple[int | None, int | None, str | None]:
        """参与重复行判定的证据键（§6.4：证据也相同才算重复）。"""
        return (self.file_id, self.page, self.text)


class EvidenceResolver:
    """fileKey/page → sourceFileId 的唯一入口（SPEC §6.9）。

    - fileKey 必须属于本次 parse_task_file，page 必须落在该文件页数内，
      多文件相同页码不得串文件（按 fileKey 精确映射，结构性防串）；
    - 合法 → state="ok" 并携带 sourceFileId；fileKey 与 page 双缺 →
      state="missing"（按无 evidence 处理，MEDIUM）；任一非法 →
      state="invalid"（LOW，且绝不建立来源链接，不伪造 sourceFileId）。
    """

    def __init__(self, files: list[ParseTaskFileInput]) -> None:
        self._by_key = {item.file_key: item for item in files}

    def resolve(self, evidence: EvidenceExtraction | None) -> ResolvedEvidence:
        if evidence is None or (evidence.fileKey is None and evidence.page is None):
            text = sanitize_evidence_text(evidence.text) if evidence else None
            return ResolvedEvidence(None, None, text, "missing")
        text = sanitize_evidence_text(evidence.text)
        item = self._by_key.get(evidence.fileKey or "") if evidence.fileKey else None
        if item is None:
            return ResolvedEvidence(None, None, text, "invalid")
        if evidence.page is None or evidence.page < 1 or evidence.page > item.page_count:
            return ResolvedEvidence(None, None, text, "invalid")
        return ResolvedEvidence(item.file_id, evidence.page, text, "ok")


# ---- 小工具 ----


def _to_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _scan_money_in_text(raw_text: str | None) -> Decimal | None:
    """从原文提取最后一个“N 元”金额（费用/保费兜底）；没有则 None。

    只接受显式“元”后缀，绝不把“2 次”之类误读成金额。
    """
    if not raw_text:
        return None
    matches = _MONEY_IN_TEXT.findall(raw_text)
    if not matches:
        return None
    return Decimal(matches[-1].replace(",", "")).quantize(Decimal("0.01"))


def _normalize_first_reg_date(value: str | None) -> str | None:
    """初登日期统一为 YYYY-MM；无法安全解析返回 None（不猜）。"""
    if not value:
        return None
    text = value.strip()
    for pattern in _DATE_PATTERNS:
        match = pattern.match(text)
        if match:
            year, month = int(match.group(1)), int(match.group(2))
            if 1 <= month <= 12:
                return f"{year:04d}-{month:02d}"
    return None


def _normalize_multiplier(multiplier: float | None, raw_text: str | None) -> Decimal | None:
    """倍数归一化：模型值优先；缺失且原文含“翻倍”时按 2 兜底。"""
    if multiplier is not None:
        return Decimal(str(multiplier)).quantize(Decimal("0.01"))
    if raw_text and _DOUBLE_WORD in raw_text:
        return Decimal("2.00")
    return None


def _status_from_model(raw_status: str | None, *, is_service: bool) -> ItemStatus:
    """模型状态串 → 行状态枚举；非法/缺失按 UNKNOWN。

    险种行的 FREE 无业务含义（“保费 0 元”是 INCLUDED，SPEC §6.6），
    统一改 INCLUDED；服务行的 FREE 交由 resolve_service_status 再校验。
    """
    if raw_status:
        try:
            status = ItemStatus(raw_status)
        except ValueError:
            return ItemStatus.UNKNOWN
        if status == ItemStatus.FREE and not is_service:
            return ItemStatus.INCLUDED
        return status
    return ItemStatus.UNKNOWN


def _seat_fields(
    item_coverage: Decimal | None,
    item_per_seat: Decimal | None,
    item_seat_count: int | None,
    raw_value: str | None,
) -> tuple[Decimal | None, Decimal | None, int | None, bool]:
    """座位结构归一化（SPEC §6.3）。

    返回 (总额, 单座, 座位数, 是否矛盾)。模型给了“单座与座位”但总额缺失
    时自动补总额；三值齐备但总额 ≠ 单座×座位时，以“单座×座位”为准
    （候选值必须自洽）并标记矛盾 → 该行降为 LOW 交由用户核对。
    """
    per_seat, seat_count = item_per_seat, item_seat_count
    if per_seat is None and seat_count is None and raw_value:
        parsed = parse_seat_expression(raw_value)
        if parsed:
            per_seat, seat_count = parsed[0], parsed[1]
    amount = item_coverage
    if per_seat is not None and seat_count is not None:
        expected = (per_seat * seat_count).quantize(Decimal("0.01"))
        if amount is None:
            return expected, per_seat, seat_count, False
        if amount != expected:
            return expected, per_seat, seat_count, True
    return amount, per_seat, seat_count, False


# ---- 主入口 ----


async def apply_extraction(
    db: AsyncSession,
    *,
    task: ParseTask,
    quote: Quote | None,
    files: list[ParseTaskFileInput],
    extraction: ExtractionResult,
    settings: Settings,
) -> None:
    """把一次成功抽取写入候选数据（在调用方的单个事务内执行）。

    抛出 ParseTaskFailure（不可重试）表示“以明确错误停止”的分支：
    混合公司批次。多方案分支只落 rawResult 并回 PENDING_CONFIRM。
    """
    _ensure_single_insurer(extraction)
    # rawResult 落库前整树脱敏（TASK-04 范围 5）：任何分支都保留回放数据
    task.raw_result = sanitize_raw_result(extraction.model_dump())

    if quote is None:
        # 报价已在解析期间被删除：任务保留 rawResult，无候选可写
        return

    if len(extraction.plans) > 1:
        # 同公司多方案：容器报价回 PENDING_CONFIRM 展示“多方案待拆分”
        # 占位（ParseStatusRead.planCount 驱动），明细留待 TASK-05 拆分
        quote.status = QuoteStatus.PENDING_CONFIRM
        return

    plan = extraction.plans[0]
    resolver = EvidenceResolver(files)
    await _apply_single_plan(db, quote=quote, plan=plan, extraction=extraction,
                             resolver=resolver, settings=settings)
    quote.status = QuoteStatus.PENDING_CONFIRM


def _ensure_single_insurer(extraction: ExtractionResult) -> None:
    """批次公司一致性检测：逐方案公司名归一后必须同源。

    优先取 plan.insurerName（TASK-04 实现决策的可选键），缺失回退顶层
    insurer.name；映射不到标准码时按清洗后的公司名文本比较。
    """
    identities: set[str] = set()
    for plan in extraction.plans:
        name = plan.insurerName or extraction.insurer.name
        if not name or not name.strip():
            continue
        code = match_insurer(name)
        identities.add(code if code else clean_name(name))
    if len(identities) > 1:
        raise ParseTaskFailure(_MIXED_INSURER_ERROR)


async def _apply_single_plan(
    db: AsyncSession,
    *,
    quote: Quote,
    plan: PlanExtraction,
    extraction: ExtractionResult,
    resolver: EvidenceResolver,
    settings: Settings,
) -> None:
    """单方案候选写入：保护判定 → 清理旧候选 → 构建候选 → 置信度 → 重算。"""
    evidence_rows = (
        (await db.execute(select(FieldEvidence).where(FieldEvidence.quote_id == quote.id)))
        .scalars()
        .all()
    )

    def locked(field_name: str) -> bool:
        return any(
            row.field_name == field_name and row.edited_by_user for row in evidence_rows
        )

    # ---- 1. 保留行清单（删除前快照）----
    kept_coverages = await _load_kept(QuoteCoverage, quote.id, db)
    kept_services = await _load_kept(QuoteService, quote.id, db)
    kept_annotations = await _load_kept(SalesAnnotation, quote.id, db)
    kept_packages, kept_package_coverage_rows = await _load_kept_packages(db, quote.id)
    protected_package_ids = {package.id for package in kept_packages} | {
        row.package_id for row in kept_package_coverage_rows
    }

    # ---- 2. 删除旧的非用户候选行 ----
    await db.execute(
        delete(QuoteCoverage).where(
            QuoteCoverage.quote_id == quote.id, QuoteCoverage.edited_by_user.is_(False)
        )
    )
    await db.execute(
        delete(QuoteService).where(
            QuoteService.quote_id == quote.id, QuoteService.edited_by_user.is_(False)
        )
    )
    await db.execute(
        delete(SalesAnnotation).where(
            SalesAnnotation.quote_id == quote.id, SalesAnnotation.edited_by_user.is_(False)
        )
    )
    await db.execute(
        delete(FieldEvidence).where(
            FieldEvidence.quote_id == quote.id, FieldEvidence.edited_by_user.is_(False)
        )
    )
    # 保障包整包保护：包或包内任一行被编辑则整包保留（内部行编辑把
    # editedByUser 落在行上），其余整包删除（内部行随包级联删除）
    if protected_package_ids:
        await db.execute(
            delete(SupplementalPackage).where(
                SupplementalPackage.quote_id == quote.id,
                SupplementalPackage.id.not_in(protected_package_ids),
            )
        )
    else:
        await db.execute(
            delete(SupplementalPackage).where(SupplementalPackage.quote_id == quote.id)
        )

    # ---- 3. 价格分项与车辆快照合并（用户编辑字段跳过，不做证据写入）----
    for field_name, value_attr, status_attr in _PRICE_MERGES:
        if locked(field_name):
            continue
        item = getattr(plan.pricing, field_name)
        value = _to_decimal(item.value)
        if value is not None:
            status = PriceItemStatus.INCLUDED
        elif item.status == "NOT_INCLUDED":
            status = PriceItemStatus.NOT_INCLUDED
        else:
            status = PriceItemStatus.UNKNOWN
        setattr(quote, value_attr, value)
        setattr(quote, status_attr, status)

    official = plan.pricing.officialTotal
    if not locked("officialTotal"):
        official_value = _to_decimal(official.value)
        quote.official_total = official_value
        quote.official_total_status = (
            OfficialTotalStatus.INCLUDED if official_value is not None
            else OfficialTotalStatus.UNKNOWN
        )

    for field_name, attr, model_field in _VEHICLE_MERGES:
        if locked(field_name):
            continue
        field_obj = getattr(extraction.vehicle, model_field)
        if field_name == "vehicleModel":
            value = sanitize_text(field_obj.value) if field_obj.value else None
        elif field_name == "firstRegDate":
            value = _normalize_first_reg_date(field_obj.value)
        else:
            value = field_obj.value
        setattr(quote, attr, value)

    # ---- 4. 构建明细候选行 ----
    candidate_coverages, compulsory_evidence, tax_evidence = _build_coverage_rows(
        quote.id, plan, resolver, quote.is_nev
    )
    candidate_services = _build_service_rows(quote.id, plan, resolver)
    candidate_packages = _build_packages(quote.id, plan, resolver)
    candidate_annotations = _build_annotations(quote.id, plan, resolver)

    # 与保留的用户行去重：同码（或同名未识别/同类型服务/同名包/同文标注）
    # 不再重复插入，避免重解析后用户行与候选行成对出现
    kept_codes = {row.code for row in kept_coverages if row.code}
    kept_unrecognized_names = {
        clean_name(row.raw_name) for row in kept_coverages if not row.code
    }
    insert_coverages = [
        row
        for row in candidate_coverages
        if (row.code not in kept_codes if row.code else clean_name(row.raw_name) not in kept_unrecognized_names)
    ]
    kept_service_types = {row.service_type for row in kept_services}
    insert_services = [
        row for row in candidate_services if row.service_type not in kept_service_types
    ]
    kept_package_names = {clean_name(package.name) for package in kept_packages}
    insert_packages = [
        package
        for package in candidate_packages
        if clean_name(package.name) not in kept_package_names
    ]
    kept_annotation_keys = {clean_name(row.content) for row in kept_annotations}
    insert_annotations = [
        row
        for row in candidate_annotations
        if clean_name(row.content) not in kept_annotation_keys
    ]

    # ---- 5. 合并口径的总额三态预演（价格字段置信度输入）----
    all_coverages = kept_coverages + insert_coverages
    package_premiums = [package.premium for package in kept_packages] + [
        package.premium for package in insert_packages
    ]
    tolerance = Decimal(str(settings.total_check_tolerance))
    total_check = _provisional_total_check(
        quote, all_coverages, package_premiums, tolerance
    )

    # ---- 6. 标量证据行（价格/车辆/模型公司；非法来源不建链并降档）----
    for field_name, _value_attr, _status_attr in _PRICE_MERGES:
        item = getattr(plan.pricing, field_name)
        if locked(field_name):
            continue
        value = getattr(quote, _PRICE_VALUE_ATTR[field_name])
        _upsert_evidence(
            db,
            quote_id=quote.id,
            field_name=field_name,
            raw_value=str(value) if value is not None else None,
            resolved=resolver.resolve(item.evidence),
            self_confidence=item.selfConfidence,
            participates_in_total=True,
            total_check_status=total_check,
        )
    if not locked("officialTotal"):
        official_value = quote.official_total
        _upsert_evidence(
            db,
            quote_id=quote.id,
            field_name="officialTotal",
            raw_value=str(official_value) if official_value is not None else None,
            resolved=resolver.resolve(official.evidence),
            self_confidence=official.selfConfidence,
            participates_in_total=False,
            total_check_status=None,
        )
    for field_name, attr, model_field in _VEHICLE_MERGES:
        if locked(field_name):
            continue
        field_obj = getattr(extraction.vehicle, model_field)
        value = getattr(quote, attr)
        _upsert_evidence(
            db,
            quote_id=quote.id,
            field_name=field_name,
            raw_value=None if value is None else str(value),
            resolved=resolver.resolve(field_obj.evidence),
            self_confidence=field_obj.selfConfidence,
            participates_in_total=False,
            total_check_status=None,
        )
    if (
        extraction.insurer.name
        and extraction.insurer.name.strip()
        and not locked(INSURER_MODEL_FIELD)
    ):
        _upsert_evidence(
            db,
            quote_id=quote.id,
            field_name=INSURER_MODEL_FIELD,
            raw_value=sanitize_text(extraction.insurer.name.strip()),
            resolved=resolver.resolve(extraction.insurer.evidence),
            self_confidence=extraction.insurer.selfConfidence,
            participates_in_total=False,
            total_check_status=None,
        )
    # 交强/车船税兜底合并时补充来源定位（值可能来自明细行的原文）
    if compulsory_evidence is not None and not locked("compulsoryPremium"):
        _merge_evidence_location(db, quote.id, "compulsoryPremium", compulsory_evidence)
    if tax_evidence is not None and not locked("vehicleTax"):
        _merge_evidence_location(db, quote.id, "vehicleTax", tax_evidence)

    # ---- 7. 插入候选行并重算价格 ----
    for row in insert_coverages:
        db.add(row)
    for row in insert_services:
        db.add(row)
    for package in insert_packages:
        db.add(package)
    for row in insert_annotations:
        db.add(row)

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
            for row in all_coverages
        ],
        package_premiums=package_premiums,
        discounts=[],
        tolerance=tolerance,
        writer=_QuotePriceWriter(quote),
    )


# 价格字段证据 upsert 需要的“证据字段名 → 报价值属性”映射
_PRICE_VALUE_ATTR = {name: attr for name, attr, _status in _PRICE_MERGES}


def _upsert_evidence(
    db: AsyncSession,
    *,
    quote_id: int,
    field_name: str,
    raw_value: str | None,
    resolved: ResolvedEvidence,
    self_confidence: float | None,
    participates_in_total: bool,
    total_check_status: TotalCheckStatus | None,
) -> None:
    """写入标量字段证据；非法来源不建链并按规则降档（SPEC §5.2、§6.9）。"""
    confidence = synthesize_confidence(
        self_confidence=self_confidence,
        evidence_state=resolved.state,
        participates_in_total=participates_in_total,
        total_check_status=total_check_status.value if total_check_status else None,
    )
    db.add(
        FieldEvidence(
            quote_id=quote_id,
            field_name=field_name,
            raw_value=raw_value,
            # 非法证据绝不伪造 sourceFileId（SPEC §15.2 第 3 条）
            source_file_id=resolved.file_id if resolved.state == "ok" else None,
            source_page=resolved.page if resolved.state == "ok" else None,
            source_text=resolved.text if resolved.state == "ok" else None,
            confidence_level=confidence,
            edited_by_user=False,
        )
    )


async def _merge_evidence_location(
    db: AsyncSession, quote_id: int, field_name: str, resolved: ResolvedEvidence
) -> None:
    """交强/车船税兜底合并后补充来源定位（该行被合并进价格字段）。"""
    if resolved.state != "ok":
        return
    row = (
        await db.execute(
            select(FieldEvidence).where(
                FieldEvidence.quote_id == quote_id,
                FieldEvidence.field_name == field_name,
                FieldEvidence.edited_by_user.is_(False),
            )
        )
    ).scalar_one_or_none()
    if row is not None and row.source_file_id is None:
        row.source_file_id = resolved.file_id
        row.source_page = resolved.page
        row.source_text = resolved.text


def _provisional_total_check(
    quote: Quote,
    coverages: list[QuoteCoverage],
    package_premiums: list[Decimal | None],
    tolerance: Decimal,
) -> TotalCheckStatus:
    """以合并后的候选口径预演总额三态，供价格字段置信度合成使用。

    与最终 recalculate 使用同一组 pricing 纯函数，结果必然一致；
    优惠在候选阶段恒为空（用户未填写），不影响总额三态。
    """
    computed_commercial = compute_commercial_premium(
        [
            CoveragePriceRow(
                category=row.category,
                code=row.code,
                status=row.status,
                premium=row.premium,
                coverage_amount=row.coverage_amount,
                per_seat_amount=row.per_seat_amount,
            )
            for row in coverages
        ]
    )
    computed_package = compute_package_total(package_premiums)
    computed_total = compute_computed_total(
        commercial_status=quote.commercial_status,
        commercial_premium=quote.commercial_premium,
        computed_commercial_premium=computed_commercial,
        compulsory_status=quote.compulsory_status,
        compulsory_premium=quote.compulsory_premium,
        vehicle_tax_status=quote.vehicle_tax_status,
        vehicle_tax=quote.vehicle_tax,
        package_status=quote.package_status,
        package_total=quote.package_total,
        computed_package_total=computed_package,
        other_fees_status=quote.other_fees_status,
        other_fees=quote.other_fees,
    )
    return resolve_total_check_status(computed_total, quote.official_total, tolerance)


# ---- 明细候选行构建（纯函数，不触库）----


def _build_coverage_rows(
    quote_id: int,
    plan: PlanExtraction,
    resolver: EvidenceResolver,
    is_nev: bool | None,
) -> tuple[list[QuoteCoverage], ResolvedEvidence | None, ResolvedEvidence | None]:
    """构建险种候选行；交强险与车船税行合并进价格字段（SPEC §3.1）。

    返回 (候选行列表, 交强证据, 车船税证据)。字典能映射 COMPULSORY 的
    行、以及未识别但名称含“车船税”的行都不生成 quote_coverage。
    """
    rows: list[QuoteCoverage] = []
    compulsory_evidence: ResolvedEvidence | None = None
    tax_evidence: ResolvedEvidence | None = None
    seen_identities: list[RowIdentity] = []

    for item in (*plan.coreCoverages, *plan.additionalCoverages):
        raw_name = sanitize_text(item.rawName.strip())
        code = match_coverage(item.rawName)
        resolved = resolver.resolve(item.evidence)

        # 交强险：只落价格字段（compulsoryPremium），不生成行（SPEC §3.1）
        if code == "COMPULSORY":
            if compulsory_evidence is None:
                compulsory_evidence = resolved
            continue
        # 车船税兜底：字典未收录，避免污染未识别区（落 vehicleTax 字段）
        if code is None and "车船税" in clean_name(raw_name):
            if tax_evidence is None:
                tax_evidence = resolved
            continue

        if code is not None:
            definition = get_coverage_definition(code)
            category = CoverageCategory(definition.category)
            display_name = definition.label
        else:
            category = CoverageCategory.UNRECOGNIZED
            display_name = raw_name

        raw_value = sanitize_text(item.rawValue) if item.rawValue else None
        status = _status_from_model(item.status, is_service=False)
        coverage_amount, per_seat, seat_count, seat_conflict = _seat_fields(
            _to_decimal(item.coverageAmount),
            _to_decimal(item.perSeatAmount),
            item.seatCount,
            raw_value,
        )
        premium = _to_decimal(item.premium)
        if premium is None:
            # 保费缺失时从原文兜底“N 元”（只认显式金额，绝不推断）
            premium = _scan_money_in_text(raw_value)

        confidence = synthesize_confidence(
            self_confidence=item.selfConfidence,
            evidence_state=resolved.state,
            unrecognized=code is None,
            range_hint=check_amount_range(code, coverage_amount) is not None,
            nev_inconsistent=nev_inconsistent(is_nev, item.rawName),
        )
        if seat_conflict:
            confidence = ConfidenceLevel.LOW

        row = QuoteCoverage(
            quote_id=quote_id,
            code=code,
            category=category,
            raw_name=raw_name,
            raw_value=raw_value,
            name=display_name,
            status=status,
            coverage_amount=coverage_amount,
            per_seat_amount=per_seat,
            seat_count=seat_count,
            shared_coverage=item.sharedCoverage,
            premium=premium,
            multiplier=_normalize_multiplier(item.multiplier, item.rawValue),
            condition=(
                normalize_condition(sanitize_text(item.condition))
                if item.condition
                else None
            ),
            description=(
                sanitize_evidence_text(item.description) if item.description else None
            ),
            source_file_id=resolved.file_id if resolved.state == "ok" else None,
            source_page=resolved.page if resolved.state == "ok" else None,
            source_text=resolved.text if resolved.state == "ok" else None,
            confidence_level=confidence,
            edited_by_user=False,
        )
        # 严格重复去重（SPEC §6.4）：rawName/rawValue/保额/保费/证据全部
        # 相同才丢弃；同码不同内容的行保留交由用户确认
        identity = RowIdentity(
            raw_name=row.raw_name,
            raw_value=row.raw_value,
            coverage_amount=coverage_amount,
            premium=premium,
            evidence_key=resolved.key,
        )
        if any(identity == previous for previous in seen_identities):
            continue
        seen_identities.append(identity)
        rows.append(row)

    for unmatched in plan.unmatchedItems:
        raw_text = sanitize_text(unmatched.rawText.strip())
        resolved = resolver.resolve(unmatched.evidence)
        # 未识别金额项：金额落到 premium（钱字段）以阻断 computed 商业险，
        # 直到用户映射或丢弃（SPEC §4.1 unmatchedItems 要点）
        rows.append(
            QuoteCoverage(
                quote_id=quote_id,
                code=None,
                category=CoverageCategory.UNRECOGNIZED,
                raw_name=raw_text,
                raw_value=None,
                name=raw_text,
                status=ItemStatus.UNKNOWN,
                premium=_scan_money_in_text(raw_text),
                description=(
                    sanitize_evidence_text(unmatched.reason)
                    if unmatched.reason
                    else None
                ),
                source_file_id=resolved.file_id if resolved.state == "ok" else None,
                source_page=resolved.page if resolved.state == "ok" else None,
                source_text=resolved.text if resolved.state == "ok" else None,
                confidence_level=synthesize_confidence(
                    self_confidence=unmatched.selfConfidence,
                    evidence_state=resolved.state,
                    unrecognized=True,
                ),
                edited_by_user=False,
            )
        )
    return rows, compulsory_evidence, tax_evidence


def _build_service_rows(
    quote_id: int,
    plan: PlanExtraction,
    resolver: EvidenceResolver,
) -> list[QuoteService]:
    """增值服务候选行：类型映射 + FREE/UNKNOWN 状态语义（SPEC §6.6/§12）。

    - 明确 0 元（模型值或原文“0元”）→ FREE；
    - 费用缺失 → UNKNOWN（即使模型称 INCLUDED，也不推断免费/费用）；
    - 模型标 FREE 但费用缺失或非 0 → 降为 UNKNOWN。
    """
    rows: list[QuoteService] = []
    for item in plan.services:
        raw_name = sanitize_text(item.rawName.strip())
        raw_value = sanitize_text(item.rawValue) if item.rawValue else None
        status = _status_from_model(item.status, is_service=True)
        cost = _to_decimal(item.cost)
        if cost is None:
            cost = _scan_money_in_text(raw_value)
        status = resolve_service_status(status, cost)
        if cost is None and status == ItemStatus.INCLUDED:
            status = ItemStatus.UNKNOWN
        resolved = resolver.resolve(item.evidence)
        rows.append(
            QuoteService(
                quote_id=quote_id,
                service_type=ServiceType(match_service(item.rawName)),
                status=status,
                count=item.count,
                cost=cost,
                description=(
                    sanitize_evidence_text(item.description)
                    if item.description
                    else None
                ),
                raw_name=raw_name,
                raw_value=raw_value,
                source_file_id=resolved.file_id if resolved.state == "ok" else None,
                source_page=resolved.page if resolved.state == "ok" else None,
                source_text=resolved.text if resolved.state == "ok" else None,
                confidence_level=synthesize_confidence(
                    self_confidence=item.selfConfidence,
                    evidence_state=resolved.state,
                ),
                edited_by_user=False,
            )
        )
    return rows


def _build_packages(
    quote_id: int,
    plan: PlanExtraction,
    resolver: EvidenceResolver,
) -> list[SupplementalPackage]:
    """独立保障包候选（含内部保障）：类型/单位/倍数归一化。

    隔离铁律：包内驾乘类保障只落 package_coverage，结构性上不可能写入
    quote_coverage 的司机/乘客责任险（SPEC §2.6）。
    保费：模型值优先，缺失时从 rawValue 兜底“N 元”（只认显式金额）。
    """
    packages: list[SupplementalPackage] = []
    for item in plan.supplementalPackages:
        resolved = resolver.resolve(item.evidence)
        premium = _to_decimal(item.premium)
        raw_value = sanitize_text(item.rawValue) if item.rawValue else None
        if premium is None:
            premium = _scan_money_in_text(raw_value)
        package = SupplementalPackage(
            quote_id=quote_id,
            name=sanitize_text(item.name.strip()),
            raw_name=sanitize_text(item.rawName) if item.rawName else None,
            raw_value=raw_value,
            premium=premium,
            description=(
                sanitize_evidence_text(item.description) if item.description else None
            ),
            source_file_id=resolved.file_id if resolved.state == "ok" else None,
            source_page=resolved.page if resolved.state == "ok" else None,
            source_text=resolved.text if resolved.state == "ok" else None,
            confidence_level=synthesize_confidence(
                self_confidence=item.selfConfidence,
                evidence_state=resolved.state,
            ),
            edited_by_user=False,
        )
        for coverage_item in item.coverages:
            coverage_resolved = resolver.resolve(coverage_item.evidence)
            type_invalid = (
                coverage_item.type is not None
                and coverage_item.type not in PACKAGE_COVERAGE_DEFINITIONS
            )
            # 模型类型码必须命中 §3.3 码表；非法或缺失按原文关键词归一
            # （仍未命中则 OTHER），非法类型码额外降为 MEDIUM（§4.1 要点）
            type_code = coverage_item.type
            if type_code not in PACKAGE_COVERAGE_DEFINITIONS:
                type_code = match_package_type(coverage_item.rawText or coverage_item.name)
            unit = coverage_item.unit
            if unit not in ("CNY", "TIMES", "DAYS", "OTHER"):
                # 单位非法或缺失：有金额按 CNY，否则 OTHER，不臆测换算
                unit = "CNY" if coverage_item.coverageAmount is not None else "OTHER"
            coverage_amount, per_seat, seat_count, seat_conflict = _seat_fields(
                _to_decimal(coverage_item.coverageAmount),
                _to_decimal(coverage_item.perSeatAmount),
                coverage_item.seatCount,
                sanitize_text(coverage_item.rawText) if coverage_item.rawText else None,
            )
            confidence = synthesize_confidence(
                self_confidence=coverage_item.selfConfidence,
                evidence_state=coverage_resolved.state,
                other_medium_hint=type_invalid,
            )
            if seat_conflict:
                confidence = ConfidenceLevel.LOW
            package.coverages.append(
                PackageCoverage(
                    type=type_code,
                    name=(
                        sanitize_text(coverage_item.name.strip())
                        if coverage_item.name
                        else None
                    ),
                    status=_status_from_model(coverage_item.status, is_service=False),
                    coverage_amount=coverage_amount,
                    unit=PackageUnit(unit),
                    per_seat_amount=per_seat,
                    seat_count=seat_count,
                    shared=coverage_item.shared,
                    multiplier=_normalize_multiplier(
                        coverage_item.multiplier, coverage_item.rawText
                    ),
                    condition=(
                        normalize_condition(sanitize_text(coverage_item.condition))
                        if coverage_item.condition
                        else None
                    ),
                    description=(
                        sanitize_evidence_text(coverage_item.description)
                        if coverage_item.description
                        else None
                    ),
                    raw_text=(
                        sanitize_evidence_text(coverage_item.rawText)
                        if coverage_item.rawText
                        else None
                    ),
                    source_file_id=(
                        coverage_resolved.file_id
                        if coverage_resolved.state == "ok"
                        else None
                    ),
                    source_page=(
                        coverage_resolved.page
                        if coverage_resolved.state == "ok"
                        else None
                    ),
                    source_text=(
                        coverage_resolved.text
                        if coverage_resolved.state == "ok"
                        else None
                    ),
                    confidence_level=confidence,
                    edited_by_user=False,
                )
            )
        packages.append(package)
    return packages


def _build_annotations(
    quote_id: int,
    plan: PlanExtraction,
    resolver: EvidenceResolver,
) -> list[SalesAnnotation]:
    """销售标注候选行：kind 只接受 §3.4 枚举，非法值统一 OTHER。

    隔离铁律：标注默认不参与任何结构化对比与金额计算——本函数产出的行
    只进 sales_annotation 表，写入路径与价格/明细完全隔离。
    """
    rows: list[SalesAnnotation] = []
    for item in plan.annotations:
        resolved = resolver.resolve(item.evidence)
        try:
            kind = AnnotationKind(item.kind) if item.kind else AnnotationKind.OTHER
        except ValueError:
            kind = AnnotationKind.OTHER
        rows.append(
            SalesAnnotation(
                quote_id=quote_id,
                content=sanitize_text(item.content.strip()),
                kind=kind,
                source_type=AnnotationSourceType.SALES_ANNOTATION,
                source_file_id=resolved.file_id if resolved.state == "ok" else None,
                source_page=resolved.page if resolved.state == "ok" else None,
                # 标注表无置信度列（SPEC §2.7）；证据仅用于来源定位展示
                edited_by_user=False,
            )
        )
    return rows


async def _load_kept(model, quote_id: int, db: AsyncSession) -> list:  # noqa: ANN001
    """删除前快照：该层用户编辑过的行（重解析必须保留）。"""
    return list(
        (
            await db.execute(
                select(model).where(
                    model.quote_id == quote_id, model.edited_by_user.is_(True)
                )
            )
        ).scalars()
    )


async def _load_kept_packages(
    db: AsyncSession, quote_id: int
) -> tuple[list[SupplementalPackage], list[PackageCoverage]]:
    """取整包保护范围内的包与包内被编辑的行。"""
    packages = list(
        (
            await db.execute(
                select(SupplementalPackage).where(
                    SupplementalPackage.quote_id == quote_id,
                    SupplementalPackage.edited_by_user.is_(True),
                )
            )
        ).scalars()
    )
    edited_rows = list(
        (
            await db.execute(
                select(PackageCoverage)
                .join(
                    SupplementalPackage,
                    SupplementalPackage.id == PackageCoverage.package_id,
                )
                .where(
                    SupplementalPackage.quote_id == quote_id,
                    PackageCoverage.edited_by_user.is_(True),
                )
            )
        ).scalars()
    )
    return packages, edited_rows
