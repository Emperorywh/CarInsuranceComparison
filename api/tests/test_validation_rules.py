"""校验规则与置信度合成纯函数单测（TASK-04 验证 1/2；SPEC §4.2、§6）。

高风险语义重点：明确 0 元服务才是 FREE、空费用服务为 UNKNOWN、
证据非法/缺失的降档差异、低质量集中提示阈值。
"""

from __future__ import annotations

from decimal import Decimal

from app.models.enums import ConfidenceLevel, ItemStatus
from app.services.validation.rules import (
    low_quality_warning,
    nev_inconsistent,
    resolve_service_status,
    synthesize_confidence,
)


class TestSynthesizeConfidence:
    def test_high_when_everything_fine(self) -> None:
        assert (
            synthesize_confidence(
                self_confidence=0.95, evidence_state="ok"
            )
            == ConfidenceLevel.HIGH
        )

    def test_low_invalid_evidence(self) -> None:
        # 证据指向不存在的文件/页码：LOW，且来源不建链（SPEC §6.9）
        assert (
            synthesize_confidence(self_confidence=0.99, evidence_state="invalid")
            == ConfidenceLevel.LOW
        )

    def test_low_self_confidence(self) -> None:
        assert (
            synthesize_confidence(self_confidence=0.59, evidence_state="ok")
            == ConfidenceLevel.LOW
        )

    def test_low_total_mismatch_participant(self) -> None:
        assert (
            synthesize_confidence(
                self_confidence=0.99,
                evidence_state="ok",
                participates_in_total=True,
                total_check_status="MISMATCH",
            )
            == ConfidenceLevel.LOW
        )

    def test_mismatch_non_participant_stays_high(self) -> None:
        # 官方总价是校验参照而非参与合计字段：MISMATCH 不直接降档
        assert (
            synthesize_confidence(
                self_confidence=0.95,
                evidence_state="ok",
                participates_in_total=False,
                total_check_status="MISMATCH",
            )
            == ConfidenceLevel.HIGH
        )

    def test_medium_missing_evidence(self) -> None:
        assert (
            synthesize_confidence(self_confidence=0.99, evidence_state="missing")
            == ConfidenceLevel.MEDIUM
        )

    def test_medium_not_checkable_participant(self) -> None:
        assert (
            synthesize_confidence(
                self_confidence=0.99,
                evidence_state="ok",
                participates_in_total=True,
                total_check_status="NOT_CHECKABLE",
            )
            == ConfidenceLevel.MEDIUM
        )

    def test_medium_hints(self) -> None:
        base = dict(self_confidence=0.99, evidence_state="ok")
        assert (
            synthesize_confidence(**base, unrecognized=True) == ConfidenceLevel.MEDIUM
        )
        assert synthesize_confidence(**base, range_hint=True) == ConfidenceLevel.MEDIUM
        assert (
            synthesize_confidence(**base, nev_inconsistent=True)
            == ConfidenceLevel.MEDIUM
        )
        assert (
            synthesize_confidence(**base, other_medium_hint=True)
            == ConfidenceLevel.MEDIUM
        )

    def test_low_priority_over_medium(self) -> None:
        # 多信号命中取最低档（LOW > MEDIUM > HIGH）
        assert (
            synthesize_confidence(
                self_confidence=0.5,
                evidence_state="invalid",
                unrecognized=True,
            )
            == ConfidenceLevel.LOW
        )


class TestResolveServiceStatus:
    def test_explicit_zero_is_free(self) -> None:
        assert (
            resolve_service_status(ItemStatus.INCLUDED, Decimal("0"))
            == ItemStatus.FREE
        )
        assert (
            resolve_service_status(ItemStatus.UNKNOWN, Decimal("0.00"))
            == ItemStatus.FREE
        )

    def test_free_requires_zero_cost(self) -> None:
        # 模型标 FREE 但费用缺失/非 0：降为 UNKNOWN（不推断免费）
        assert (
            resolve_service_status(ItemStatus.FREE, None) == ItemStatus.UNKNOWN
        )
        assert (
            resolve_service_status(ItemStatus.FREE, Decimal("30"))
            == ItemStatus.UNKNOWN
        )

    def test_missing_cost_not_free(self) -> None:
        assert (
            resolve_service_status(ItemStatus.INCLUDED, None) == ItemStatus.INCLUDED
        )

    def test_not_included_untouched(self) -> None:
        assert (
            resolve_service_status(ItemStatus.NOT_INCLUDED, None)
            == ItemStatus.NOT_INCLUDED
        )
        assert (
            resolve_service_status(ItemStatus.NOT_INCLUDED, Decimal("0"))
            == ItemStatus.NOT_INCLUDED
        )


class TestNevInconsistent:
    def test_fuel_wording_with_nev_flag(self) -> None:
        assert nev_inconsistent(True, "机动车损失保险") is True

    def test_nev_wording_with_fuel_flag(self) -> None:
        assert nev_inconsistent(False, "新能源汽车损失保险") is True

    def test_consistent_or_unknown(self) -> None:
        assert nev_inconsistent(True, "新能源汽车损失保险") is False
        assert nev_inconsistent(None, "机动车损失保险") is False
        assert nev_inconsistent(True, None) is False


class TestLowQualityWarning:
    def test_low_ratio_threshold(self) -> None:
        # 5 个字段 1 个 LOW = 20% → 触发
        levels = [ConfidenceLevel.HIGH] * 4 + [ConfidenceLevel.LOW]
        assert low_quality_warning(levels) is not None

    def test_medium_low_half_threshold(self) -> None:
        # 无 LOW 但 MEDIUM+LOW 达 60% → 触发
        levels = [ConfidenceLevel.MEDIUM] * 3 + [ConfidenceLevel.HIGH] * 2
        assert low_quality_warning(levels) is not None

    def test_below_thresholds(self) -> None:
        levels = [ConfidenceLevel.HIGH] * 4 + [ConfidenceLevel.MEDIUM]
        assert low_quality_warning(levels) is None

    def test_empty(self) -> None:
        assert low_quality_warning([]) is None
