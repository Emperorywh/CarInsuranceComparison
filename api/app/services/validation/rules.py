"""校验规则与置信度三档合成（SPEC §4.2、§6；TASK-04 范围 8）。

职责边界：
- 本模块只承载“解析候选”侧的确定性规则；价格三态与净支出计算仍在
  services/pricing.py，两者由流水线在同一事务内先后调用；
- 全部为纯函数：置信度合成只看布尔/枚举信号，不做任何 IO；
- 高风险语义（司机/乘客、三个医保外对象、保障包与主险隔离、明确 0 元
  服务才是 FREE）在归一化引擎 + 本模块 + 数据库约束三层保证。

置信度合成（SPEC §4.2）：LOW > MEDIUM > HIGH，任一命中即取最低档：
- LOW：总额校验失败且字段参与合计 / 证据指向不存在的文件或页码 /
  金额为负或异常量级 / 模型自报 < 0.6；
- MEDIUM：自报为空或 < 0.85 / 无 evidence / 参与金额计算但总额
  NOT_CHECKABLE / UNRECOGNIZED / 触发保额档位或新能源一致性提示；
- HIGH：其余。
"""

from __future__ import annotations

from decimal import Decimal

from app.models.enums import ConfidenceLevel, ItemStatus

# 模型自报置信度的两道阈值（SPEC §4.2）
LOW_SELF_CONFIDENCE = 0.6
MEDIUM_SELF_CONFIDENCE = 0.85

# 低质量集中提示阈值（SPEC §12：图片模糊/低质量）
LOW_RATIO_THRESHOLD = 0.2
MEDIUM_LOW_RATIO_THRESHOLD = 0.5


def _self_band(self_confidence: float | None) -> str:
    """把模型自报分数归入 none / low / medium / high 四个信号带。"""
    if self_confidence is None:
        return "none"
    if self_confidence < LOW_SELF_CONFIDENCE:
        return "low"
    if self_confidence < MEDIUM_SELF_CONFIDENCE:
        return "medium"
    return "high"


def synthesize_confidence(
    *,
    self_confidence: float | None,
    evidence_state: str,
    participates_in_total: bool = False,
    total_check_status: str | None = None,
    unrecognized: bool = False,
    range_hint: bool = False,
    nev_inconsistent: bool = False,
    negative_amount: bool = False,
    other_medium_hint: bool = False,
) -> ConfidenceLevel:
    """合成单个字段/行的置信度档位。

    evidence_state 取值：
    - "ok"：存在且合法（fileKey 属于本任务、页码在范围内）；
    - "missing"：模型未给出 evidence（MEDIUM，SPEC §4.2）；
    - "invalid"：给出了但非法（fileKey 未知 / 页码越界）——LOW，
      且上层必须丢弃该来源定位，绝不伪造 sourceFileId（SPEC §6.9）。
    total_check_status 只关心 "MISMATCH"（LOW）与 "NOT_CHECKABLE"（MEDIUM）。
    other_medium_hint：其他应降为 MEDIUM 的提示信号（如保障包类型码
    非法被改为 OTHER），与 range_hint / nev_inconsistent 同档。
    """
    if evidence_state == "invalid":
        return ConfidenceLevel.LOW
    if negative_amount:
        return ConfidenceLevel.LOW
    if participates_in_total and total_check_status == "MISMATCH":
        return ConfidenceLevel.LOW

    band = _self_band(self_confidence)
    if band == "low":
        return ConfidenceLevel.LOW
    if evidence_state == "missing":
        return ConfidenceLevel.MEDIUM
    if unrecognized or range_hint or nev_inconsistent or other_medium_hint:
        return ConfidenceLevel.MEDIUM
    if participates_in_total and total_check_status == "NOT_CHECKABLE":
        return ConfidenceLevel.MEDIUM
    if band in ("none", "medium"):
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.HIGH


def resolve_service_status(status: ItemStatus, cost: Decimal | None) -> ItemStatus:
    """增值服务状态语义（SPEC §6.6 / §12）。

    - 只有“明确列出且费用为 0”才是 FREE：模型标 INCLUDED/UNKNOWN 但
      费用明确为 0 → 修正为 FREE；费用缺失绝不推断为免费；
    - 模型标 FREE 但费用缺失或非 0 → 降为 UNKNOWN（FREE 需 0 元佐证）；
    - 其他状态（NOT_INCLUDED/NOT_APPLICABLE）原样保留。
    """
    zero_cost = cost is not None and cost == 0
    if status in (ItemStatus.INCLUDED, ItemStatus.UNKNOWN) and zero_cost:
        return ItemStatus.FREE
    if status == ItemStatus.FREE and not zero_cost:
        return ItemStatus.UNKNOWN
    return status


# ---- 新能源一致性（SPEC §6.8，仅提示不阻断）----

_FUEL_WORDING = ("机动车损失", "机动车第三者", "机动车商业")
_NEV_WORDING = ("新能源汽车", "新能源")


def nev_inconsistent(is_nev: bool | None, raw_name: str | None) -> bool:
    """判断险种措辞与新能源标识是否矛盾；无法判断（is_nev 为空）返回 False。"""
    if is_nev is None or not raw_name:
        return False
    cleaned = raw_name.strip()
    has_fuel = any(word in cleaned for word in _FUEL_WORDING)
    has_nev = any(word in cleaned for word in _NEV_WORDING)
    if is_nev:
        return has_fuel
    return has_nev


# ---- 低质量集中提示（SPEC §12）----


def low_quality_warning(levels: list[ConfidenceLevel]) -> str | None:
    """候选字段置信度分布触发阈值时返回确认页顶部提示文案。

    规则：LOW 占比 ≥20%，或 MEDIUM+LOW 合计 ≥50%；由调用方收集全部
    候选行/标量证据的置信度（用户已确认字段不参与，避免误报）。
    """
    total = len(levels)
    if total == 0:
        return None
    low = sum(1 for level in levels if level == ConfidenceLevel.LOW)
    medium = sum(1 for level in levels if level == ConfidenceLevel.MEDIUM)
    if low / total >= LOW_RATIO_THRESHOLD:
        return "较多字段置信度低，请逐项核对原文后再确认"
    if (low + medium) / total >= MEDIUM_LOW_RATIO_THRESHOLD:
        return "过半字段置信度不足，建议核对原单或重新解析"
    return None
