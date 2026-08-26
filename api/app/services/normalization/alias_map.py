"""标准码表字典（SPEC §3.1–§3.3）：险种、保险公司、保障包内部类型。

单一代码来源：
- 前后端展示值经 /api/dictionaries 由本模块驱动，不各自复制一套字典；
- 别名初始映射来自 SPEC，持续扩充时只改本文件（代码即配置，git 管理）；
- COMPULSORY 仅作识别映射码：交强险只落 Quote 价格字段与 field_evidence，
  不允许生成 quote_coverage 行（row_selectable=False）。
"""

from __future__ import annotations

from dataclasses import dataclass

# ---- 险种类别（字典展示层用；行级 category 仍以 CoverageCategory 枚举为准）----
CATEGORY_CORE = "CORE"
CATEGORY_ADDITIONAL = "ADDITIONAL"
# 交强险特殊类别：只允许出现在价格分项，不允许作为险种行
CATEGORY_COMPULSORY_ONLY = "COMPULSORY"


@dataclass(frozen=True)
class CoverageDefinition:
    """标准险种定义：码、显示名、类别与别名集合。"""

    code: str
    label: str
    category: str
    aliases: tuple[str, ...] = ()
    row_selectable: bool = True


COVERAGE_DEFINITIONS: dict[str, CoverageDefinition] = {
    # ---- CORE 主险（SPEC §3.1）----
    "COMPULSORY": CoverageDefinition(
        code="COMPULSORY",
        label="交强险",
        category=CATEGORY_COMPULSORY_ONLY,
        aliases=("交通事故责任强制保险", "交强"),
        row_selectable=False,
    ),
    "VEHICLE_LOSS": CoverageDefinition(
        code="VEHICLE_LOSS",
        label="车损险",
        category=CATEGORY_CORE,
        aliases=(
            "新能源汽车车损失保险",
            "新能源汽车损失保险",
            "机动车损失保险",
            "车辆损失保险",
        ),
    ),
    "THIRD_PARTY_LIABILITY": CoverageDefinition(
        code="THIRD_PARTY_LIABILITY",
        label="三者险",
        category=CATEGORY_CORE,
        aliases=(
            "新能源汽车第三者责任保险",
            "新能源汽车车第三者责任保险",
            "机动车第三者责任保险",
            "商业第三者责任险",
            "第三者责任险",
        ),
    ),
    "DRIVER_LIABILITY": CoverageDefinition(
        code="DRIVER_LIABILITY",
        label="司机险",
        category=CATEGORY_CORE,
        aliases=(
            "新能源汽车车上人员责任保险（司机）",
            "车上人员责任险：驾驶员",
            "驾驶员座位",
        ),
    ),
    "PASSENGER_LIABILITY": CoverageDefinition(
        code="PASSENGER_LIABILITY",
        label="乘客险",
        category=CATEGORY_CORE,
        aliases=(
            "新能源汽车车上人员责任保险（乘客）",
            "车上人员责任险：乘客",
            "乘客座位",
        ),
    ),
    # ---- ADDITIONAL 附加险（SPEC §3.1）----
    "TP_NON_MEDICAL": CoverageDefinition(
        code="TP_NON_MEDICAL",
        label="三者医保外",
        category=CATEGORY_ADDITIONAL,
        aliases=(
            "附加医保外医疗费用责任险（第三者）",
            "附加医保外医疗费用责任险（新能源汽车第三者）",
        ),
    ),
    "DRIVER_NON_MEDICAL": CoverageDefinition(
        code="DRIVER_NON_MEDICAL",
        label="司机医保外",
        category=CATEGORY_ADDITIONAL,
        aliases=(
            "附加医保外医疗费用责任险（司机）",
            "附加医保外医疗费用责任险（驾驶员）",
        ),
    ),
    "PASSENGER_NON_MEDICAL": CoverageDefinition(
        code="PASSENGER_NON_MEDICAL",
        label="乘客医保外",
        category=CATEGORY_ADDITIONAL,
        aliases=("附加医保外医疗费用责任险（乘客）",),
    ),
    "EXTERNAL_GRID": CoverageDefinition(
        code="EXTERNAL_GRID",
        label="外部电网故障损失险",
        category=CATEGORY_ADDITIONAL,
        aliases=("附加外部电网故障损失险",),
    ),
    "GLASS_BROKEN": CoverageDefinition(
        code="GLASS_BROKEN",
        label="玻璃破碎",
        category=CATEGORY_ADDITIONAL,
        aliases=("附加玻璃单独破碎险", "附加玻璃单独破碎"),
    ),
    "SCRATCH": CoverageDefinition(
        code="SCRATCH",
        label="车身划痕",
        category=CATEGORY_ADDITIONAL,
        aliases=("附加车身划痕损失险", "附加车身划痕损失"),
    ),
    "REPAIR_PERIOD_COMP": CoverageDefinition(
        code="REPAIR_PERIOD_COMP",
        label="修理期间费用补偿",
        category=CATEGORY_ADDITIONAL,
        aliases=("附加修理期间费用补偿险",),
    ),
    "SPIRIT_DAMAGE": CoverageDefinition(
        code="SPIRIT_DAMAGE",
        label="精神损害抚慰金",
        category=CATEGORY_ADDITIONAL,
        aliases=("附加精神损害抚慰金责任险",),
    ),
    "FIND_VEHICLE": CoverageDefinition(
        code="FIND_VEHICLE",
        label="找回车辆费用",
        category=CATEGORY_ADDITIONAL,
        aliases=("附加找回车辆费用险",),
    ),
}

# 可作为险种行录入的标准码（不含交强险）
ROW_COVERAGE_CODES: frozenset[str] = frozenset(
    d.code for d in COVERAGE_DEFINITIONS.values() if d.row_selectable
)


def get_coverage_definition(code: str) -> CoverageDefinition | None:
    """按标准码取险种定义；未知码返回 None（不猜测类别）。"""
    return COVERAGE_DEFINITIONS.get(code)


def coverage_display_name(code: str, fallback: str) -> str:
    """标准显示名；未识别时返回 fallback（=rawName，SPEC §2.5）。"""
    definition = COVERAGE_DEFINITIONS.get(code)
    return definition.label if definition else fallback


# ---- 保险公司码（SPEC §3.2，预置 8 家 + OTHER 共 9 项）----
# 不得把“国寿财险”和“国元保险”映射为同一公司码。
INSURER_DEFINITIONS: dict[str, str] = {
    "PICC": "人保",
    "PINGAN": "平安",
    "CPIC": "太平洋",
    "CHINALIFE_PC": "国寿财险",
    "GUOYUAN": "国元保险",
    "DADI": "大地",
    "SUNSHINE": "阳光",
    "ZHONGAN": "众安",
    "OTHER": "其他",
}

# 预置公司码（不含 OTHER）：预置走结构化选项，显示名固定为标准名
PRESET_INSURER_CODES: frozenset[str] = frozenset(
    code for code in INSURER_DEFINITIONS if code != "OTHER"
)


# ---- 保障包内部类型码（SPEC §3.3，首版）----
PACKAGE_COVERAGE_DEFINITIONS: dict[str, str] = {
    "DRIVER_ACCIDENT": "驾乘意外",
    "PASSENGER_ACCIDENT": "乘客意外",
    "SELF_PAID_MEDICAL": "自费医疗",
    "HOLIDAY_DOUBLE": "节假日翻倍",
    "AIR_ACCIDENT": "飞机意外",
    "TRAIN_ACCIDENT": "火车意外",
    "SHIP_ACCIDENT": "轮船意外",
    "VEHICLE_ACCIDENT": "汽车意外",
    "AMBULANCE_FEE": "救护车费用",
    "TRAVEL_INCONVENIENCE": "出行不便",
    "FAMILY_PROPERTY": "家庭财产",
    "LUGGAGE_LOSS": "行李物品损失",
    "OTHER": "其他",
}


def get_package_coverage_label(type_code: str) -> str | None:
    """保障包内部类型显示名；未知类型码返回 None（统一按 OTHER 处理）。"""
    return PACKAGE_COVERAGE_DEFINITIONS.get(type_code)
