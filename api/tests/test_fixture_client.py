"""TASK-07：仅测试可启用的假视觉模型（fixture client）单元测试。

覆盖：装配优先级（fixture > 正式 provider > 未配置兜底）、正常路径
Schema 校验、``__fixture__: fail`` 注入失败、文件缺失/损坏安全失败。
不访问网络，不写数据库。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.services.parser.fixture_client import FixtureVisionClient
from app.services.parser.pipeline import (
    ParseRetryableError,
    UnconfiguredVisionPipeline,
    VisionParsePipeline,
    build_parse_pipeline,
)


def _minimal_extraction(insurer_name: str = "人保") -> dict:
    """构造通过 §4.1 Schema 的最小抽取结果（全部定义键存在，值可空）。"""
    scalar = {"value": None, "rawValue": None, "selfConfidence": None, "evidence": None}
    price_item = {
        **scalar,
        "status": "UNKNOWN",
    }
    return {
        "insurer": {
            "name": insurer_name,
            "selfConfidence": 0.9,
            "evidence": {"fileKey": "F1", "page": 1, "text": f"{insurer_name}报价单"},
        },
        "vehicle": {
            "model": dict(scalar),
            "seatCount": dict(scalar),
            "firstRegDate": dict(scalar),
            "isNev": dict(scalar),
        },
        "planCount": 1,
        "plans": [
            {
                "planLabel": "方案A",
                "insurerName": insurer_name,
                "pricing": {
                    "commercialPremium": dict(price_item),
                    "compulsoryPremium": dict(price_item),
                    "vehicleTax": dict(price_item),
                    "packageTotal": dict(price_item),
                    "otherFees": dict(price_item),
                    "officialTotal": dict(price_item),
                },
                "coreCoverages": [],
                "additionalCoverages": [],
                "services": [],
                "supplementalPackages": [],
                "annotations": [],
                "unmatchedItems": [],
            }
        ],
    }


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    return tmp_path / "vision-fixture"


def _client(fixture_dir: Path) -> FixtureVisionClient:
    return FixtureVisionClient(fixture_dir)


async def _call(client: FixtureVisionClient):
    # 页面内容不会被假客户端读取，占位即可
    return await client.extractQuote([])


def test_build_pipeline_fixture_takes_precedence_over_real_provider() -> None:
    """fixture 目录与正式 VISION_* 同时配置时 fixture 优先（测试专用装配）。"""
    settings = Settings(
        vision_fixture_dir="/tmp/e2e-fixture",
        vision_base_url="https://example.com/v1",
        vision_api_key="sk-test",
        vision_model="test-model",
        _env_file=None,  # type: ignore[call-arg]
    )
    pipeline = build_parse_pipeline(settings, None)
    assert isinstance(pipeline, VisionParsePipeline)
    assert pipeline.provider == "fixture"
    assert pipeline.model == "fixture-model"


def test_build_pipeline_unconfigured_falls_back_to_safe_failure() -> None:
    """未配置 fixture 且未配置 VISION_* 时保持既有安全失败兜底。"""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    pipeline = build_parse_pipeline(settings, None)
    assert isinstance(pipeline, UnconfiguredVisionPipeline)


async def test_fixture_client_returns_validated_extraction(
    fixture_dir: Path,
) -> None:
    """正常路径：fixture 内容经正式 Schema 校验返回。"""
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "current.json").write_text(
        json.dumps(_minimal_extraction("平安")), encoding="utf-8"
    )
    result = await _call(_client(fixture_dir))
    assert result.planCount == 1
    assert result.plans[0].insurerName == "平安"
    assert result.insurer.name == "平安"


async def test_fixture_client_fail_control_is_retryable(
    fixture_dir: Path,
) -> None:
    """``__fixture__: fail`` 注入可重试失败（重试耗尽后终态 FAILED）。"""
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "current.json").write_text(
        json.dumps({"__fixture__": "fail"}), encoding="utf-8"
    )
    with pytest.raises(ParseRetryableError):
        await _call(_client(fixture_dir))


async def test_fixture_client_missing_or_broken_file_fails_safely(
    fixture_dir: Path,
) -> None:
    """fixture 缺失/损坏时安全失败，绝不假装成功。"""
    with pytest.raises(ParseRetryableError):
        await _call(_client(fixture_dir))

    fixture_dir.mkdir(parents=True)
    (fixture_dir / "current.json").write_text("{不是 JSON", encoding="utf-8")
    with pytest.raises(ParseRetryableError):
        await _call(_client(fixture_dir))


async def test_fixture_client_rereads_file_per_call(fixture_dir: Path) -> None:
    """每次调用重新读取：端到端测试改写 fixture 即可切换“模型”行为。"""
    fixture_dir.mkdir(parents=True)
    path = fixture_dir / "current.json"
    path.write_text(json.dumps(_minimal_extraction("人保")), encoding="utf-8")
    first = await _call(_client(fixture_dir))
    assert first.insurer.name == "人保"

    path.write_text(json.dumps(_minimal_extraction("太平洋")), encoding="utf-8")
    second = await _call(_client(fixture_dir))
    assert second.insurer.name == "太平洋"
