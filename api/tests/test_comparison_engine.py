"""规则对比引擎纯函数表驱动测试（TASK-06 验证 1，SPEC §7.1–§7.4）。

不访问数据库、不调用模型：直接构造 QuoteSnapshot 输入，
对五问的结构化字段、差异标签与六区行逐字段断言（不使用模糊快照）。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.enums import (
    ItemStatus,
    NetPaymentStatus,
    PriceItemStatus,
    QuoteStatus,
    TotalCheckStatus,
)
from app.services.comparison.engine import (
    CORE_COMPARE_CODES,
    CoverageSnapshot,
    DiscountSnapshot,
    PackageCoverageSnapshot,
    PackageSnapshot,
    QuoteSnapshot,
    ServiceSnapshot,
    build_comparison,
    sort_by_net_payment,
)

# ---- 快照构造助手（默认一份“保障完整、价格正常”的人保方案）----


def _core(code: str, amount: str | None, premium: str | None = "100.00", **extra) -> CoverageSnapshot:
    return CoverageSnapshot(
        code=code,
        name=code,
        status=ItemStatus.INCLUDED,
        coverage_amount=Decimal(amount) if amount else None,
        per_seat_amount=Decimal(extra["per_seat"]) if extra.get("per_seat") else None,
        seat_count=extra.get("seat_count"),
        shared_coverage=extra.get("shared"),
        premium=Decimal(premium) if premium else None,
        multiplier=Decimal(extra["multiplier"]) if extra.get("multiplier") else None,
        condition=extra.get("condition"),
    )


def make_quote(
    quote_id: int,
    display_name: str,
    *,
    net: str | None = "5000.00",
    official: str | None = "5500.00",
    check: TotalCheckStatus = TotalCheckStatus.PASSED,
    net_status: NetPaymentStatus | None = None,
    commercial_eff: str | None = "3000.00",
    computed_commercial: str | None = "3000.00",
    core: dict[str, CoverageSnapshot] | None = None,
    additional: dict[str, CoverageSnapshot] | None = None,
    services: dict[str, ServiceSnapshot] | None = None,
    packages: list[PackageSnapshot] | None = None,
    discounts: list[DiscountSnapshot] | None = None,
    unrecognized: int = 0,
    has_user_valuation: bool = False,
    passenger: CoverageSnapshot | None = None,
) -> QuoteSnapshot:
    """构造快照；core 缺省包含商业四大主险（乘客险支持 per_seat 覆盖）。"""
    resolved_core = core if core is not None else {
        "VEHICLE_LOSS": _core("VEHICLE_LOSS", "147719.12"),
        "THIRD_PARTY_LIABILITY": _core("THIRD_PARTY_LIABILITY", "3000000"),
        "DRIVER_LIABILITY": _core("DRIVER_LIABILITY", "10000"),
        "PASSENGER_LIABILITY": passenger or _core(
            "PASSENGER_LIABILITY", "40000", per_seat="10000", seat_count=4
        ),
    }
    resolved_net_status = net_status or (
        NetPaymentStatus.OK if net is not None else NetPaymentStatus.MISSING_TOTAL
    )
    return QuoteSnapshot(
        quote_id=quote_id,
        display_name=display_name,
        insurer_code="PICC",
        insurer_name="人保",
        agent_name=None,
        plan_label=display_name,
        status=QuoteStatus.CONFIRMED,
        commercial_eff=Decimal(commercial_eff) if commercial_eff else None,
        compulsory_eff=Decimal("950.00"),
        vehicle_tax_eff=Decimal("0"),
        package_eff=Decimal("348.00"),
        other_fees_eff=Decimal("0"),
        commercial_status=PriceItemStatus.INCLUDED,
        compulsory_status=PriceItemStatus.INCLUDED,
        vehicle_tax_status=PriceItemStatus.INCLUDED,
        package_status=PriceItemStatus.INCLUDED,
        other_fees_status=PriceItemStatus.NOT_INCLUDED,
        official_total=Decimal(official) if official else None,
        computed_total=Decimal("5298.00"),
        total_check_status=check,
        net_payment=Decimal(net) if net else None,
        net_payment_status=resolved_net_status,
        computed_commercial_premium=(
            Decimal(computed_commercial) if computed_commercial else None
        ),
        has_user_valuation=has_user_valuation,
        core=resolved_core,
        additional=additional or {},
        services=services or {},
        packages=packages or [],
        discounts=discounts or [],
        unrecognized_money_count=unrecognized,
    )


def _section(result, key: str):
    return next(section for section in result.sections if section.key == key)


def _row(result, key: str):
    return next(row for section in result.sections for row in section.rows if row.key == key)


# ---- 排序（§7.1）----


@pytest.mark.parametrize(
    ("nets", "expected_ids"),
    [
        # 正常升序
        (["6000", "5000", "5500"], [2, 3, 1]),
        # null 排最后，组内保持传入顺序（稳定）
        ([None, "5000", None], [2, 1, 3]),
        # 全部 null：保持传入顺序
        ([None, None], [1, 2]),
    ],
)
def test_sort_by_net_payment(nets: list[str | None], expected_ids: list[int]) -> None:
    quotes = [
        make_quote(index + 1, f"Q{index + 1}", net=net)
        for index, net in enumerate(nets)
    ]
    ordered = sort_by_net_payment(quotes)
    assert [q.quote_id for q, _ in ordered] == expected_ids
    # rank 连续且从 0 开始（null 报价也有序号供前端标注）
    assert [rank for _, rank in ordered] == list(range(len(nets)))


# ---- 第一问：哪个最便宜（§7.2.1）----


def test_cheapest_min_and_tie() -> None:
    a = make_quote(1, "A", net="5000.00")
    b = make_quote(2, "B", net="4800.00")
    c = make_quote(3, "C", net="4800.00")
    answer = build_comparison([a, b, c], 1).five_questions.cheapest
    assert answer.kind == "MIN"
    assert answer.quote_ids == [2, 3]  # 并列最低全部列出
    assert answer.net_payment == 4800.0
    assert "「B」、「C」" in answer.text


@pytest.mark.parametrize(
    "kwargs",
    [
        # 总额校验异常 → 暂为最低
        dict(net="4800.00", check=TotalCheckStatus.MISMATCH),
        # 官方与系统总价都无法校验 → 暂为最低
        dict(net="4800.00", check=TotalCheckStatus.NOT_CHECKABLE),
        # 净支出含用户自愿填写的折现估值 → 暂为最低
        dict(net="4800.00", has_user_valuation=True),
    ],
)
def test_cheapest_tentative(kwargs: dict) -> None:
    cheapest = make_quote(1, "便宜", **kwargs)
    other = make_quote(2, "对照", net="5200.00")
    answer = build_comparison([other, cheapest], 1).five_questions.cheapest
    assert answer.kind == "TENTATIVE"
    assert answer.quote_ids == [1]
    assert "暂为最低" in answer.text


def test_cheapest_insufficient_when_all_missing() -> None:
    a = make_quote(1, "A", net=None)
    b = make_quote(
        2,
        "B",
        net=None,
        net_status=NetPaymentStatus.INVALID_DISCOUNT,  # 优惠超额同样不参与判定
    )
    answer = build_comparison([a, b], 1).five_questions.cheapest
    assert answer.kind == "INSUFFICIENT_PRICE"
    assert answer.quote_ids == []
    assert "价格信息不足" in answer.text


def test_invalid_discount_excluded_and_annotated() -> None:
    """优惠超额报价排最后、不参与最低价判定，但保留在结果中并标注。"""
    ok = make_quote(1, "正常", net="5000.00")
    bad = make_quote(
        2, "超额", net=None, net_status=NetPaymentStatus.INVALID_DISCOUNT
    )
    result = build_comparison([bad, ok], 1)
    assert result.five_questions.cheapest.quote_ids == [1]
    # 排序：超额在最后
    assert result.price_order[-1].quote_id == 2
    meta_bad = next(m for m in result.quotes if m.quote_id == 2)
    assert "优惠超额，请修正" in meta_bad.annotations
    # 价格区净支出单元格文本不得静默当 0
    net_cells = _row(result, "net").cells
    assert "优惠超额" in net_cells[0].text


# ---- 第二问：关键保障额度最高（§7.2.2，绝不求和）----


def test_strongest_metrics_per_object() -> None:
    a = make_quote(
        1, "A", additional={"TP_NON_MEDICAL": _core("TP_NON_MEDICAL", "1000000")}
    )
    b = make_quote(
        2,
        "B",
        core={
            "VEHICLE_LOSS": _core("VEHICLE_LOSS", "150000"),
            "THIRD_PARTY_LIABILITY": _core("THIRD_PARTY_LIABILITY", "5000000"),
            "DRIVER_LIABILITY": _core("DRIVER_LIABILITY", "10000"),
            "PASSENGER_LIABILITY": _core(
                "PASSENGER_LIABILITY", "40000", per_seat="10000", seat_count=4
            ),
        },
        additional={"TP_NON_MEDICAL": _core("TP_NON_MEDICAL", "500000")},
    )
    metrics = {m.key: m for m in build_comparison([a, b], 1).five_questions.strongest}
    # 三者：B 最高
    assert metrics["third_party"].max_quote_ids == [2]
    assert metrics["third_party"].max_amount == 5_000_000.0
    # 三者医保外：A 最高（不同保障对象分别比较，绝不求和）
    assert metrics["tp_non_medical"].max_quote_ids == [1]
    assert metrics["tp_non_medical"].max_amount == 1_000_000.0
    # 车损：B 更高（只描述差异，无“越高越好”表述由前端保证）
    assert metrics["vehicle_loss"].max_quote_ids == [2]


def test_strongest_insufficient_and_missing() -> None:
    a = make_quote(1, "A")  # 三者 300 万
    # B 三者存在但保额未知 → 计入 missing_quote_ids
    b = make_quote(
        2,
        "B",
        core={
            "VEHICLE_LOSS": _core("VEHICLE_LOSS", "147719.12"),
            "THIRD_PARTY_LIABILITY": CoverageSnapshot(
                code="THIRD_PARTY_LIABILITY", name="三者险",
                status=ItemStatus.UNKNOWN, coverage_amount=None,
                per_seat_amount=None, seat_count=None, shared_coverage=None,
                premium=None, multiplier=None, condition=None,
            ),
            "DRIVER_LIABILITY": _core("DRIVER_LIABILITY", "10000"),
            "PASSENGER_LIABILITY": _core(
                "PASSENGER_LIABILITY", "40000", per_seat="10000", seat_count=4
            ),
        },
    )
    metrics = {m.key: m for m in build_comparison([a, b], 1).five_questions.strongest}
    assert metrics["third_party"].max_quote_ids == [1]
    assert metrics["third_party"].missing_quote_ids == [2]
    assert not metrics["third_party"].insufficient


def test_strongest_insufficient_when_no_quote_has_amount() -> None:
    a = make_quote(1, "A", core={code: _core(code, None) for code in CORE_COMPARE_CODES})
    b = make_quote(2, "B", core={code: _core(code, None) for code in CORE_COMPARE_CODES})
    metrics = {m.key: m for m in build_comparison([a, b], 1).five_questions.strongest}
    assert metrics["third_party"].insufficient is True
    assert metrics["third_party"].max_quote_ids == []


# ---- 第三问：保障不完整（§7.2.3，交强险不计入）----


def test_incomplete_missing_core_codes() -> None:
    """缺司机险 + 三者未知 → 保障不完整并给出缺失清单；交强不参与判定。"""
    a = make_quote(1, "完整")
    b = make_quote(
        2,
        "缺司机",
        core={
            "VEHICLE_LOSS": _core("VEHICLE_LOSS", "147719.12"),
            # 三者状态未知 → 计入缺失清单（SPEC §7.2.3）
            "THIRD_PARTY_LIABILITY": CoverageSnapshot(
                code="THIRD_PARTY_LIABILITY", name="三者险",
                status=ItemStatus.UNKNOWN, coverage_amount=None,
                per_seat_amount=None, seat_count=None, shared_coverage=None,
                premium=None, multiplier=None, condition=None,
            ),
            # DRIVER_LIABILITY 整行缺失
            "PASSENGER_LIABILITY": _core(
                "PASSENGER_LIABILITY", "40000", per_seat="10000", seat_count=4
            ),
        },
    )
    items = build_comparison([a, b], 1).five_questions.incomplete
    assert items[0].complete is True
    assert items[0].missing == []
    assert items[1].complete is False
    # 三者（未知）+ 司机（缺行）；交强险缺失不出现（不在判定范围）
    assert items[1].missing == ["三者险", "司机险"]


# ---- 第四问：价格归因（§7.2.4）----


def test_attribution_deltas_and_top_changes() -> None:
    """以最低净支出为基准：Δ分项与险种 Top 变化逐项断言。"""
    base = make_quote(
        1,
        "基准",
        net="5000.00",
        core={
            "VEHICLE_LOSS": _core("VEHICLE_LOSS", "147719.12", premium="1477.19"),
            "THIRD_PARTY_LIABILITY": _core("THIRD_PARTY_LIABILITY", "2000000", premium="1000.00"),
            "DRIVER_LIABILITY": _core("DRIVER_LIABILITY", "10000", premium="500.00"),
            "PASSENGER_LIABILITY": _core(
                "PASSENGER_LIABILITY", "40000", premium="2000.00", per_seat="10000", seat_count=4
            ),
        },
        computed_commercial="3977.19",
    )
    other = make_quote(
        2,
        "更贵",
        net="5365.00",
        official="5865.00",
        core={
            "VEHICLE_LOSS": _core("VEHICLE_LOSS", "147719.12", premium="1477.19"),
            # 三者 300 万、保费 +237.41 → 险种级最大变化
            "THIRD_PARTY_LIABILITY": _core("THIRD_PARTY_LIABILITY", "3000000", premium="1237.41"),
            "DRIVER_LIABILITY": _core("DRIVER_LIABILITY", "10000", premium="500.00"),
            "PASSENGER_LIABILITY": _core(
                "PASSENGER_LIABILITY", "40000", premium="2000.00", per_seat="10000", seat_count=4
            ),
        },
        commercial_eff="3214.60",
        computed_commercial="4214.60",
    )
    attribution = build_comparison([base, other], 1).five_questions.attribution
    assert attribution.price_baseline_quote_id == 1
    assert len(attribution.pairs) == 1
    pair = attribution.pairs[0]
    assert pair.other_quote_id == 2
    assert pair.delta_net == 365.0
    assert pair.detail_complete is True
    parts = {p.key: p for p in pair.parts}
    assert parts["commercial"].delta == 214.6  # 3214.60 - 3000.00
    assert parts["commercial"].comparable is True
    assert parts["compulsory"].delta == 0.0
    # 险种 Top 变化：三者 +237.41 唯一非零
    assert len(pair.top_changes) == 1
    assert pair.top_changes[0].code == "THIRD_PARTY_LIABILITY"
    assert pair.top_changes[0].delta == pytest.approx(237.41)


def test_attribution_blocked_when_detail_incomplete() -> None:
    """任一侧明细保费不完整 → 险种级归因阻断并明确说明。"""
    base = make_quote(1, "基准", computed_commercial="3000.00")
    other = make_quote(2, "残缺", computed_commercial=None)
    pair = build_comparison([base, other], 1).five_questions.attribution.pairs[0]
    assert pair.detail_complete is False
    assert pair.top_changes == []
    assert pair.note is not None and "明细保费不完整" in pair.note


def test_attribution_part_not_comparable_when_eff_missing() -> None:
    """eff 值任一侧缺失 → 该分项不可比（delta=None），绝不当 0。"""
    base = make_quote(1, "基准")
    other = make_quote(2, "未知", commercial_eff=None)
    pair = build_comparison([base, other], 1).five_questions.attribution.pairs[0]
    commercial = next(p for p in pair.parts if p.key == "commercial")
    assert commercial.comparable is False
    assert commercial.delta is None
    assert commercial.baseline_value == 3000.0
    assert commercial.other_value is None
    # Δ净支出仍给出
    assert pair.delta_net == 0.0


def test_attribution_unavailable_when_no_usable_net() -> None:
    a = make_quote(1, "A", net=None)
    b = make_quote(2, "B", net=None)
    attribution = build_comparison([a, b], 1).five_questions.attribution
    assert attribution.price_baseline_quote_id is None
    assert attribution.pairs == []
    assert attribution.unavailable_reason is not None


# ---- 第五问：不能直接比（§7.2.5）----


def test_incomparable_scope_and_amount_difference() -> None:
    """三者保额不同 + A 独有电网险 → 口径不同提示与明细。"""
    a = make_quote(
        1,
        "A",
        additional={
            "TP_NON_MEDICAL": _core("TP_NON_MEDICAL", "500000"),
            "EXTERNAL_GRID": _core("EXTERNAL_GRID", "50000"),
        },
    )
    b = make_quote(
        2,
        "B",
        core={
            "VEHICLE_LOSS": _core("VEHICLE_LOSS", "147719.12"),
            "THIRD_PARTY_LIABILITY": _core("THIRD_PARTY_LIABILITY", "5000000"),
            "DRIVER_LIABILITY": _core("DRIVER_LIABILITY", "10000"),
            "PASSENGER_LIABILITY": _core(
                "PASSENGER_LIABILITY", "40000", per_seat="10000", seat_count=4
            ),
        },
        additional={"TP_NON_MEDICAL": _core("TP_NON_MEDICAL", "300000")},
    )
    answer = build_comparison([a, b], 1).five_questions.incomparable
    assert answer.scope_differs is True
    dimensions = {(d.code, d.dimension) for d in answer.differences}
    assert ("THIRD_PARTY_LIABILITY", "保额") in dimensions
    # 第五问口径检查限定商业四大主险（SPEC §7.2.5）；附加险集合差异
    # 不进入同口径提示，而是通过附加险分区的 +/− 行呈现
    assert ("EXTERNAL_GRID", "集合") not in dimensions
    amount_diff = next(
        d
        for d in answer.differences
        if d.code == "THIRD_PARTY_LIABILITY" and d.dimension == "保额"
    )
    assert "300 万" in amount_diff.detail and "500 万" in amount_diff.detail
    assert answer.messages[0] == "同口径提示：核心保障口径不同，不能仅按总价判断"


def test_incomparable_unknown_and_unrecognized_count() -> None:
    """三者未知 → 信息不足提示；未识别金额项按数量提示且不进分区。"""
    a = make_quote(
        1,
        "A",
        core={
            "VEHICLE_LOSS": _core("VEHICLE_LOSS", "147719.12"),
            "THIRD_PARTY_LIABILITY": CoverageSnapshot(
                code="THIRD_PARTY_LIABILITY", name="三者险",
                status=ItemStatus.UNKNOWN, coverage_amount=None,
                per_seat_amount=None, seat_count=None, shared_coverage=None,
                premium=None, multiplier=None, condition=None,
            ),
            "DRIVER_LIABILITY": _core("DRIVER_LIABILITY", "10000"),
            "PASSENGER_LIABILITY": _core(
                "PASSENGER_LIABILITY", "40000", per_seat="10000", seat_count=4
            ),
        },
        unrecognized=2,
    )
    b = make_quote(2, "B", unrecognized=1)
    answer = build_comparison([a, b], 1).five_questions.incomparable
    assert answer.unrecognized_count == 3
    assert any("3 项未识别保障" in message for message in answer.messages)
    assert any("三者险" in message and "信息不足" in message for message in answer.messages)


def test_incomparable_no_noise_when_dimensions_uniformly_absent() -> None:
    """所有报价都不提供的维度（如司机险座位数）不制造“信息不足”噪音。"""
    a = make_quote(1, "A")
    b = make_quote(2, "B")
    answer = build_comparison([a, b], 1).five_questions.incomparable
    assert answer.scope_differs is False
    assert answer.unknown_items == []
    assert answer.messages == []


# ---- 差异标签与分区（§7.3/§7.4）----


def test_diff_tags_table() -> None:
    """表驱动：↑/↓/+/−/相对基准逐标签断言（基准列为列 0，无箭头）。"""
    baseline = make_quote(1, "基准", net="5000.00")
    up = make_quote(2, "贵", net="5500.00")
    down = make_quote(3, "省", net="4500.00")
    missing = make_quote(4, "缺", net=None)
    result = build_comparison([baseline, up, down, missing], 1)
    cells = _row(result, "net").cells
    assert cells[0].tag is None and cells[0].diff is False  # 基准列不标
    assert cells[1].tag == "UP" and cells[1].diff is True
    assert cells[2].tag == "DOWN" and cells[2].diff is True
    assert cells[3].tag == "MISS" and cells[3].diff is True


def test_add_tag_for_baseline_absent_coverage() -> None:
    """基准缺失、对方有值 → + 新增（保障包与附加险常见形态）。"""
    baseline = make_quote(1, "基准")
    other = make_quote(
        2,
        "有包",
        packages=[
            PackageSnapshot(
                "车主尊享保障",
                Decimal("348"),
                [
                    PackageCoverageSnapshot(
                        type="DRIVER_ACCIDENT",
                        name="驾乘意外",
                        status=ItemStatus.INCLUDED,
                        coverage_amount=Decimal("300000"),
                        unit="CNY",
                        multiplier=Decimal("2"),
                        condition="LEGAL_HOLIDAY",
                    )
                ],
            )
        ],
    )
    result = build_comparison([baseline, other], 1)
    pkg_section = _section(result, "packages")
    assert [row.cells[1].tag for row in pkg_section.rows] == ["ADD", "ADD"]
    # 保障包内部保障按类型展开，口径文本带倍数与条件
    item_row = next(r for r in pkg_section.rows if "DRIVER_ACCIDENT" in r.key)
    assert "30 万" in item_row.cells[1].text
    assert "×2" in item_row.cells[1].text
    assert "法定节假日" in item_row.cells[1].text


def test_sections_order_and_diff_first() -> None:
    """六个稳定分区按序返回；每个分区差异行置顶。"""
    baseline = make_quote(1, "基准", net="5000.00")
    other = make_quote(2, "对照", net="5200.00")
    result = build_comparison([baseline, other], 1)
    assert [s.key for s in result.sections] == [
        "price",
        "core",
        "additional",
        "packages",
        "services",
        "net",
    ]
    for section in result.sections:
        diff_flags = [row.diff for row in section.rows]
        assert diff_flags == sorted(diff_flags, reverse=True), section.key


def test_unrecognized_excluded_from_sections() -> None:
    """未识别金额项不进入任何结构化分区（第五问只提示数量）。"""
    baseline = make_quote(1, "基准", unrecognized=2)
    result = build_comparison([baseline], 99)
    for section in result.sections:
        for row in section.rows:
            assert "未识别" not in row.label


def test_services_compared_by_type_and_discount_rows() -> None:
    """服务按类型比较；优惠按类型+描述比较并给出折现合计。"""
    baseline = make_quote(
        1,
        "A",
        services={
            "ROAD_RESCUE": ServiceSnapshot("ROAD_RESCUE", ItemStatus.FREE, 2, Decimal("0")),
        },
        discounts=[
            DiscountSnapshot("CASH", "微信红包", Decimal("200"), Decimal("200"), True),
            DiscountSnapshot("SERVICE", "洗车", Decimal("100"), None, True),
        ],
    )
    other = make_quote(
        2,
        "B",
        services={
            "ROAD_RESCUE": ServiceSnapshot("ROAD_RESCUE", ItemStatus.FREE, 3, Decimal("0")),
        },
        discounts=[
            DiscountSnapshot("CASH", "微信红包", Decimal("300"), Decimal("300"), True),
        ],
    )
    result = build_comparison([baseline, other], 1)
    rescue = next(
        row for row in _section(result, "services").rows if row.key.endswith("ROAD_RESCUE")
    )
    assert "2 次" in rescue.cells[0].text
    assert "3 次" in rescue.cells[1].text
    assert rescue.diff is True

    net_rows = {row.key: row for row in _section(result, "net").rows}
    cash_row = next(k for k in net_rows if k.startswith("discount:CASH"))
    # 优惠行是文本行：差异只高亮，不使用金额箭头
    assert net_rows[cash_row].diff is True
    assert net_rows[cash_row].cells[1].tag is None
    # 无折现的服务权益优惠不减钱
    service_discount = next(k for k in net_rows if k.startswith("discount:SERVICE"))
    assert "无折现" in net_rows[service_discount].cells[0].text
    deduction = net_rows["deduction_total"]
    assert deduction.cells[0].text == "¥200.00"
    assert deduction.cells[1].text == "¥300.00"


def test_two_and_six_quotes_table() -> None:
    """表驱动规模检查：2 与 6 个报价都能稳定出全五问与六区。"""
    for count in (2, 6):
        quotes = [
            make_quote(
                index + 1,
                f"Q{index + 1}",
                net=str(5000 + index * 100) + ".00",
                core={
                    "VEHICLE_LOSS": _core("VEHICLE_LOSS", "147719.12"),
                    "THIRD_PARTY_LIABILITY": _core(
                        "THIRD_PARTY_LIABILITY", str(2000000 + index * 500000)
                    ),
                    "DRIVER_LIABILITY": _core("DRIVER_LIABILITY", "10000"),
                    "PASSENGER_LIABILITY": _core(
                        "PASSENGER_LIABILITY", "40000", per_seat="10000", seat_count=4
                    ),
                },
            )
            for index in range(count)
        ]
        result = build_comparison(quotes, 1)
        assert len(result.quotes) == count
        assert result.quotes[0].is_diff_baseline is True
        assert sum(1 for m in result.quotes if m.is_price_baseline) == 1
        assert [s.key for s in result.sections] == [
            "price", "core", "additional", "packages", "services", "net",
        ]
        # 价格归因基准 = 最低净支出（第一个）
        assert result.five_questions.cheapest.quote_ids == [1]


def test_same_quotes_do_not_cross_columns() -> None:
    """两份字段完全相同的快照不会串列（引擎不依赖对象身份/索引查找）。"""
    a = make_quote(1, "同名", net="5000.00")
    b = make_quote(2, "同名", net="5000.00")
    result = build_comparison([a, b], 1)
    for section in result.sections:
        for row in section.rows:
            assert row.diff is False
            assert row.cells[0].tag is None
            assert row.cells[1].tag in ("SAME", None)


def test_result_contract_serializable() -> None:
    """整个结果可 JSON 序列化（camelCase 契约、金额 float）。"""
    import json

    result = build_comparison([make_quote(1, "A"), make_quote(2, "B", net="5200.00")], 7)
    data = json.loads(result.model_dump_json())
    assert data["projectId"] == 7
    assert data["disclaimer"].startswith("本工具")
    assert [s["key"] for s in data["sections"]] == [
        "price", "core", "additional", "packages", "services", "net",
    ]
