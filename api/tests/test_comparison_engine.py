"""规则对比引擎纯函数表驱动测试（TASK-06 验证 1，SPEC §7.1–§7.4）。

不访问数据库、不调用模型：直接构造 QuoteSnapshot 输入，
对单一总表的结构化行与差异标签逐字段断言（不使用模糊快照）。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.enums import (
    CoverageCategory,
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
from app.services.normalization.alias_map import COVERAGE_DEFINITIONS

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
        has_user_valuation=has_user_valuation,
        core=resolved_core,
        additional=additional or {},
        services=services or {},
        packages=packages or [],
        discounts=discounts or [],
        unrecognized_money_count=unrecognized,
    )


def _row(result, key: str):
    return next(row for row in result.rows if row.key == key)


# 总表行所属分组：0 价格 / 1 核心保障 / 2 附加险 / 3 额外保障 / 4 增值服务 / 5 优惠
_ADDITIONAL_CODES = {
    code
    for code, definition in COVERAGE_DEFINITIONS.items()
    if definition.category == CoverageCategory.ADDITIONAL.value
}


def _group_of(key: str) -> int:
    if key.startswith("pkg:"):
        return 3
    if key.startswith("svc:"):
        return 4
    if key.startswith("discount:") or key in ("deduction_total", "net_status"):
        return 5
    if key.split(":", 1)[0] in _ADDITIONAL_CODES:
        return 2
    if key.split(":", 1)[0] in CORE_COMPARE_CODES:
        return 1
    return 0  # 价格分组键（net / official_total / ... / other_fees）


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


def test_invalid_discount_excluded_and_annotated() -> None:
    """优惠超额报价排最后、不参与最低价判定，但保留在结果中并标注。"""
    ok = make_quote(1, "正常", net="5000.00")
    bad = make_quote(
        2, "超额", net=None, net_status=NetPaymentStatus.INVALID_DISCOUNT
    )
    result = build_comparison([bad, ok], 1)
    # 排序：超额在最后
    assert result.price_order[-1].quote_id == 2
    meta_bad = next(m for m in result.quotes if m.quote_id == 2)
    assert "优惠超额，请修正" in meta_bad.annotations
    # 价格分组净支出单元格文本不得静默当 0
    net_cells = _row(result, "net").cells
    assert "优惠超额" in net_cells[0].text


# ---- 差异标签与单一总表（§7.3/§7.4）----


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
    pkg_rows = [row for row in result.rows if row.key.startswith("pkg:")]
    assert [row.cells[1].tag for row in pkg_rows] == ["ADD", "ADD"]
    # 保障包内部保障按类型展开，口径文本带倍数与条件
    item_row = next(r for r in pkg_rows if "DRIVER_ACCIDENT" in r.key)
    assert "30 万" in item_row.cells[1].text
    assert "×2" in item_row.cells[1].text
    assert "法定节假日" in item_row.cells[1].text


def test_rows_group_order_and_diff_first() -> None:
    """单一总表按 价格→核心→附加→额外保障→服务→优惠 平铺；组内差异行置顶。"""

    def full(index: int, name: str, net: str) -> QuoteSnapshot:
        return make_quote(
            index,
            name,
            net=net,
            additional={"TP_NON_MEDICAL": _core("TP_NON_MEDICAL", "500000")},
            services={
                "ROAD_RESCUE": ServiceSnapshot(
                    "ROAD_RESCUE", ItemStatus.FREE, 2, Decimal("0")
                )
            },
            packages=[PackageSnapshot("车主尊享保障", Decimal("348"))],
        )

    result = build_comparison([full(1, "基准", "5000.00"), full(2, "对照", "5200.00")], 1)
    groups = [_group_of(row.key) for row in result.rows]
    # 六个分组都出现，且按固定顺序平铺
    assert set(groups) == {0, 1, 2, 3, 4, 5}
    assert groups == sorted(groups)
    # 每个分组内差异行置顶
    for group in set(groups):
        flags = [
            row.diff for row, g in zip(result.rows, groups, strict=True) if g == group
        ]
        assert flags == sorted(flags, reverse=True), group


def test_unrecognized_annotated_and_excluded_from_rows() -> None:
    """未识别金额项不进入总表行，只在方案列异常标注数量。"""
    baseline = make_quote(1, "基准", unrecognized=2)
    result = build_comparison([baseline], 99)
    for row in result.rows:
        assert "未识别" not in row.label
    assert "2 项未识别保障未参与对比" in result.quotes[0].annotations


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
        row for row in result.rows if row.key.endswith("ROAD_RESCUE")
    )
    assert "2 次" in rescue.cells[0].text
    assert "3 次" in rescue.cells[1].text
    assert rescue.diff is True

    discount_keys = ("discount:", "deduction_total", "net_status")
    net_rows = {
        row.key: row
        for row in result.rows
        if row.key.startswith(discount_keys)
    }
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
    """表驱动规模检查：2 与 6 个报价都能稳定出全部指标行。"""
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
        # 每行的列数与报价数一一对应
        assert all(len(row.cells) == count for row in result.rows)
        # 价格分组的净支出行恒存在
        assert any(row.key == "net" for row in result.rows)


def test_same_quotes_do_not_cross_columns() -> None:
    """两份字段完全相同的快照不会串列（引擎不依赖对象身份/索引查找）。"""
    a = make_quote(1, "同名", net="5000.00")
    b = make_quote(2, "同名", net="5000.00")
    result = build_comparison([a, b], 1)
    for row in result.rows:
        assert row.diff is False
        assert row.cells[0].tag is None
        assert row.cells[1].tag in ("SAME", None)


def test_result_contract_serializable() -> None:
    """整个结果可 JSON 序列化（camelCase 契约、金额 float、行平铺在 rows）。"""
    import json

    result = build_comparison([make_quote(1, "A"), make_quote(2, "B", net="5200.00")], 7)
    data = json.loads(result.model_dump_json())
    assert data["projectId"] == 7
    assert data["disclaimer"].startswith("本工具")
    # 单一总表：行直接平铺在 rows，无分区包装、无五问字段
    assert data["rows"][0]["key"] == "net"
    assert all("label" in row and "cells" in row for row in data["rows"])
    assert "fiveQuestions" not in data and "sections" not in data
