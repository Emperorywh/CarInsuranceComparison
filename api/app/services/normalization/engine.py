"""归一化引擎（SPEC §3）：险种 / 公司 / 服务 / 保障包类型的确定性映射。

设计不变量：
- 匹配策略固定为三层有序匹配：精确（别名表）→ 清洗后精确（去空格、
  统一全半角括号等）→ 关键词组合包含；任何一层命中即停止，不跨层猜测；
- 无法映射的险种一律返回 None（进入 UNRECOGNIZED 区，由用户手动映射），
  绝不猜测类别；公司与服务映射失败分别返回 None / OTHER；
- 高风险区分（司机 vs 乘客、三个医保外对象）依赖“关键词组合必须同时
  命中”的规则而非单词包含：目标词（第三者/司机/乘客）缺失时不映射；
- 本模块是纯函数集合：解析流水线（TASK-04）与确认页手动映射共用同一套
  字典与规则，禁止在路由层另写映射。
"""

from __future__ import annotations

import re

from app.services.normalization.alias_map import (
    INSURER_DEFINITIONS,
    PACKAGE_COVERAGE_DEFINITIONS,
)

# ---- 文本清洗（第二层匹配的预处理）----

# 全角括号/冒号/中点/连接符统一为常见半角或删除，提高变体归并能力
_CLEAN_RE = re.compile(r"[（）()\[\]【】\s　:：·、，,。.\-—_/\\]")


def clean_name(raw: str | None) -> str:
    """清洗险种/公司/服务名称：去空白与全半角括号等干扰符号，转小写。

    只用于匹配比较，不改变落库的原始名称（rawName 永远保存模型原文）。
    """
    if not raw:
        return ""
    return _CLEAN_RE.sub("", raw).lower()


# ---- 险种映射（SPEC §3.1）----

# 关键词规则表：按数组顺序求值，首个命中的规则决定映射结果。
# 每条规则是若干“关键词组合”，组合内全部关键词都出现才命中（AND 语义）；
# 多个组合之间任一命中即可（OR 语义）。顺序即优先级：
# 1) 医保外三对象（必须带目标词，缺失时不映射，防止三者/司机/乘客互换）；
# 2) 电网（先于车损，避免“电网故障损失险”被“损失”误伤）；
# 3) 交强 / 三者 / 车损；
# 4) 车上人员与座位（先司机后乘客）；
# 5) 其余附加险。
_COVERAGE_RULES: list[tuple[str, tuple[tuple[str, ...], ...]]] = [
    ("TP_NON_MEDICAL", ((("医保外"), ("第三者")), (("医保外"), ("三者")))),
    ("DRIVER_NON_MEDICAL", ((("医保外"), ("司机")), (("医保外"), ("驾驶员")))),
    ("PASSENGER_NON_MEDICAL", ((("医保外"), ("乘客")), (("医保外"), ("乘车")))),
    ("EXTERNAL_GRID", ((("电网"),),)),
    ("COMPULSORY", ((("交强"),), (("交通事故责任强制保险"),))),
    ("THIRD_PARTY_LIABILITY", ((("第三者"),), (("三者"),))),
    ("VEHICLE_LOSS", ((("车损"),), (("车辆损失"),), (("机动车损失"),), (("损失保险"),))),
    ("DRIVER_LIABILITY", (
        (("车上人员"), ("司机")), (("车上人员"), ("驾驶员")),
        (("座位"), ("司机")), (("座位"), ("驾驶员")),
        (("司机险"),), (("驾驶员险"),), (("司机座位"),), (("驾驶员座位"),),
    )),
    ("PASSENGER_LIABILITY", (
        (("车上人员"), ("乘客")), (("座位"), ("乘客")),
        (("乘客险"),), (("乘客座位"),), (("乘客责任"),),
    )),
    ("GLASS_BROKEN", ((("玻璃"),),)),
    ("SCRATCH", ((("划痕"),),)),
    ("REPAIR_PERIOD_COMP", ((("修理期间"),),)),
    ("SPIRIT_DAMAGE", ((("精神损害"),),)),
    ("FIND_VEHICLE", ((("找回车辆"),), (("寻找车辆"),))),
]


def _build_exact_index() -> dict[str, str]:
    """精确匹配索引：显示名 + 全部别名 → 标准码（清洗后比较）。"""
    from app.services.normalization.alias_map import COVERAGE_DEFINITIONS

    index: dict[str, str] = {}
    for definition in COVERAGE_DEFINITIONS.values():
        index[clean_name(definition.label)] = definition.code
        for alias in definition.aliases:
            index[clean_name(alias)] = definition.code
    return index


_COVERAGE_EXACT: dict[str, str] = _build_exact_index()


def _rule_hit(cleaned: str, keywords: tuple[str, ...]) -> bool:
    return all(keyword in cleaned for keyword in keywords)


def match_coverage(raw_name: str | None) -> str | None:
    """原始险种名 → 标准码；三层有序匹配失败返回 None（进 UNRECOGNIZED）。"""
    if not raw_name or not raw_name.strip():
        return None
    cleaned = clean_name(raw_name)
    if not cleaned:
        return None
    # 第一层：精确匹配（原始与清洗后各试一次）
    hit = _COVERAGE_EXACT.get(clean_name(raw_name)) or _COVERAGE_EXACT.get(cleaned)
    if hit:
        return hit
    # 第三层：有序关键词组合；第二层（清洗后精确）已并入索引，无需单独处理
    for code, rule_sets in _COVERAGE_RULES:
        for keywords in rule_sets:
            if _rule_hit(cleaned, keywords):
                return code
    return None


# ---- 保险公司映射（SPEC §3.2）----

# 公司别名表：标准码 → 别名集合。“国寿财险”与“国元保险”是两家公司，
# 别名互不重叠，包含式匹配也不得互相命中（别名单独成词）。
_INSURER_ALIASES: dict[str, tuple[str, ...]] = {
    "PICC": ("人保", "中国人保", "人保财险", "人保股份", "picc", "中国人民保险", "人民保险"),
    "PINGAN": ("平安", "中国平安", "平安产险", "平安财险", "平安保险"),
    "CPIC": ("太平洋", "中国太平洋", "太平洋产险", "太平洋财险", "太保", "cpic"),
    "CHINALIFE_PC": ("国寿财险", "国寿财", "中国人寿财产", "人寿财产", "人寿财险"),
    "GUOYUAN": ("国元保险", "国元财险", "国元农业", "国元"),
    "DADI": ("大地", "中国大地", "大地财险", "大地保险"),
    "SUNSHINE": ("阳光", "阳光财产", "阳光财险", "阳光保险"),
    "ZHONGAN": ("众安", "众安在线", "众安财产"),
}


def match_insurer(raw_name: str | None) -> str | None:
    """公司名 → 标准码（仅预置 8 家）；其他公司返回 None（保持自由名）。"""
    if not raw_name or not raw_name.strip():
        return None
    cleaned = clean_name(raw_name)
    if not cleaned:
        return None
    # 先标准码与显示名精确匹配，再做别名包含（别名出现在公司名中即命中）
    for code, label in INSURER_DEFINITIONS.items():
        if code != "OTHER" and cleaned == clean_name(label):
            return code
    for code, aliases in _INSURER_ALIASES.items():
        for alias in aliases:
            if alias in cleaned:
                return code
    return None


# ---- 增值服务映射（SPEC §3.4）----

# 顺序即优先级：代办送检先于检测（“代办送检”含“送检”，不能落 INSPECTION）。
_SERVICE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("INSPECTION_AGENT", ("代办送检", "代为送检", "送检代办", "代办年检")),
    ("DRIVER_SERVICE", ("代驾", "代为驾驶")),
    ("INSPECTION", ("安全检测", "车辆检测", "年检", "检测", "验车")),
    ("ROAD_RESCUE", ("道路救援", "救援")),
]


def match_service(raw_name: str | None) -> str:
    """服务名 → serviceType；未命中返回 OTHER（确认页可改选，不降置信度）。"""
    if not raw_name:
        return "OTHER"
    cleaned = clean_name(raw_name)
    for code, keywords in _SERVICE_RULES:
        if any(keyword in cleaned for keyword in keywords):
            return code
    return "OTHER"


# ---- 保障包内部类型映射（SPEC §3.3）----

# 顺序即优先级：驾乘/乘客意外先于汽车意外；节假日翻倍先于各类意外。
_PACKAGE_TYPE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("HOLIDAY_DOUBLE", ("节假日翻倍", "节假日")),
    ("DRIVER_ACCIDENT", ("驾乘意外", "驾乘", "司机意外", "驾驶员意外")),
    ("PASSENGER_ACCIDENT", ("乘客意外",)),
    ("SELF_PAID_MEDICAL", ("自费医疗", "自费药")),
    ("AIR_ACCIDENT", ("飞机", "航空")),
    ("TRAIN_ACCIDENT", ("火车", "高铁", "动车", "轨道")),
    ("SHIP_ACCIDENT", ("轮船", "船舶", "渡轮")),
    ("AMBULANCE_FEE", ("救护车",)),
    ("TRAVEL_INCONVENIENCE", ("出行不便", "旅程延误", "行程延误", "航班延误")),
    ("FAMILY_PROPERTY", ("家庭财产", "家财")),
    ("LUGGAGE_LOSS", ("行李",)),
    ("VEHICLE_ACCIDENT", ("汽车意外", "机动车意外")),
]


def match_package_type(raw_text: str | None) -> str:
    """保障包内部保障文本 → 类型码；未命中返回 OTHER，不臆测单位与金额。"""
    if not raw_text:
        return "OTHER"
    cleaned = clean_name(raw_text)
    for code, keywords in _PACKAGE_TYPE_RULES:
        if any(keyword in cleaned for keyword in keywords) and code in PACKAGE_COVERAGE_DEFINITIONS:
            return code
    return "OTHER"


# ---- 条件归一化 ----

# 条件取值的白名单语义：MVP 只有节假日翻倍是确定出现的条件（SPEC §4.1 示例）。
# 中文“节假日”统一映射为 LEGAL_HOLIDAY 便于对比引擎按常量比较；
# 其余条件保留脱敏后的原文，由用户在确认页修正。
_ALLOWED_CONDITIONS = ("LEGAL_HOLIDAY",)


def normalize_condition(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip()
    if "节假日" in cleaned:
        return "LEGAL_HOLIDAY"
    if cleaned in _ALLOWED_CONDITIONS:
        return cleaned
    return cleaned
