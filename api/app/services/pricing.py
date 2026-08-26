"""确定性价格服务（SPEC §2.2 / §6.1）。

业务不变量：
- 任何 null 都不得自行当 0：UNKNOWN 分项使 computedTotal 不可计算；
  NOT_INCLUDED 分项按 0 参与合计（用户已明确“不包含”）；
- 显示值优先、计算值回退：eff(x) = 用户确认值 x（存在时，否则 computedX）；
- 总价三态校验：只有 computedTotal 与 officialTotal 都非空才校验，
  容差默认 0.50 元（吸收四舍五入），无法校验是 NOT_CHECKABLE 而非“通过”；
- 净支出 = (officialTotal ?? computedTotal) − Σ(勾选计入且含折现值的优惠)；
  名义金额 amount 仅展示、绝不参与计算；优惠超额时 netPayment=null 并标记
  INVALID_DISCOUNT，不自动截断为 0；
- computedCommercialPremium 只有在“正式商业险都已归类且保费完整”时才计算，
  存在含金额的未识别项时保持 null，直到用户映射或丢弃；
- computedPackageTotal 只有在“全部保障包价格完整”时才计算。

全部为纯函数（可独立单测），由 quote_service 在同一事务内调用落库。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.models.enums import (
    CoverageCategory,
    ItemStatus,
    NetPaymentStatus,
    PriceItemStatus,
    TotalCheckStatus,
)
from app.services.normalization.amounts import TWO_PLACES

# 参与“含金额未识别项”判定的字段：任一非空即视为金额项
_MONEY_FIELDS = ("premium", "coverage_amount", "per_seat_amount")
# 明确“不投保/不适用”的险种行保费按 0 参与商业险合计
_ZERO_PREMIUM_STATUSES = (ItemStatus.NOT_INCLUDED, ItemStatus.NOT_APPLICABLE)


@dataclass(frozen=True)
class CoveragePriceRow:
    """compute_commercial_premium 所需的险种行字段投影。"""

    category: CoverageCategory
    code: str | None
    status: ItemStatus
    premium: Decimal | None
    coverage_amount: Decimal | None
    per_seat_amount: Decimal | None


@dataclass(frozen=True)
class DiscountValueRow:
    """净支出计算所需的优惠字段投影（名义金额不参与）。"""

    include_in_net: bool
    cash_equivalent: Decimal | None


def compute_commercial_premium(rows: Sequence[CoveragePriceRow]) -> Decimal | None:
    """计算商业险合计候选值；不满足前提时返回 None。

    前提（SPEC §2.2）：
    1. 至少存在一行已归类的正式商业险（CORE/ADDITIONAL）——空集合不能算 0；
    2. 每行保费完整，或该行已明确“不包含/不适用”（按 0）；
    3. 不存在“尚未处理且含金额”的未识别行（会阻断，直到映射或丢弃）。
    """
    formal_rows = [row for row in rows if row.category != CoverageCategory.UNRECOGNIZED]
    if not formal_rows:
        return None

    total = Decimal("0")
    for row in formal_rows:
        if row.premium is not None:
            total += row.premium
        elif row.status in _ZERO_PREMIUM_STATUSES:
            continue
        else:
            # 保费缺失且未明确不包含：不能当 0，整体不可计算
            return None

    for row in rows:
        if row.category != CoverageCategory.UNRECOGNIZED:
            continue
        if row.status == ItemStatus.NOT_INCLUDED:
            # 用户已明确该未识别项不包含，不再阻断
            continue
        if any(getattr(row, field) is not None for field in _MONEY_FIELDS):
            return None
    return total.quantize(TWO_PLACES)


def compute_package_total(package_premiums: Sequence[Decimal | None]) -> Decimal | None:
    """保障包合计：至少一个包且全部保费完整时才计算，否则 None。"""
    if not package_premiums:
        return None
    if any(premium is None for premium in package_premiums):
        return None
    return sum(package_premiums, start=Decimal("0")).quantize(TWO_PLACES)


def effective_price_item(
    status: PriceItemStatus,
    value: Decimal | None,
    computed: Decimal | None,
) -> Decimal | None:
    """单个价格分项的有效值（eff(x)，SPEC §6.1）。

    - NOT_INCLUDED → 0（用户已明确确认不包含）；
    - 其余（INCLUDED/UNKNOWN）→ 用户确认值优先，缺失回退计算值；
    - 两者皆空 → None（不可计算，禁止当 0）。
    """
    if status == PriceItemStatus.NOT_INCLUDED:
        return Decimal("0")
    if value is not None:
        return value
    if computed is not None:
        return computed
    return None


def compute_computed_total(
    *,
    commercial_status: PriceItemStatus,
    commercial_premium: Decimal | None,
    computed_commercial_premium: Decimal | None,
    compulsory_status: PriceItemStatus,
    compulsory_premium: Decimal | None,
    vehicle_tax_status: PriceItemStatus,
    vehicle_tax: Decimal | None,
    package_status: PriceItemStatus,
    package_total: Decimal | None,
    computed_package_total: Decimal | None,
    other_fees_status: PriceItemStatus,
    other_fees: Decimal | None,
) -> Decimal | None:
    """computedTotal：五个必需分项的 eff 之和；任一分项不可计算则整体为 None。

    交强险/车船税/其他费用没有系统计算值（只有用户确认值）。
    """
    parts = (
        effective_price_item(
            commercial_status, commercial_premium, computed_commercial_premium
        ),
        effective_price_item(compulsory_status, compulsory_premium, None),
        effective_price_item(vehicle_tax_status, vehicle_tax, None),
        effective_price_item(package_status, package_total, computed_package_total),
        effective_price_item(other_fees_status, other_fees, None),
    )
    if any(part is None for part in parts):
        return None
    return sum(parts, start=Decimal("0")).quantize(TWO_PLACES)


def resolve_total_check_status(
    computed_total: Decimal | None,
    official_total: Decimal | None,
    tolerance: Decimal,
) -> TotalCheckStatus:
    """总价三态校验：两个总价都非空才可比；|差| ≤ 容差 → PASSED。

    边界：差值恰好等于容差算 PASSED（“≤”语义，吸收四舍五入）。
    """
    if computed_total is None or official_total is None:
        return TotalCheckStatus.NOT_CHECKABLE
    if abs(computed_total - official_total) <= tolerance:
        return TotalCheckStatus.PASSED
    return TotalCheckStatus.MISMATCH


def resolve_net_payment(
    official_total: Decimal | None,
    computed_total: Decimal | None,
    discounts: Sequence[DiscountValueRow],
) -> tuple[Decimal | None, NetPaymentStatus]:
    """净支出与状态（SPEC §2.7）。

    基准 = officialTotal ?? computedTotal；仅勾选计入且折现值非空的优惠扣减。
    - 基准缺失 → (None, MISSING_TOTAL)；
    - 折现合计 > 基准 → (None, INVALID_DISCOUNT)，不截断为 0；
    - 恰好等于基准 → 净支出 0，状态 OK（“大于”才是超额）。
    """
    base = official_total if official_total is not None else computed_total
    if base is None:
        return None, NetPaymentStatus.MISSING_TOTAL
    deduction = sum(
        (
            discount.cash_equivalent
            for discount in discounts
            if discount.include_in_net and discount.cash_equivalent is not None
        ),
        start=Decimal("0"),
    )
    if deduction > base:
        return None, NetPaymentStatus.INVALID_DISCOUNT
    return (base - deduction).quantize(TWO_PLACES), NetPaymentStatus.OK


@dataclass(frozen=True)
class QuotePriceInput:
    """recalculate_quote 需要的报价价格字段投影（与 Quote 列同名）。"""

    commercial_status: PriceItemStatus
    commercial_premium: Decimal | None
    compulsory_status: PriceItemStatus
    compulsory_premium: Decimal | None
    vehicle_tax_status: PriceItemStatus
    vehicle_tax: Decimal | None
    package_status: PriceItemStatus
    package_total: Decimal | None
    other_fees_status: PriceItemStatus
    other_fees: Decimal | None
    official_total: Decimal | None


class _QuotePriceWriter:
    """把纯函数结果写回 ORM Quote 的桥接，仅暴露需要的字段赋值。"""

    def __init__(self, quote) -> None:  # noqa: ANN001 - ORM Quote，避免循环导入
        self._quote = quote

    def set_computed_commercial(self, value: Decimal | None) -> None:
        self._quote.computed_commercial_premium = value

    def set_computed_package(self, value: Decimal | None) -> None:
        self._quote.computed_package_total = value

    def set_computed_total(self, value: Decimal | None) -> None:
        self._quote.computed_total = value

    def set_total_check(self, value: TotalCheckStatus) -> None:
        self._quote.total_check_status = value

    def set_net_payment(self, amount: Decimal | None, status: NetPaymentStatus) -> None:
        self._quote.net_payment = amount
        self._quote.net_payment_status = status


def recalculate_quote(
    quote_price: QuotePriceInput,
    *,
    coverages: Sequence[CoveragePriceRow],
    package_premiums: Sequence[Decimal | None],
    discounts: Sequence[DiscountValueRow],
    tolerance: Decimal,
    writer: _QuotePriceWriter | None = None,
) -> tuple[Decimal | None, TotalCheckStatus, Decimal | None, NetPaymentStatus]:
    """对一份报价执行完整重算，并（提供 writer 时）写回 ORM 字段。

    返回 (computedTotal, totalCheckStatus, netPayment, netPaymentStatus)，
    便于测试断言与服务层复用。
    """
    computed_commercial = compute_commercial_premium(coverages)
    computed_package = compute_package_total(package_premiums)
    computed_total = compute_computed_total(
        commercial_status=quote_price.commercial_status,
        commercial_premium=quote_price.commercial_premium,
        computed_commercial_premium=computed_commercial,
        compulsory_status=quote_price.compulsory_status,
        compulsory_premium=quote_price.compulsory_premium,
        vehicle_tax_status=quote_price.vehicle_tax_status,
        vehicle_tax=quote_price.vehicle_tax,
        package_status=quote_price.package_status,
        package_total=quote_price.package_total,
        computed_package_total=computed_package,
        other_fees_status=quote_price.other_fees_status,
        other_fees=quote_price.other_fees,
    )
    total_check = resolve_total_check_status(
        computed_total, quote_price.official_total, tolerance
    )
    net_amount, net_status = resolve_net_payment(
        quote_price.official_total, computed_total, discounts
    )
    if writer is not None:
        writer.set_computed_commercial(computed_commercial)
        writer.set_computed_package(computed_package)
        writer.set_computed_total(computed_total)
        writer.set_total_check(total_check)
        writer.set_net_payment(net_amount, net_status)
    return computed_total, total_check, net_amount, net_status
