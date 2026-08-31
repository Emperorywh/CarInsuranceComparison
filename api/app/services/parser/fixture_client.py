"""仅测试可启用的假视觉模型客户端（TASK-07：Playwright 端到端环境）。

用途边界：
- 服务 Playwright 端到端测试：让浏览器真机视口走完「上传 → 解析 →
  候选 → 确认/拆分/合并 → 对比 → 导出」全链路，而不访问真实模型供应商；
- 只能通过环境变量 ``VISION_FIXTURE_DIR`` 显式启用（该变量不属于常规
  部署配置，正式部署绝不设置）；除读取 fixture 文件外不产生任何网络请求。

行为协议：
- 每次调用都重新读取 ``{fixture_dir}/current.json``，端到端测试通过改写
  该文件切换“模型”返回内容（写入方须用临时文件原子替换，避免 worker
  读到半截文件）；
- 内容为 ``{"__fixture__": "fail"}`` 时抛可重试失败：worker 按
  attempt ≤ 3 走完产品内置重试后终态 FAILED，覆盖「模型最终失败 →
  PARSE_FAILED → 转手动录入」端到端路径；
- 其余内容走正式 ``parse_extraction`` Schema 校验，与真实 provider 的
  返回处理完全同源；脱敏、证据校验、归一化、候选落库全部复用正式流水线，
  因此端到端测试验证的正是生产代码路径，仅模型调用被替换。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.services.parser.extraction_schema import ExtractionResult, parse_extraction
from app.services.parser.pipeline import ParseRetryableError
from app.services.parser.vision_client import VisionInputPage

logger = logging.getLogger(__name__)

# 控制指令：fixture 文件中的特殊键，用于注入“必然失败”的非成功行为
_FIXTURE_CONTROL_KEY = "__fixture__"


class FixtureVisionClient:
    """从目录读取固定抽取结果的假 VisionClient（仅测试启用）。"""

    provider = "fixture"
    model = "fixture-model"

    def __init__(self, fixture_dir: Path) -> None:
        self._fixture_dir = fixture_dir
        self._current_path = fixture_dir / "current.json"

    async def extractQuote(self, pages: list[VisionInputPage]) -> ExtractionResult:
        # 每次调用重新读取：E2E 场景在两次上传之间改写 fixture 即可切换
        # “模型”行为；文件缺失/损坏按可重试失败处理（绝不假装成功）
        if not self._current_path.exists():
            raise ParseRetryableError(
                "测试模型尚未准备就绪：缺少 fixture 文件（仅测试环境会出现此提示）"
            )
        try:
            raw = json.loads(self._current_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as cause:
            raise ParseRetryableError(
                "测试模型 fixture 文件不可读（仅测试环境会出现此提示）"
            ) from cause

        control = raw.get(_FIXTURE_CONTROL_KEY) if isinstance(raw, dict) else None
        if control == "fail":
            # 可重试失败：重试耗尽后终态 FAILED（与真实模型的持续故障同路径）
            raise ParseRetryableError("模型服务暂时不可用，请稍后重试或转手动录入")
        # 正常路径：与真实 provider 相同的 Schema 校验入口
        return parse_extraction(raw)


def build_fixture_client_if_configured(fixture_dir: str) -> FixtureVisionClient | None:
    """按配置构建假客户端；未配置返回 None（正式环境恒为 None）。"""
    trimmed = fixture_dir.strip()
    if not trimmed:
        return None
    logger.warning(
        "已启用测试假视觉模型（VISION_FIXTURE_DIR 已设置）；该模式仅限端到端测试，"
        "正式部署不得配置，否则解析结果为固定测试数据"
    )
    return FixtureVisionClient(Path(trimmed))
