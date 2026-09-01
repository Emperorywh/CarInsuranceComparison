"""规则对比引擎（TASK-06，SPEC §7）：排序、单一总表差异行与差异标签。

业务不变量（全部纯函数，不触碰数据库、不调用模型）：
- 比较只消费已确认数据快照（CONFIRMED / MERGE_REVIEW 的旧确认值）；
- 固定差异基准 = 用户勾选顺序第一个报价；价格基准 = 最低净支出报价，
  两者分开标注、互不改写（SPEC §7.1）；
- 任何 null 都不当 0：eff 值缺失的单元格显示“—”并标注原因，
  MISSING_TOTAL / INVALID_DISCOUNT 排最后且不参与最低价判定；
- 未识别金额项不进入结构化行，只把数量并入方案列异常标注；
- 全部指标行按 价格 → 核心保障 → 附加险 → 额外保障 → 增值服务 →
  优惠/净支出 的分组顺序平铺下发（单一总表，各分组内差异行置顶）。

实现方式：每个分组先按报价顺序预计算每列的 (结构化值, 展示文本) 对，
再交给统一行构造器打差异标签——避免闭包捕获列索引，完全相同的两份
报价也不会串列。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app.models.enums import (
    CoverageCategory,
    ItemStatus,
    NetPaymentStatus,
    PriceItemStatus,
    QuoteStatus,
    TotalCheckStatus,
)
from app.schemas.compare import (
    CompareCell,
    CompareQuoteMeta,
    CompareRow,
    ComparisonResult,
    PriceOrderEntry,
)
from app.services.dictionaries import STATUS_LABELS
from app.services.normalization.alias_map import (
    COVERAGE_DEFINITIONS,
    PACKAGE_COVERAGE_DEFINITIONS,
)

# 每列预计算结果：(结构化值, 展示文本)；值可为 None（缺失/未知）
CellPair = tuple[object, str]

# 统一免责声明（SPEC §8：对比页与导出长图共用同一文案）
DISCLAIMER = "本工具用于整理报价差异，不替代正式保险条款与投保决定，请以保险公司最终保单为准。"

# 商业四大主险（交强险不计入）：核心保障分组的比较范围
CORE_COMPARE_CODES: tuple[str, ...] = (
    "VEHICLE_LOSS",
    "THIRD_PARTY_LIABILITY",
    "DRIVER_LIABILITY",
    "PASSENGER_LIABILITY",
)

# 服务类型展示名（与 /api/dictionaries 的 serviceType 同源语义）
_SERVICE_LABELS: Mapping[str, str] = {
    "ROAD_RESCUE": "道路救援",
    "INSPECTION": "车辆安全检测",
    "DRIVER_SERVICE": "代驾",
    "INSPECTION_AGENT": "代办送检",
    "OTHER": "其他服务",
}

_ITEM_STATUS_TEXT: Mapping[ItemStatus, str] = {
    ItemStatus.INCLUDED: "已包含",
    ItemStatus.NOT_INCLUDED: "不包含",
    ItemStatus.FREE: "免费",
    ItemStatus.NOT_APPLICABLE: "不适用",
    ItemStatus.UNKNOWN: "未知",
}

_CHECK_TEXT: Mapping[TotalCheckStatus, str] = {
    TotalCheckStatus.PASSED: "校验通过",
    TotalCheckStatus.MISMATCH: "金额不一致",
    TotalCheckStatus.NOT_CHECKABLE: "无法校验",
}

_NET_STATUS_TEXT: Mapping[NetPaymentStatus, str] = {
    NetPaymentStatus.OK: "正常",
    NetPaymentStatus.MISSING_TOTAL: "总价缺失",
    NetPaymentStatus.INVALID_DISCOUNT: "优惠超额，请修正",
}


# ---- 展示格式化（服务端产出 text，前端与导出长图直接渲染）----


def _fmt_money(value: Decimal | None) -> str:
    """金额：两位小数千分位；null 必须显示“—”，绝不当 0。"""
    if value is None:
        return "—"
    return f"¥{float(value):,.2f}"


def _fmt_amount(value: Decimal | None) -> str:
    """保额：≥1 万按“万”展示，不足 1 万保留元；null 显示“—”。"""
    if value is None:
        return "—"
    amount = float(value)
    if amount >= 10000:
        wan = amount / 10000
        return f"{wan:g} 万" if wan == int(wan) else f"{wan:.2f} 万"
    return f"{amount:g} 元"


def _fmt_unit_amount(value: Decimal | None, unit: str | None) -> str:
    """保障包内部保障的“保额 + 单位”展示（CNY→万，TIMES→次，DAYS→天）。"""
    if value is None:
        return "—"
    if unit == "TIMES":
        return f"{float(value):g} 次"
    if unit == "DAYS":
        return f"{float(value):g} 天"
    if unit in (None, "CNY"):
        return _fmt_amount(value)
    return f"{float(value):g}"


# ---- 引擎输入快照（由 comparison.service 从 ORM 装配）----


@dataclass(frozen=True)
class CoverageSnapshot:
    """单个标准险种在该报价中的代表行（多条同码行取“已包含且保额最大”者）。"""

    code: str
    name: str
    status: ItemStatus
    coverage_amount: Decimal | None
    per_seat_amount: Decimal | None
    seat_count: int | None
    shared_coverage: bool | None
    premium: Decimal | None
    multiplier: Decimal | None
    condition: str | None

    def effective_amount(self) -> Decimal | None:
        """比较用保额：总额缺失时按“单座 × 座位”推导（与录入校验同口径）。"""
        if self.coverage_amount is not None:
            return self.coverage_amount
        if self.per_seat_amount is not None and self.seat_count is not None:
            return self.per_seat_amount * self.seat_count
        return None


@dataclass(frozen=True)
class ServiceSnapshot:
    """按 serviceType 归并后的服务代表行（service_type 为枚举字符串值）。"""

    service_type: str
    status: ItemStatus
    count: int | None
    cost: Decimal | None


@dataclass(frozen=True)
class PackageCoverageSnapshot:
    type: str
    name: str | None
    status: ItemStatus
    coverage_amount: Decimal | None
    unit: str | None
    multiplier: Decimal | None
    condition: str | None


@dataclass(frozen=True)
class PackageSnapshot:
    name: str
    premium: Decimal | None
    coverages: list[PackageCoverageSnapshot] = field(default_factory=list)


@dataclass(frozen=True)
class DiscountSnapshot:
    discount_type: str
    description: str | None
    amount: Decimal | None
    cash_equivalent: Decimal | None
    include_in_net: bool


@dataclass(frozen=True)
class QuoteSnapshot:
    """一份报价的对比输入投影：只含已确认旧值，不含任何候选/合并中数据。"""

    quote_id: int
    display_name: str
    insurer_code: str
    insurer_name: str
    agent_name: str | None
    plan_label: str | None
    status: QuoteStatus

    # 五个价格分项的 eff 值（NOT_INCLUDED 按 0；UNKNOWN/缺失为 None）
    commercial_eff: Decimal | None
    compulsory_eff: Decimal | None
    vehicle_tax_eff: Decimal | None
    package_eff: Decimal | None
    other_fees_eff: Decimal | None
    commercial_status: PriceItemStatus
    compulsory_status: PriceItemStatus
    vehicle_tax_status: PriceItemStatus
    package_status: PriceItemStatus
    other_fees_status: PriceItemStatus

    official_total: Decimal | None
    computed_total: Decimal | None
    total_check_status: TotalCheckStatus
    net_payment: Decimal | None
    net_payment_status: NetPaymentStatus
    # 净支出是否含用户自愿填写的折现估值（进入方案列“含用户估值”标注）
    has_user_valuation: bool

    core: Mapping[str, CoverageSnapshot]
    additional: Mapping[str, CoverageSnapshot]
    services: Mapping[str, ServiceSnapshot]
    packages: list[PackageSnapshot]
    discounts: list[DiscountSnapshot]
    # 已确认保留且含金额的未识别项数量（进入方案列异常标注，不进总表）
    unrecognized_money_count: int


# ---- 排序（SPEC §7.1）----


def sort_by_net_payment(quotes: Sequence[QuoteSnapshot]) -> list[tuple[QuoteSnapshot, int]]:
    """净支出升序；null 排最后，组内保持用户传入顺序（Python 稳定排序）。"""
    usable = [q for q in quotes if q.net_payment is not None]
    unusable = [q for q in quotes if q.net_payment is None]
    ordered = sorted(usable, key=lambda q: q.net_payment) + unusable
    return [(quote, rank) for rank, quote in enumerate(ordered)]


# ---- 行构造：预计算 CellPair 后统一打差异标签（SPEC §7.3）----


def _value_key(value: object) -> object:
    """结构化值比较键：Decimal 按数值比较（100 == 100.00），其余按自身。"""
    if isinstance(value, Decimal):
        return float(value)
    return value


def _diff_cell(
    *,
    value: float | str | bool | None,
    baseline: float | str | bool | None,
    text: str,
    numeric: bool,
    is_baseline_column: bool,
) -> CompareCell:
    """构造单元格并相对基准列打差异标签。

    - 数值列：↑ 增 / ↓ 减 / = 相同；
    - 缺失方向：基准缺失当前有值 → + 新增；基准有值当前缺失 → − 缺失；
    - 文本/布尔列：不同只标 diff（前端高亮），不使用箭头（方向语义仅数值成立）；
    - 基准列自身不标箭头（tag=None），保证“第一个方案”不出现误导性方向。
    """
    if is_baseline_column:
        return CompareCell(text=text, value=value, tag=None, diff=False)
    if baseline is None and value is not None:
        return CompareCell(text=text, value=value, tag="ADD", diff=True)
    if baseline is not None and value is None:
        return CompareCell(text=text, value=value, tag="MISS", diff=True)
    if numeric and isinstance(baseline, (int, float)) and isinstance(value, (int, float)):
        if value > baseline:  # type: ignore[operator]
            return CompareCell(text=text, value=value, tag="UP", diff=True)
        if value < baseline:  # type: ignore[operator]
            return CompareCell(text=text, value=value, tag="DOWN", diff=True)
        return CompareCell(text=text, value=value, tag="SAME", diff=False)
    same = _value_key(value) == _value_key(baseline)
    return CompareCell(text=text, value=value, tag="SAME" if same else None, diff=not same)


def _row(
    *,
    key: str,
    label: str,
    kind: str,
    pairs: Sequence[CellPair],
    note: str | None = None,
) -> CompareRow:
    """通用行构造：pairs 与报价顺序一一对应（列 0 为差异基准）。"""
    baseline_value = pairs[0][0]
    cells = [
        _diff_cell(
            value=value,  # type: ignore[arg-type]
            baseline=baseline_value,  # type: ignore[arg-type]
            text=text,
            numeric=kind in ("money", "amount", "count"),
            is_baseline_column=index == 0,
        )
        for index, (value, text) in enumerate(pairs)
    ]
    return CompareRow(
        key=key,
        label=label,
        kind=kind,  # type: ignore[arg-type]
        cells=cells,
        diff=any(cell.diff for cell in cells[1:]),
        note=note,
    )


def _order_diff_first(rows: list[CompareRow]) -> list[CompareRow]:
    """差异行置顶、相同行靠后（组内保持原有相对顺序，稳定可预期）。"""
    return [row for row in rows if row.diff] + [row for row in rows if not row.diff]


def _money_pair(value: Decimal | None, missing_text: str = "—") -> CellPair:
    """金额列预计算：eff 值格式化为 ¥；缺失显示占位文本（绝不当 0）。"""
    return (float(value) if value is not None else None, _fmt_money(value) if value is not None else missing_text)


# ---- 行构建（单一总表：价格 → 核心保障 → 附加险 → 额外保障 → 增值服务 → 优惠/净支出）----


def _price_rows(quotes: Sequence[QuoteSnapshot]) -> list[CompareRow]:
    """价格分组：净支出 + 两个总价 + 校验状态 + 五个分项 eff 值。

    官方总价、含用户估值与校验异常都不得隐藏：净支出缺失时单元格文本
    直接显示“无法计算（总价缺失/优惠超额，请修正）”。
    """
    rows: list[CompareRow] = [
        _row(
            key="net",
            label="实际净支出",
            kind="money",
            pairs=[
                (
                    float(q.net_payment),
                    _fmt_money(q.net_payment),
                )
                if q.net_payment is not None
                else (None, f"无法计算（{_NET_STATUS_TEXT[q.net_payment_status]}）")
                for q in quotes
            ],
            note="净支出 = (官方总价 ?? 系统总价) − 计入折现的优惠",
        ),
        _row(
            key="official_total",
            label="官方总价",
            kind="money",
            pairs=[
                _money_pair(q.official_total, "未识别") for q in quotes
            ],
        ),
        _row(
            key="computed_total",
            label="系统计算总价",
            kind="money",
            pairs=[_money_pair(q.computed_total, "无法计算") for q in quotes],
        ),
        _row(
            key="total_check",
            label="总额校验",
            kind="text",
            pairs=[
                (q.total_check_status.value, _CHECK_TEXT[q.total_check_status]) for q in quotes
            ],
            note="官方总价与系统总价不一致时必须核对后采信",
        ),
    ]
    part_specs: tuple[tuple[str, str, str], ...] = (
        ("commercial", "商业险", "commercial"),
        ("compulsory", "交强险", "compulsory"),
        ("vehicle_tax", "车船税", "vehicle_tax"),
        ("package_total", "独立保障包", "package"),
        ("other_fees", "其他费用", "other_fees"),
    )
    for key, label, prefix in part_specs:
        pairs: list[CellPair] = []
        for quote in quotes:
            eff: Decimal | None = getattr(quote, f"{prefix}_eff")
            status: PriceItemStatus = getattr(quote, f"{prefix}_status")
            if eff is not None:
                pairs.append((float(eff), _fmt_money(eff)))
            elif status == PriceItemStatus.UNKNOWN:
                # eff 缺失且状态未知：口径缺失，不是 0
                pairs.append((None, "未知"))
            else:
                pairs.append((None, "—"))
        rows.append(_row(key=key, label=label, kind="money", pairs=pairs))
    return _order_diff_first(rows)


def _coverage_amount_text(snapshot: CoverageSnapshot) -> str:
    """保额口径文本：单座×座位时展开为“1000 元 × 4 座”。"""
    if snapshot.per_seat_amount is not None and snapshot.seat_count is not None:
        return f"{_fmt_amount(snapshot.per_seat_amount)} × {snapshot.seat_count} 座"
    return _fmt_amount(snapshot.effective_amount())


def _coverage_rows(
    *,
    codes: Sequence[str],
    quotes: Sequence[QuoteSnapshot],
    pick_map: Callable[[QuoteSnapshot], Mapping[str, CoverageSnapshot]],
) -> list[CompareRow]:
    """核心保障/附加险分组：按标准码逐行比较集合、状态、保额、座位、保费等。

    未被任何报价包含的标准码不生成行（避免全“—”空行）；单座/座位/共享/
    倍数/条件仅在任一报价出现时生成行，减少噪音但不丢失差异。
    """
    rows: list[CompareRow] = []
    maps = [pick_map(q) for q in quotes]
    for code in codes:
        definition = COVERAGE_DEFINITIONS.get(code)
        label = definition.label if definition else code
        snapshots = [m.get(code) for m in maps]
        if not any(s is not None for s in snapshots):
            continue

        def pairs_for(
            attr: str,
            fmt: Callable[[Decimal | int | str], str],
            rows: list[CoverageSnapshot | None] = snapshots,
        ) -> list[CellPair]:
            """按属性预计算各列 (结构化值, 展示文本)；缺失列为 (None, "—")。

            rows 通过默认参数绑定当前循环迭代（Ruff B023），
            保证多份完全相同的报价也不会串列。
            """
            out: list[CellPair] = []
            for snapshot in rows:
                value: object = None
                text = "—"
                if snapshot is not None:
                    raw = getattr(snapshot, attr)
                    if raw is not None:
                        if isinstance(raw, Decimal):
                            value = float(raw)
                            text = fmt(raw)
                        elif isinstance(raw, bool):
                            value = raw
                            text = "共享" if raw else "不共享"
                        elif isinstance(raw, int):
                            value = float(raw)
                            text = fmt(raw)
                        else:
                            value = str(raw)
                            text = str(raw)
                out.append((value, text))
            return out

        def fmt_amount(value: Decimal | int | str) -> str:
            return _fmt_amount(value) if isinstance(value, Decimal) else str(value)

        def fmt_money(value: Decimal | int | str) -> str:
            return _fmt_money(value) if isinstance(value, Decimal) else str(value)

        def fmt_seats(value: Decimal | int | str) -> str:
            return f"{value} 座" if isinstance(value, int) else str(value)

        def fmt_multiplier(value: Decimal | int | str) -> str:
            return f"×{float(value):g}" if isinstance(value, Decimal) else str(value)

        rows.append(
            _row(
                key=f"{code}:status",
                label=f"{label}·投保状态",
                kind="text",
                pairs=[
                    (
                        (s.status.value, _ITEM_STATUS_TEXT[s.status]) if s else ("ABSENT", "未投保")
                    )
                    for s in snapshots
                ],
                note="“未投保”指该报价没有此险种行；与“未知（未识别到状态）”含义不同",
            )
        )
        # 保额行：单座×座位口径展开为文本，结构化值取推导后的总额
        rows.append(
            _row(
                key=f"{code}:amount",
                label=f"{label}·保额",
                kind="amount",
                pairs=[
                    (
                        (float(s.effective_amount()), _coverage_amount_text(s))
                        if s and s.effective_amount() is not None
                        else (None, "—")
                    )
                    for s in snapshots
                ],
            )
        )
        if any(s is not None and s.per_seat_amount is not None for s in snapshots):
            rows.append(
                _row(
                    key=f"{code}:per_seat",
                    label=f"{label}·单座保额",
                    kind="amount",
                    pairs=pairs_for("per_seat_amount", fmt_amount),
                )
            )
        if any(s is not None and s.seat_count is not None for s in snapshots):
            rows.append(
                _row(
                    key=f"{code}:seats",
                    label=f"{label}·座位数",
                    kind="count",
                    pairs=pairs_for("seat_count", fmt_seats),
                )
            )
        if any(s is not None and s.premium is not None for s in snapshots):
            rows.append(
                _row(
                    key=f"{code}:premium",
                    label=f"{label}·保费",
                    kind="money",
                    pairs=pairs_for("premium", fmt_money),
                )
            )
        if any(s is not None and s.shared_coverage is not None for s in snapshots):
            rows.append(
                _row(
                    key=f"{code}:shared",
                    label=f"{label}·共享保额",
                    kind="text",
                    pairs=pairs_for("shared_coverage", fmt_amount),
                )
            )
        if any(s is not None and s.multiplier is not None for s in snapshots):
            rows.append(
                _row(
                    key=f"{code}:multiplier",
                    label=f"{label}·倍数",
                    kind="count",
                    pairs=pairs_for("multiplier", fmt_multiplier),
                )
            )
        if any(s is not None and s.condition for s in snapshots):
            rows.append(
                _row(
                    key=f"{code}:condition",
                    label=f"{label}·生效条件",
                    kind="text",
                    pairs=pairs_for("condition"),
                )
            )
    return _order_diff_first(rows)


def _package_item_text(item: PackageCoverageSnapshot) -> str | None:
    """保障包内部保障的口径文本（状态/保额+单位/倍数/条件）。"""
    if item.status == ItemStatus.NOT_INCLUDED:
        return "不包含"
    parts: list[str] = []
    if item.coverage_amount is not None:
        parts.append(_fmt_unit_amount(item.coverage_amount, item.unit))
    if item.multiplier is not None:
        parts.append(f"×{float(item.multiplier):g}")
    if item.condition:
        parts.append("法定节假日" if item.condition == "LEGAL_HOLIDAY" else item.condition)
    if not parts:
        parts.append(_ITEM_STATUS_TEXT[item.status])
    return " · ".join(parts)


def _package_rows(quotes: Sequence[QuoteSnapshot]) -> list[CompareRow]:
    """额外保障分组：保障包按包名比较保费，并展开内部保障逐行比较。

    不同公司的包名通常不同 → 大量 +/− 行属预期行为；内部保障按类型码
    对齐（标签来自 §3.3 码表，未知类型回退原码）。
    """
    rows: list[CompareRow] = []
    # 包名顺序：跨报价按首次出现对齐同一键（基准列的包自然排前）
    names: list[str] = []
    for quote in quotes:
        for package in quote.packages:
            if package.name not in names:
                names.append(package.name)

    for name in names:
        packages_by_quote = [
            next((p for p in quote.packages if p.name == name), None) for quote in quotes
        ]
        rows.append(
            _row(
                key=f"pkg:{name}:premium",
                label=f"{name}·保费",
                kind="money",
                pairs=[
                    _money_pair(p.premium) if p else (None, "—")
                    for p in packages_by_quote
                ],
            )
        )
        # 内部保障类型顺序：跨报价按首次出现
        type_order: list[str] = []
        for package in packages_by_quote:
            if package is None:
                continue
            for item in package.coverages:
                if item.type not in type_order:
                    type_order.append(item.type)
        for type_code in type_order:
            type_label = PACKAGE_COVERAGE_DEFINITIONS.get(type_code, type_code)
            items_by_quote = [
                next(
                    (c for c in p.coverages if c.type == type_code), None
                )
                if p is not None
                else None
                for p in packages_by_quote
            ]
            rows.append(
                _row(
                    key=f"pkg:{name}:{type_code}",
                    label=f"{name}·{type_label}",
                    kind="text",
                    pairs=[
                        (
                            (text, text)
                            if item is not None
                            and (text := _package_item_text(item)) is not None
                            else (None, "—")
                        )
                        for item in items_by_quote
                    ],
                )
            )
    return _order_diff_first(rows)


def _service_rows(quotes: Sequence[QuoteSnapshot]) -> list[CompareRow]:
    """增值服务分组：按服务类型比较“状态 · 次数 · 费用”（每类型一行）。

    服务类型是服务比较的稳定业务键；OTHER 恒排最后，与字典顺序一致。
    """
    rows: list[CompareRow] = []
    types: list[str] = []
    for quote in quotes:
        for type_code in quote.services:
            if type_code not in types:
                types.append(type_code)
    types.sort(key=lambda t: (t == "OTHER", t))
    for type_code in types:
        label = _SERVICE_LABELS.get(type_code, type_code)
        pairs: list[CellPair] = []
        for quote in quotes:
            service = quote.services.get(type_code)
            if service is None:
                pairs.append((None, "—"))
                continue
            if service.status == ItemStatus.NOT_INCLUDED:
                pairs.append((service.status.value, "不包含"))
                continue
            parts = [_ITEM_STATUS_TEXT[service.status]]
            if service.count is not None:
                parts.append(f"{service.count} 次")
            if service.cost is not None:
                parts.append(_fmt_money(service.cost))
            text = " · ".join(parts)
            pairs.append((text, text))
        rows.append(
            _row(key=f"svc:{type_code}", label=label, kind="text", pairs=pairs)
        )
    return _order_diff_first(rows)


def _discount_rows(quotes: Sequence[QuoteSnapshot]) -> list[CompareRow]:
    """优惠/净支出分组：逐笔优惠 + 折现合计 + 净支出状态说明。

    优惠键 = 类型 + 描述（用户自由填写，没有更稳定的业务键）；名义金额
    仅展示，绝不参与净支出（SPEC §2.7）。
    """
    rows: list[CompareRow] = []
    keys: list[tuple[str, str]] = []
    for quote in quotes:
        for discount in quote.discounts:
            key = (discount.discount_type, discount.description or "")
            if key not in keys:
                keys.append(key)

    type_labels: Mapping[str, str] = STATUS_LABELS["discountType"]
    for key in keys:
        label_text = type_labels.get(key[0], key[0])
        row_label = f"{label_text}·{key[1]}" if key[1] else label_text
        pairs: list[CellPair] = []
        for quote in quotes:
            discount = next(
                (
                    d
                    for d in quote.discounts
                    if d.discount_type == key[0] and (d.description or "") == key[1]
                ),
                None,
            )
            if discount is None:
                pairs.append((None, "—"))
            elif not discount.include_in_net:
                nominal = _fmt_money(discount.amount) if discount.amount is not None else "—"
                pairs.append(("EXCLUDED", f"不计入（名义 {nominal}）"))
            elif discount.cash_equivalent is not None:
                pairs.append(
                    (float(discount.cash_equivalent), f"计入折现 {_fmt_money(discount.cash_equivalent)}")
                )
            else:
                pairs.append(("NO_CASH", "计入但无折现估值（不减钱）"))
        rows.append(
            _row(
                key=f"discount:{key[0]}:{key[1]}",
                label=row_label,
                kind="text",
                pairs=pairs,
            )
        )

    deduction_pairs: list[CellPair] = []
    for quote in quotes:
        total = sum(
            (
                d.cash_equivalent
                for d in quote.discounts
                if d.include_in_net and d.cash_equivalent is not None
            ),
            start=Decimal("0"),
        )
        deduction_pairs.append((float(total), _fmt_money(total)))
    rows.append(
        _row(key="deduction_total", label="计入折现合计", kind="money", pairs=deduction_pairs)
    )
    rows.append(
        _row(
            key="net_status",
            label="净支出状态",
            kind="text",
            pairs=[
                (q.net_payment_status.value, _NET_STATUS_TEXT[q.net_payment_status])
                for q in quotes
            ],
            note="总价缺失或优惠超额时净支出不可用，不参与最低价判定",
        )
    )
    return _order_diff_first(rows)


# ---- 元信息与总装 ----


def _quote_meta(
    quote: QuoteSnapshot,
    *,
    diff_baseline_id: int,
    price_baseline_id: int | None,
    rank_by_id: Mapping[int, int],
) -> CompareQuoteMeta:
    """方案列元信息：异常/口径标注由服务端给出，前端不得隐藏。"""
    annotations: list[str] = []
    if quote.total_check_status == TotalCheckStatus.MISMATCH:
        annotations.append("官方总价异常")
    if quote.has_user_valuation:
        annotations.append("含用户估值")
    if quote.net_payment_status == NetPaymentStatus.MISSING_TOTAL:
        annotations.append("总价缺失")
    if quote.net_payment_status == NetPaymentStatus.INVALID_DISCOUNT:
        annotations.append("优惠超额，请修正")
    if quote.status == QuoteStatus.MERGE_REVIEW:
        annotations.append("合并确认中：对比读取已确认旧值")
    if quote.unrecognized_money_count > 0:
        annotations.append(f"{quote.unrecognized_money_count} 项未识别保障未参与对比")
    return CompareQuoteMeta(
        quote_id=quote.quote_id,
        display_name=quote.display_name,
        insurer_code=quote.insurer_code,
        insurer_name=quote.insurer_name,
        agent_name=quote.agent_name,
        plan_label=quote.plan_label,
        status_label=STATUS_LABELS["quoteStatus"].get(quote.status.value, quote.status.value),
        is_diff_baseline=quote.quote_id == diff_baseline_id,
        is_price_baseline=quote.quote_id == price_baseline_id,
        price_rank=rank_by_id.get(quote.quote_id),
        annotations=annotations,
    )


def build_comparison(quotes: Sequence[QuoteSnapshot], project_id: int) -> ComparisonResult:
    """总装入口：排序与单一总表行一次算齐（只读，不修改任何报价数据）。"""
    ordered = sort_by_net_payment(quotes)
    rank_by_id = {q.quote_id: rank for q, rank in ordered}
    usable = [q for q in quotes if q.net_payment is not None]
    # 价格基准：最低净支出报价；并列时取用户勾选顺序在前者
    price_baseline = None
    if usable:
        minimum = min(q.net_payment for q in usable)
        price_baseline = next(q for q in quotes if q.net_payment == minimum)
    price_baseline_id = price_baseline.quote_id if price_baseline else None
    diff_baseline_id = quotes[0].quote_id

    metas = [
        _quote_meta(
            quote,
            diff_baseline_id=diff_baseline_id,
            price_baseline_id=price_baseline_id,
            rank_by_id=rank_by_id,
        )
        for quote in quotes
    ]
    price_order = [
        PriceOrderEntry(
            quote_id=quote.quote_id,
            net_payment=float(quote.net_payment) if quote.net_payment is not None else None,
            net_payment_status=quote.net_payment_status,
            official_total=(
                float(quote.official_total) if quote.official_total is not None else None
            ),
            total_check_status=quote.total_check_status,
            has_user_valuation=quote.has_user_valuation,
            rank=rank,
        )
        for quote, rank in ordered
    ]

    additional_codes = [
        code
        for code, definition in COVERAGE_DEFINITIONS.items()
        if definition.category == CoverageCategory.ADDITIONAL.value
    ]
    # 单一总表：六个分组的行按固定顺序平铺（各分组内差异行已置顶）
    rows = [
        *_price_rows(quotes),
        *_coverage_rows(
            codes=CORE_COMPARE_CODES,
            quotes=quotes,
            pick_map=lambda q: q.core,
        ),
        *_coverage_rows(
            codes=additional_codes,
            quotes=quotes,
            pick_map=lambda q: q.additional,
        ),
        *_package_rows(quotes),
        *_service_rows(quotes),
        *_discount_rows(quotes),
    ]

    return ComparisonResult(
        project_id=project_id,
        quotes=metas,
        price_order=price_order,
        diff_baseline_quote_id=diff_baseline_id,
        price_baseline_quote_id=price_baseline_id,
        rows=rows,
        disclaimer=DISCLAIMER,
    )
