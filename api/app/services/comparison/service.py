"""对比服务（TASK-06）：加载已确认报价快照并组装对比结果。

职责边界：
- 本模块只做“加载 + 投影”：把 ORM 报价装配成引擎输入快照；
- MERGE_REVIEW 报价的 ORM 明细行就是尚未被候选变更覆盖的已确认旧值
  （merge_change 独立存放，解决时才落业务表），直接读取即为旧确认值，
  候选 merge_change 绝不泄漏进对比结果；
- 一次 select + selectinload 带全部明细，避免 N+1（6×200 明细 P95 < 500ms）。
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import QuoteNotFoundError, ValidationError
from app.models import Quote, SupplementalPackage
from app.models.enums import (
    CoverageCategory,
    ItemStatus,
    QuoteStatus,
)
from app.schemas.compare import ComparisonResult
from app.services.comparison.engine import (
    CoverageSnapshot,
    DiscountSnapshot,
    PackageCoverageSnapshot,
    PackageSnapshot,
    QuoteSnapshot,
    ServiceSnapshot,
    build_comparison,
)
from app.services.pricing import effective_price_item

# 可对比状态：MERGE_REVIEW 读取旧确认值（SPEC §7.1）
COMPARABLE_STATUSES: frozenset[QuoteStatus] = frozenset(
    {QuoteStatus.CONFIRMED, QuoteStatus.MERGE_REVIEW}
)

# 对比数量上限（SPEC §12：>6 提示分批）
MIN_COMPARE_QUOTES = 2
MAX_COMPARE_QUOTES = 6

# 未识别行参与“金额项”判定的字段（与 pricing 服务口径一致）
_UNRECOGNIZED_MONEY_FIELDS = ("premium", "coverage_amount", "per_seat_amount")

# 服务代表行优先级：已包含/免费的服务行优先于未知/不包含行
_SERVICE_PREFERRED_STATUSES = frozenset({ItemStatus.INCLUDED, ItemStatus.FREE})


def parse_quote_ids(raw: str) -> list[int]:
    """解析并校验 quoteIds 查询参数（语义化错误码，前端可直接提示）。"""
    text = (raw or "").strip()
    if not text:
        raise ValidationError("请先勾选要对比的报价", code="COMPARE_QUOTES_REQUIRED")
    parts = [part.strip() for part in text.split(",") if part.strip()]
    ids: list[int] = []
    for part in parts:
        if not part.isdigit():
            raise ValidationError(
                "quoteIds 参数格式不正确，应为英文逗号分隔的报价编号",
                code="COMPARE_QUOTES_INVALID",
            )
        ids.append(int(part))
    if len(set(ids)) != len(ids):
        raise ValidationError("报价不能重复选择", code="COMPARE_QUOTES_DUPLICATED")
    if len(ids) < MIN_COMPARE_QUOTES:
        raise ValidationError("至少选择 2 个报价才能对比", code="COMPARE_TOO_FEW")
    if len(ids) > MAX_COMPARE_QUOTES:
        raise ValidationError(
            f"最多同时对比 {MAX_COMPARE_QUOTES} 个报价，请分批对比",
            code="COMPARE_TOO_MANY",
        )
    return ids


def quote_display_name(quote: Quote) -> str:
    """方案展示名：方案标签优先，其次“公司名·保险员”，最后公司名。"""
    if quote.plan_label:
        return quote.plan_label
    if quote.agent_name:
        return f"{quote.insurer_name}·{quote.agent_name}"
    return quote.insurer_name


def _representative_coverage(rows: list) -> CoverageSnapshot | None:  # noqa: ANN001 - QuoteCoverage 行
    """多条同码险种行的代表行：已包含且保额最大者优先，其次保额最大。

    对比只取“最能代表该险种”的一行；重复行明细属于确认页范畴，
    不进入结构化分区（SPEC §7 实施范围第 5 条按维度比较，不按行数）。
    """
    if not rows:
        return None
    first = rows[0]

    def amount_key(row) -> tuple[int, Decimal]:  # noqa: ANN001
        amount = row.coverage_amount or Decimal("0")
        return (1 if row.status == ItemStatus.INCLUDED else 0, amount)

    best = max(rows, key=amount_key)
    return CoverageSnapshot(
        code=first.code or "",
        name=first.name,
        status=best.status,
        coverage_amount=best.coverage_amount,
        per_seat_amount=best.per_seat_amount,
        seat_count=best.seat_count,
        shared_coverage=best.shared_coverage,
        premium=best.premium,
        multiplier=best.multiplier,
        condition=best.condition,
    )


def _coverage_maps(
    quote: Quote,
) -> tuple[dict[str, CoverageSnapshot], dict[str, CoverageSnapshot], int]:
    """按类别分组险种行 → 标准码映射；同时统计保留的未识别金额项数量。

    未识别金额项不进入结构化分区，只把数量交给第五问提示（SPEC §7.2）。
    """
    core: dict[int, list] = {}  # noqa: ANN001
    additional: dict[int, list] = {}  # noqa: ANN001
    unrecognized_money = 0
    for row in quote.coverages:
        if row.category == CoverageCategory.CORE and row.code:
            core.setdefault(row.code, []).append(row)
        elif row.category == CoverageCategory.ADDITIONAL and row.code:
            additional.setdefault(row.code, []).append(row)
        elif row.category == CoverageCategory.UNRECOGNIZED:
            # 用户已明确“不包含”的未识别项视为已处理，不计入提示数量
            kept = row.status != ItemStatus.NOT_INCLUDED
            has_money = any(
                getattr(row, field) is not None for field in _UNRECOGNIZED_MONEY_FIELDS
            )
            if kept and has_money:
                unrecognized_money += 1
    core_map = {
        code: snapshot
        for code, rows in core.items()
        if (snapshot := _representative_coverage(rows)) is not None
    }
    additional_map = {
        code: snapshot
        for code, rows in additional.items()
        if (snapshot := _representative_coverage(rows)) is not None
    }
    return core_map, additional_map, unrecognized_money


def _service_map(quote: Quote) -> dict[str, ServiceSnapshot]:
    """按服务类型归并服务行（同类型多行时取已包含/免费行优先）。"""
    grouped: dict[str, list] = {}  # noqa: ANN001
    for row in quote.services:
        grouped.setdefault(row.service_type.value, []).append(row)
    result: dict[str, ServiceSnapshot] = {}
    for type_code, rows in grouped.items():

        def priority(row) -> tuple[int, int]:  # noqa: ANN001
            return (1 if row.status in _SERVICE_PREFERRED_STATUSES else 0, row.id)

        best = max(rows, key=priority)
        result[type_code] = ServiceSnapshot(
            service_type=type_code,
            status=best.status,
            count=best.count,
            cost=best.cost,
        )
    return result


def _package_snapshots(quote: Quote) -> list[PackageSnapshot]:
    """保障包列表（保持 id 顺序），内部保障按 id 顺序展开。"""
    result: list[PackageSnapshot] = []
    for package in sorted(quote.packages, key=lambda p: p.id):
        result.append(
            PackageSnapshot(
                name=package.name,
                premium=package.premium,
                coverages=[
                    PackageCoverageSnapshot(
                        type=item.type,
                        name=item.name,
                        status=item.status,
                        coverage_amount=item.coverage_amount,
                        unit=item.unit.value if item.unit else None,
                        multiplier=item.multiplier,
                        condition=item.condition,
                    )
                    for item in sorted(package.coverages, key=lambda c: c.id)
                ],
            )
        )
    return result


def to_snapshot(quote: Quote) -> QuoteSnapshot:
    """把已完整加载的 ORM 报价投影为引擎输入快照（只读，不改任何数据）。"""
    core_map, additional_map, unrecognized_money = _coverage_maps(quote)
    has_user_valuation = any(
        discount.include_in_net and discount.cash_equivalent is not None
        for discount in quote.discounts
    )
    return QuoteSnapshot(
        quote_id=quote.id,
        display_name=quote_display_name(quote),
        insurer_code=quote.insurer_code,
        insurer_name=quote.insurer_name,
        agent_name=quote.agent_name,
        plan_label=quote.plan_label,
        status=quote.status,
        # 五个分项 eff 值复用 pricing 服务口径：显示值优先、计算值回退、
        # NOT_INCLUDED 按 0、UNKNOWN/缺失为 None（与其他 Task 零漂移）
        commercial_eff=effective_price_item(
            quote.commercial_status,
            quote.commercial_premium,
            quote.computed_commercial_premium,
        ),
        compulsory_eff=effective_price_item(
            quote.compulsory_status, quote.compulsory_premium, None
        ),
        vehicle_tax_eff=effective_price_item(
            quote.vehicle_tax_status, quote.vehicle_tax, None
        ),
        package_eff=effective_price_item(
            quote.package_status, quote.package_total, quote.computed_package_total
        ),
        other_fees_eff=effective_price_item(
            quote.other_fees_status, quote.other_fees, None
        ),
        commercial_status=quote.commercial_status,
        compulsory_status=quote.compulsory_status,
        vehicle_tax_status=quote.vehicle_tax_status,
        package_status=quote.package_status,
        other_fees_status=quote.other_fees_status,
        official_total=quote.official_total,
        computed_total=quote.computed_total,
        total_check_status=quote.total_check_status,
        net_payment=quote.net_payment,
        net_payment_status=quote.net_payment_status,
        computed_commercial_premium=quote.computed_commercial_premium,
        has_user_valuation=has_user_valuation,
        core=core_map,
        additional=additional_map,
        services=_service_map(quote),
        packages=_package_snapshots(quote),
        discounts=[
            DiscountSnapshot(
                discount_type=d.discount_type.value,
                description=d.description,
                amount=d.amount,
                cash_equivalent=d.cash_equivalent,
                include_in_net=d.include_in_net,
            )
            for d in sorted(quote.discounts, key=lambda row: row.id)
        ],
        unrecognized_money_count=unrecognized_money,
    )


async def load_snapshots(
    db: AsyncSession, project_id: int, quote_ids: list[int]
) -> list[QuoteSnapshot]:
    """加载并校验报价归属/状态，按用户传入顺序返回快照。

    校验口径（SPEC §10 / TASK-06 验证 3）：
    - 不存在的报价 → 404 QUOTE_NOT_FOUND；
    - 属于其他项目 → 422 QUOTE_NOT_IN_PROJECT；
    - 非可对比状态 → 422 QUOTE_NOT_COMPARABLE。
    """
    stmt = (
        select(Quote)
        .where(Quote.id.in_(quote_ids))
        .options(
            selectinload(Quote.coverages),
            selectinload(Quote.services),
            selectinload(Quote.packages).selectinload(SupplementalPackage.coverages),
            selectinload(Quote.discounts),
        )
    )
    found = {quote.id: quote for quote in (await db.execute(stmt)).scalars().all()}
    snapshots: list[QuoteSnapshot] = []
    for quote_id in quote_ids:
        quote = found.get(quote_id)
        if quote is None:
            raise QuoteNotFoundError()
        if quote.project_id != project_id:
            raise ValidationError(
                "所选报价不属于当前项目", code="QUOTE_NOT_IN_PROJECT"
            )
        if quote.status not in COMPARABLE_STATUSES:
            raise ValidationError(
                "仅已确认或合并确认中的报价可参与对比，请先完成确认",
                code="QUOTE_NOT_COMPARABLE",
            )
        snapshots.append(to_snapshot(quote))
    return snapshots


async def build_project_comparison(
    db: AsyncSession, project_id: int, quote_ids: list[int]
) -> ComparisonResult:
    """对比入口：加载快照 → 纯规则引擎总装（不修改任何报价数据）。"""
    snapshots = await load_snapshots(db, project_id, quote_ids)
    return build_comparison(snapshots, project_id)
