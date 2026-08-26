"""价格与数值规则纯函数单测（TASK-02 验证第 1 条）。

覆盖金额换算、座位总额、FREE/UNKNOWN/NOT_INCLUDED 状态语义、
总额 PASSED/MISMATCH/NOT_CHECKABLE 三态、正常净支出、无折现优惠、
优惠超额——每类至少一个正常例和一个边界例；任何 null 不得当 0。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ValidationError
from app.models.enums import (
    CoverageCategory,
    ItemStatus,
    NetPaymentStatus,
    PriceItemStatus,
    TotalCheckStatus,
)
from app.services.normalization.amounts import (
    RowIdentity,
    check_amount_range,
    derive_item_status,
    is_duplicate_row,
    parse_cn_amount,
    parse_seat_expression,
    resolve_seat_amounts,
)
from app.services.pricing import (
    CoveragePriceRow,
    DiscountValueRow,
    compute_commercial_premium,
    compute_computed_total,
    compute_package_total,
    effective_price_item,
    resolve_net_payment,
    resolve_total_check_status,
)

D = Decimal
TOLERANCE = D("0.50")


def _row(
    category: CoverageCategory = CoverageCategory.CORE,
    code: str | None = "THIRD_PARTY_LIABILITY",
    status: ItemStatus = ItemStatus.INCLUDED,
    premium: Decimal | None = None,
    coverage_amount: Decimal | None = None,
    per_seat: Decimal | None = None,
) -> CoveragePriceRow:
    return CoveragePriceRow(
        category=category,
        code=code,
        status=status,
        premium=premium,
        coverage_amount=coverage_amount,
        per_seat_amount=per_seat,
    )


# ---- 金额换算（元/万、千分位）----


class TestParseCnAmount:
    def test_normal_wan_and_plain(self) -> None:
        assert parse_cn_amount("300万") == D("3000000")
        assert parse_cn_amount("300万元") == D("3000000")
        assert parse_cn_amount("0.1万") == D("1000")
        assert parse_cn_amount("1045") == D("1045")
        assert parse_cn_amount("1,237.41元") == D("1237.41")

    def test_boundary_unparseable_returns_none(self) -> None:
        # 复合表达式/空值/非数字都不能猜：一律 None
        assert parse_cn_amount("0.1万/座×4") is None
        assert parse_cn_amount("") is None
        assert parse_cn_amount(None) is None
        assert parse_cn_amount("面议") is None


class TestSeatRules:
    def test_parse_seat_expression_both_orders(self) -> None:
        assert parse_seat_expression("0.1万/座 × 4") == (D("1000"), 4)
        assert parse_seat_expression("4座 × 10000元") == (D("10000"), 4)
        assert parse_seat_expression("0.1万元/座×4") == (D("1000"), 4)

    def test_parse_invalid_returns_none(self) -> None:
        assert parse_seat_expression("300万") is None
        assert parse_seat_expression(None) is None

    def test_resolve_autofills_total(self) -> None:
        # 未填总额时自动推导：总额 = 单座 × 座位（SPEC §6.3）
        assert resolve_seat_amounts(D("1000"), 4, None) == D("4000")

    def test_resolve_accepts_consistent_total(self) -> None:
        assert resolve_seat_amounts(D("0.1") * 10000, 4, D("4000")) == D("4000")

    def test_resolve_rejects_inconsistent_total(self) -> None:
        with pytest.raises(ValidationError, match="单座 × 座位"):
            resolve_seat_amounts(D("1000"), 4, D("5000"))

    def test_resolve_without_seat_inputs_passthrough(self) -> None:
        # 单座或座位缺失时不推导，缺失不得自行当 0
        assert resolve_seat_amounts(None, 4, D("4000")) == D("4000")
        assert resolve_seat_amounts(D("1000"), None, None) is None


# ---- 状态语义（FREE / UNKNOWN / NOT_INCLUDED）----


class TestDeriveItemStatus:
    def test_explicit_negative_is_not_included(self) -> None:
        assert derive_item_status("不投保该附加险") == ItemStatus.NOT_INCLUDED

    def test_blank_and_placeholder_are_unknown(self) -> None:
        assert derive_item_status(None) == ItemStatus.UNKNOWN
        assert derive_item_status("  ") == ItemStatus.UNKNOWN
        # “—”在语义不明确时必须是 UNKNOWN，不能猜成不包含
        assert derive_item_status("—") == ItemStatus.UNKNOWN

    def test_service_zero_cost_is_free_coverage_zero_premium_is_included(self) -> None:
        # 只有服务明确 0 元才是 FREE；险种 0 元保费仍是 INCLUDED（SPEC §6.6）
        assert derive_item_status("2次，0元", is_service=True) == ItemStatus.FREE
        assert derive_item_status("免费1次", is_service=True) == ItemStatus.FREE
        assert derive_item_status("保费0元", is_service=False) == ItemStatus.INCLUDED

    def test_listed_content_is_included(self) -> None:
        assert derive_item_status("300万元，保费1237.41元") == ItemStatus.INCLUDED


# ---- 重复行判定 ----


class TestDuplicateRow:
    def test_identical_rows_are_duplicates(self) -> None:
        a = RowIdentity("三者险", "300万", D("3000000"), D("1237.41"), (1, 1, "同一句"))
        b = RowIdentity("三者险", "300万", D("3000000"), D("1237.41"), (1, 1, "同一句"))
        assert is_duplicate_row(a, b) is True

    def test_different_premium_not_duplicate(self) -> None:
        a = RowIdentity("三者险", "300万", D("3000000"), D("1237.41"), (1, 1, "句"))
        b = RowIdentity("三者险", "300万", D("3000000"), D("9999.99"), (1, 1, "句"))
        assert is_duplicate_row(a, b) is False

    def test_different_evidence_not_duplicate(self) -> None:
        # 同 code 同金额但来源不同：不得自动去重
        a = RowIdentity("三者险", "300万", D("3000000"), D("1237.41"), (1, 1, "句A"))
        b = RowIdentity("三者险", "300万", D("3000000"), D("1237.41"), (2, 1, "句B"))
        assert is_duplicate_row(a, b) is False


# ---- 数值范围提示 ----


class TestAmountRange:
    def test_out_of_range_returns_hint(self) -> None:
        hint = check_amount_range("THIRD_PARTY_LIABILITY", D("400000"))
        assert hint is not None and "三者险保额" in hint
        hint = check_amount_range("VEHICLE_LOSS", D("6000000"))
        assert hint is not None and "车损保额" in hint

    def test_in_range_returns_none(self) -> None:
        assert check_amount_range("THIRD_PARTY_LIABILITY", D("3000000")) is None
        assert check_amount_range("VEHICLE_LOSS", D("147719.12")) is None
        assert check_amount_range(None, D("1")) is None
        assert check_amount_range("THIRD_PARTY_LIABILITY", None) is None


# ---- 商业险 / 保障包计算值 ----


class TestComputedCommercialPremium:
    def test_all_classified_complete_sums(self) -> None:
        rows = [
            _row(premium=D("1237.41")),
            _row(code="VEHICLE_LOSS", premium=D("1100")),
            _row(code="DRIVER_LIABILITY", premium=D("50")),
            _row(code="PASSENGER_LIABILITY", premium=D("100")),
        ]
        assert compute_commercial_premium(rows) == D("2487.41")

    def test_missing_premium_blocks(self) -> None:
        # 保费缺失且未明确不包含：不能当 0
        assert compute_commercial_premium([_row(premium=None)]) is None

    def test_empty_formal_rows_is_none_not_zero(self) -> None:
        assert compute_commercial_premium([]) is None

    def test_explicit_not_included_counts_zero(self) -> None:
        rows = [_row(premium=D("100")), _row(status=ItemStatus.NOT_INCLUDED, premium=None)]
        assert compute_commercial_premium(rows) == D("100")

    def test_unrecognized_with_money_blocks(self) -> None:
        rows = [
            _row(premium=D("100")),
            _row(
                category=CoverageCategory.UNRECOGNIZED,
                code=None,
                premium=D("55"),
            ),
        ]
        assert compute_commercial_premium(rows) is None

    def test_unrecognized_with_amount_only_also_blocks(self) -> None:
        # 只有保额没有保费的未识别项同样阻断（其保费未知，不能当 0）
        rows = [
            _row(premium=D("100")),
            _row(
                category=CoverageCategory.UNRECOGNIZED,
                code=None,
                coverage_amount=D("50000"),
            ),
        ]
        assert compute_commercial_premium(rows) is None

    def test_unrecognized_explicitly_excluded_no_longer_blocks(self) -> None:
        rows = [
            _row(premium=D("100")),
            _row(
                category=CoverageCategory.UNRECOGNIZED,
                code=None,
                status=ItemStatus.NOT_INCLUDED,
                premium=D("55"),
            ),
        ]
        assert compute_commercial_premium(rows) == D("100")


class TestComputedPackageTotal:
    def test_all_complete_sums(self) -> None:
        assert compute_package_total([D("348"), D("100")]) == D("448")

    def test_any_missing_blocks(self) -> None:
        assert compute_package_total([D("348"), None]) is None

    def test_empty_is_none_not_zero(self) -> None:
        assert compute_package_total([]) is None


# ---- eff 与 computedTotal ----


class TestEffectivePriceItem:
    def test_not_included_is_zero(self) -> None:
        assert effective_price_item(PriceItemStatus.NOT_INCLUDED, None, None) == D("0")

    def test_user_value_preferred_over_computed(self) -> None:
        assert effective_price_item(
            PriceItemStatus.INCLUDED, D("100"), D("999")
        ) == D("100")

    def test_fallback_to_computed(self) -> None:
        assert effective_price_item(PriceItemStatus.INCLUDED, None, D("999")) == D("999")

    def test_both_missing_is_none(self) -> None:
        assert effective_price_item(PriceItemStatus.INCLUDED, None, None) is None
        assert effective_price_item(PriceItemStatus.UNKNOWN, None, None) is None


def _total_kwargs(**overrides) -> dict:
    # 默认：商业 2487.41（计算值）、交强 1045、车船 0、保障包 348（计算值）、其他费用不包含
    kwargs = dict(
        commercial_status=PriceItemStatus.INCLUDED,
        commercial_premium=None,
        computed_commercial_premium=D("2487.41"),
        compulsory_status=PriceItemStatus.INCLUDED,
        compulsory_premium=D("1045"),
        vehicle_tax_status=PriceItemStatus.INCLUDED,
        vehicle_tax=D("0"),
        package_status=PriceItemStatus.INCLUDED,
        package_total=None,
        computed_package_total=D("348"),
        other_fees_status=PriceItemStatus.NOT_INCLUDED,
        other_fees=D("0"),
    )
    kwargs.update(overrides)
    return kwargs


class TestComputedTotal:
    def test_full_sum(self) -> None:
        assert compute_computed_total(**_total_kwargs()) == D("3880.41")

    def test_any_unknown_blocks(self) -> None:
        # 任一必需分项 UNKNOWN 且无值 → 整体 null，绝不把未知当 0
        # （API 不变量保证“值非空必为 INCLUDED”，故 UNKNOWN 必然无值）
        assert compute_computed_total(
            **_total_kwargs(
                compulsory_status=PriceItemStatus.UNKNOWN, compulsory_premium=None
            )
        ) is None

    def test_all_not_included_is_zero(self) -> None:
        result = compute_computed_total(
            **_total_kwargs(
                commercial_status=PriceItemStatus.NOT_INCLUDED,
                computed_commercial_premium=None,
                compulsory_status=PriceItemStatus.NOT_INCLUDED,
                vehicle_tax_status=PriceItemStatus.NOT_INCLUDED,
                package_status=PriceItemStatus.NOT_INCLUDED,
                computed_package_total=None,
            )
        )
        assert result == D("0")

    def test_included_but_no_value_blocks(self) -> None:
        assert compute_computed_total(
            **_total_kwargs(commercial_premium=None, computed_commercial_premium=None)
        ) is None


# ---- 总额三态校验 ----


class TestTotalCheckStatus:
    def test_passed_when_within_tolerance(self) -> None:
        assert resolve_total_check_status(D("100.00"), D("100.30"), TOLERANCE) == (
            TotalCheckStatus.PASSED
        )

    def test_boundary_equal_tolerance_is_passed(self) -> None:
        # |差| 恰好等于容差：≤ 语义 → PASSED（吸收四舍五入）
        assert resolve_total_check_status(D("100.00"), D("100.50"), TOLERANCE) == (
            TotalCheckStatus.PASSED
        )

    def test_mismatch_when_beyond_tolerance(self) -> None:
        assert resolve_total_check_status(D("3880.41"), D("5785.14"), TOLERANCE) == (
            TotalCheckStatus.MISMATCH
        )

    def test_not_checkable_when_either_missing(self) -> None:
        assert resolve_total_check_status(None, D("100"), TOLERANCE) == (
            TotalCheckStatus.NOT_CHECKABLE
        )
        assert resolve_total_check_status(D("100"), None, TOLERANCE) == (
            TotalCheckStatus.NOT_CHECKABLE
        )
        assert resolve_total_check_status(None, None, TOLERANCE) == (
            TotalCheckStatus.NOT_CHECKABLE
        )


# ---- 净支出 ----


class TestNetPayment:
    def test_normal_deduction(self) -> None:
        discounts = [
            DiscountValueRow(True, D("300")),
            DiscountValueRow(False, D("500")),  # 未勾选：不减
        ]
        amount, status = resolve_net_payment(D("5785.14"), None, discounts)
        assert (amount, status) == (D("5485.14"), NetPaymentStatus.OK)

    def test_no_cash_equivalent_never_deducts(self) -> None:
        # 无折现值（洗车/保养）：无论是否勾选都不减钱
        discounts = [DiscountValueRow(True, None)]
        amount, status = resolve_net_payment(D("5785.14"), None, discounts)
        assert (amount, status) == (D("5785.14"), NetPaymentStatus.OK)

    def test_official_total_preferred_over_computed(self) -> None:
        amount, _ = resolve_net_payment(D("5785.14"), D("3880.41"), [])
        assert amount == D("5785.14")

    def test_fallback_to_computed_when_official_missing(self) -> None:
        amount, _ = resolve_net_payment(None, D("3880.41"), [DiscountValueRow(True, D("80.41"))])
        assert amount == D("3800.00")

    def test_both_missing_is_missing_total(self) -> None:
        assert resolve_net_payment(None, None, []) == (None, NetPaymentStatus.MISSING_TOTAL)

    def test_over_discount_invalid(self) -> None:
        discounts = [DiscountValueRow(True, D("9999"))]
        assert resolve_net_payment(D("5785.14"), None, discounts) == (
            None,
            NetPaymentStatus.INVALID_DISCOUNT,
        )

    def test_boundary_discount_equals_base_is_zero_ok(self) -> None:
        # 折现合计恰好等于基准总价：净支出 0，状态 OK（“大于”才是超额）
        discounts = [DiscountValueRow(True, D("5785.14"))]
        assert resolve_net_payment(D("5785.14"), None, discounts) == (D("0"), NetPaymentStatus.OK)
