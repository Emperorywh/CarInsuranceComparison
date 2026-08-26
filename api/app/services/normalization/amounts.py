"""确定性数值与文本规则（SPEC §6 中不依赖模型的部分）。

全部为纯函数：TASK-02 手动录入路径与 TASK-04 解析流水线共用同一套规则，
任何函数都不得把 null/缺失值自行当作 0。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.core.errors import ValidationError
from app.models.enums import ItemStatus

TWO_PLACES = Decimal("0.01")

# ---- 金额文本解析（SPEC §6.2 元/万换算、千分位清理）----

# 千分位：仅当“3 位一组”的规范写法才去逗号，避免把非千分位逗号误当分隔符
_THOUSANDS_RE = re.compile(r"\d{1,3}(?:,\d{3})+")
# “300万”“300万元”“0.1万”
_WAN_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*万(?:元)?$")
# 纯数字金额，可带“元”后缀
_PLAIN_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*元?$")


def _strip_thousands(text: str) -> str:
    """去除规范千分位逗号（1,237.41 → 1237.41）。"""
    return _THOUSANDS_RE.sub(lambda m: m.group(0).replace(",", ""), text)


def parse_cn_amount(text: str | None) -> Decimal | None:
    """把中文金额文本解析为元单位数值；无法安全解析返回 None（不猜测）。

    支持：300万 / 300万元 / 0.1万 / 1,237.41元 / 1045。
    “0.1万/座×4”这类复合表达式不属于本函数职责（见 parse_seat_expression）。
    """
    if not text:
        return None
    cleaned = _strip_thousands(text.strip())
    if match := _WAN_RE.match(cleaned):
        return (Decimal(match.group(1)) * Decimal(10000)).quantize(TWO_PLACES)
    if match := _PLAIN_RE.match(cleaned):
        return Decimal(match.group(1)).quantize(TWO_PLACES)
    return None


# ---- 座位表达式（SPEC §6.3）----

# “0.1万/座 × 4”“0.1万元/座×4”（金额在前）
_SEAT_AMOUNT_FIRST = re.compile(
    r"^(\d+(?:\.\d+)?\s*万?)(?:元)?\s*/\s*座\s*[x×*]\s*(\d+)$"
)
# “4座 × 10000元”“4座×0.1万”（座位在前）
_SEAT_COUNT_FIRST = re.compile(
    r"^(\d+)\s*座\s*[x×*]\s*(\d+(?:\.\d+)?\s*万?)(?:元)?$"
)


def parse_seat_expression(text: str | None) -> tuple[Decimal, int] | None:
    """解析“单座保额 × 座位数”表达式 → (单座金额元, 座位数)。

    例：“0.1万/座 × 4” → (1000, 4)；“4 座 × 10000 元” → (10000, 4)。
    无法安全解析返回 None。
    """
    if not text:
        return None
    cleaned = _strip_thousands(text.strip()).replace(" ", "")
    if match := _SEAT_AMOUNT_FIRST.match(cleaned):
        amount_part, seat_part = match.group(1), match.group(2)
    elif match := _SEAT_COUNT_FIRST.match(cleaned):
        seat_part, amount_part = match.group(1), match.group(2)
    else:
        return None
    amount = parse_cn_amount(amount_part)
    if amount is None or not seat_part.isdigit():
        return None
    return amount, int(seat_part)


def resolve_seat_amounts(
    per_seat: Decimal | None,
    seat_count: int | None,
    coverage_amount: Decimal | None,
) -> Decimal | None:
    """座位总额规则：总额 = 单座 × 座位（SPEC §6.3）。

    - 单座与座位齐备但未填总额 → 自动计算总额；
    - 三者齐备但总额与“单座×座位”不一致 → 抛 422（不允许静默采用任一值）；
    - 单座或座位缺失时不做推导，原样返回总额（缺失不得自行当 0）。
    """
    if per_seat is None or seat_count is None:
        return coverage_amount
    expected = (per_seat * seat_count).quantize(TWO_PLACES)
    if coverage_amount is None:
        return expected
    if coverage_amount != expected:
        raise ValidationError(
            f"保额总额与“单座 × 座位”不一致：应为 {expected} 元，"
            f"当前为 {coverage_amount} 元，请修正后重试"
        )
    return coverage_amount


# ---- 状态语义（SPEC §6.6）----

# 明确否定词：出现即 NOT_INCLUDED（“明确写有不投保/无/不包含才直接记为不包含”）
_NEGATIVE_WORDS = ("不投保", "未投保", "无需投保", "不包含", "不含此项", "无此项")
# 占位符号：行列语义不明确时一律 UNKNOWN，不得猜成 NOT_INCLUDED
_PLACEHOLDER_VALUES = {"—", "-", "－", "/", "无值"}


def derive_item_status(raw_text: str | None, *, is_service: bool = False) -> ItemStatus:
    """从原始文本推导险种/服务行的状态语义（SPEC §6.6）。

    - 空/占位（“—”等）→ UNKNOWN；
    - 明确否定词 → NOT_INCLUDED；
    - 服务行且明确 0 元/免费 → FREE（险种行明确 0 元保费仍是 INCLUDED）；
    - 其余（明确列出且带内容）→ INCLUDED。
    该规则只覆盖确定性信号，解析流水线（TASK-04）可在其上叠加上下文修正。
    """
    if raw_text is None:
        return ItemStatus.UNKNOWN
    cleaned = raw_text.strip()
    if not cleaned or cleaned in _PLACEHOLDER_VALUES:
        return ItemStatus.UNKNOWN
    if any(word in cleaned for word in _NEGATIVE_WORDS):
        return ItemStatus.NOT_INCLUDED
    if is_service and ("免费" in cleaned or re.search(r"0(\.0+)?\s*元", cleaned)):
        return ItemStatus.FREE
    return ItemStatus.INCLUDED


# ---- 重复行判定（SPEC §6.4）----


@dataclass(frozen=True)
class RowIdentity:
    """参与重复判定的行字段：只有全部相同的两行才可自动去重。

    同 code 但内容不同的行不得丢弃，必须交给用户确认（确认页合并/保留）。
    """

    raw_name: str | None
    raw_value: str | None
    coverage_amount: Decimal | None
    premium: Decimal | None
    evidence_key: tuple[int | None, int | None, str | None]


def is_duplicate_row(a: RowIdentity, b: RowIdentity) -> bool:
    return a == b


# ---- 数值范围提示（SPEC §6.7，仅提示不阻断）----

_RANGE_RULES: dict[str, tuple[Decimal, Decimal, str]] = {
    # code: (下限, 上限, 字段中文名)——超出常见档位提示用户核对，不拒绝录入
    "THIRD_PARTY_LIABILITY": (Decimal(500_000), Decimal(10_000_000), "三者险保额"),
    "VEHICLE_LOSS": (Decimal(10_000), Decimal(5_000_000), "车损保额"),
}


def check_amount_range(code: str | None, amount: Decimal | None) -> str | None:
    """保额超出常见范围时返回中文提示文案；范围内或无规则的返回 None。"""
    if code is None or amount is None:
        return None
    rule = _RANGE_RULES.get(code)
    if rule is None:
        return None
    low, high, label = rule
    if amount < low or amount > high:
        return f"{label}超出常见范围（{low / 10000:.0f} 万 – {high / 10000:.0f} 万），请核对"
    return None


def to_decimal_or_none(value: object) -> Decimal | None:
    """把请求中的数值安全转为 Decimal；非法值返回 None 交由上层校验报错。"""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
