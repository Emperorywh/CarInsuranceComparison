"""归一化引擎纯函数单测（TASK-04 验证 1；SPEC §3）。

高风险语义重点：司机/乘客不互换、三个医保外对象不互换、“国寿财险”与
“国元保险”不得映射为同一公司码、代办送检先于普通检测。
"""

from __future__ import annotations

from app.services.normalization.engine import (
    match_coverage,
    match_insurer,
    match_package_type,
    match_service,
    normalize_condition,
)


class TestMatchCoverage:
    def test_exact_alias(self) -> None:
        assert match_coverage("附加医保外医疗费用责任险（第三者）") == "TP_NON_MEDICAL"

    def test_cleaned_variant(self) -> None:
        # 空格/全角括号/中点干扰下仍精确归并
        assert match_coverage(" 新能源汽车损失保险 ") == "VEHICLE_LOSS"
        assert match_coverage("车上人员责任险：驾驶员") == "DRIVER_LIABILITY"

    def test_keyword_combination_requires_target(self) -> None:
        # 医保外但目标词缺失：宁可未识别也不猜对象（高风险防互换）
        assert match_coverage("附加医保外医疗费用责任险") is None

    def test_non_medical_targets_distinct(self) -> None:
        assert match_coverage("附加医保外医疗费用责任险（第三者）") == "TP_NON_MEDICAL"
        assert match_coverage("附加医保外医疗费用责任险（司机）") == "DRIVER_NON_MEDICAL"
        assert match_coverage("附加医保外医疗费用责任险（乘客）") == "PASSENGER_NON_MEDICAL"

    def test_driver_passenger_distinct(self) -> None:
        assert match_coverage("新能源汽车车上人员责任保险（司机）") == "DRIVER_LIABILITY"
        assert match_coverage("新能源汽车车上人员责任保险（乘客）") == "PASSENGER_LIABILITY"
        assert match_coverage("驾驶员座位") == "DRIVER_LIABILITY"
        assert match_coverage("乘客座位") == "PASSENGER_LIABILITY"

    def test_compulsory_and_grid_before_loss(self) -> None:
        assert match_coverage("交通事故责任强制保险") == "COMPULSORY"
        # “电网故障损失险”不得被“损失”误伤成车损
        assert match_coverage("附加外部电网故障损失险") == "EXTERNAL_GRID"

    def test_additional_rules(self) -> None:
        assert match_coverage("附加车身划痕损失险") == "SCRATCH"
        assert match_coverage("附加精神损害抚慰金责任险") == "SPIRIT_DAMAGE"
        assert match_coverage("附加找回车辆费用险") == "FIND_VEHICLE"

    def test_unknown_returns_none(self) -> None:
        assert match_coverage("轮胎单独损坏保障") is None
        assert match_coverage("") is None
        assert match_coverage(None) is None


class TestMatchInsurer:
    def test_presets(self) -> None:
        assert match_insurer("中国平安财产保险股份有限公司") == "PINGAN"
        assert match_insurer("人保") == "PICC"
        assert match_insurer("PICC") == "PICC"
        assert match_insurer("太平洋产险") == "CPIC"

    def test_chinalife_pc_vs_guoyuan(self) -> None:
        # 两家公司不得互混（SPEC §3.2 明确要求）
        assert match_insurer("中国人寿财产保险股份有限公司") == "CHINALIFE_PC"
        assert match_insurer("国元保险") == "GUOYUAN"
        assert match_insurer("国寿财险") == "CHINALIFE_PC"

    def test_unknown_company_returns_none(self) -> None:
        assert match_insurer("紫金财产保险股份有限公司") is None
        assert match_insurer("") is None
        assert match_insurer(None) is None


class TestMatchService:
    def test_agent_before_inspection(self) -> None:
        # “代办送检”含“送检”，必须落代办而非普通检测（有序匹配）
        assert match_service("代办送检") == "INSPECTION_AGENT"
        assert match_service("代为送检服务") == "INSPECTION_AGENT"

    def test_basic_types(self) -> None:
        assert match_service("道路救援") == "ROAD_RESCUE"
        assert match_service("免费道路救援服务") == "ROAD_RESCUE"
        assert match_service("车辆安全检测") == "INSPECTION"
        assert match_service("代驾服务") == "DRIVER_SERVICE"

    def test_other(self) -> None:
        assert match_service("积分兑换") == "OTHER"
        assert match_service(None) == "OTHER"


class TestMatchPackageType:
    def test_known_types(self) -> None:
        assert match_package_type("驾乘意外身故及残疾 30万") == "DRIVER_ACCIDENT"
        assert match_package_type("乘客意外伤害") == "PASSENGER_ACCIDENT"
        assert match_package_type("节假日翻倍") == "HOLIDAY_DOUBLE"
        assert match_package_type("飞机意外身故") == "AIR_ACCIDENT"
        assert match_package_type("救护车费用") == "AMBULANCE_FEE"

    def test_unknown_type_is_other(self) -> None:
        assert match_package_type("神秘增值大礼包") == "OTHER"
        assert match_package_type(None) == "OTHER"


class TestNormalizeCondition:
    def test_holiday_mapping(self) -> None:
        assert normalize_condition("法定节假日") == "LEGAL_HOLIDAY"
        assert normalize_condition("LEGAL_HOLIDAY") == "LEGAL_HOLIDAY"

    def test_passthrough_and_empty(self) -> None:
        assert normalize_condition("仅限工作日") == "仅限工作日"
        assert normalize_condition(None) is None
        assert normalize_condition("  ") is None
