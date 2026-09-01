"""解析流水线协议、失败分类、注入点与正式实现（SPEC §2.4、§4）。

职责边界：
- worker（worker.py）负责任务领取、attempt 计数、状态迁移与报价状态联动；
- pipeline 只负责“一次任务的实际解析工作”：读取文件、调用视觉模型、
  写入脱敏候选数据。共享类型与失败分类定义在 task_context.py（避免与
  candidate_writer/pdf 的循环导入），本模块再导出以保持既有导入路径；
- 正式能力缺失时使用 UnconfiguredVisionPipeline 兜底：任务安全失败并
  给出脱敏的配置提示，绝不假装成功（TASKS.md 范围 6）；
- 测试通过 set_parse_pipeline 注入确定性假 pipeline，不访问网络。

VisionParsePipeline（TASK-04）执行顺序（SPEC §4 步骤 1-10）：
页面准备 → 模型抽取（Schema 校验）→ 空方案守卫 →
脱敏 + 证据校验 + 归一化 + 校验/置信度 → 候选落库（单事务）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol

from app.config import Settings
from app.models import ParseTask, Quote

# 再导出共享词汇表（worker / 测试沿用本模块的导入路径）；
# 冗余别名是刻意的再导出写法，避免被当作未使用导入清理
from app.services.parser.task_context import (  # noqa: F401
    MAX_ATTEMPTS as MAX_ATTEMPTS,
)
from app.services.parser.task_context import (
    ParseConfigError,
    ParseInputError,
    ParseTaskContext,
    ParseTaskFailure,
)
from app.services.parser.task_context import (
    ParseRetryableError as ParseRetryableError,
)
from app.services.parser.task_context import (
    ParseTaskFileInput as ParseTaskFileInput,
)

if TYPE_CHECKING:  # 仅类型引用，避免运行时依赖
    from sqlalchemy.ext.asyncio import AsyncSession

    class SessionFactory(Protocol):
        """worker/pipeline 需要的会话工厂最小协议（与 async_sessionmaker 兼容）。"""

        def __call__(self) -> AsyncIterator[AsyncSession]: ...  # pragma: no cover


@asynccontextmanager
async def _owned_session(factory) -> AsyncIterator:  # noqa: ANN001
    """把 async_sessionmaker 适配成 async with 可用的上下文。"""
    async with factory() as session:
        yield session


class VisionPipeline(Protocol):
    """视觉解析流水线统一协议（TASK-03 冻结；正式实现见下方）。"""

    provider: str
    model: str

    async def execute(self, context: ParseTaskContext) -> None:
        """执行一次解析。

        成功时 pipeline 自行负责候选数据落库并正常返回；
        失败时抛 ParseTaskFailure 子类，由 worker 统一处理状态迁移。
        """
        ...


class UnconfiguredVisionPipeline:
    """正式解析能力缺失时的兜底实现：安全失败，不产生任何候选数据。

    “SUCCEEDED 只代表基础设施回调成功”的口径由测试假 pipeline 承担；
    本实现确保未配置模型时任务一定进入 FAILED 并给出可操作的提示。
    """

    provider = "openai-compatible"
    model = "not-configured"

    async def execute(self, context: ParseTaskContext) -> None:
        raise ParseConfigError(
            "视觉模型尚未配置：请在 .env 中设置 VISION_BASE_URL、VISION_API_KEY、"
            "VISION_MODEL 后重启服务，或改用“转手动录入”继续"
        )


class VisionParsePipeline:
    """正式解析流水线（TASK-04）：页面准备 → 模型抽取 → 候选落库。

    与 worker 的分工：worker 负责任务领取/attempt/终态；本类负责一次
    任务的实际工作，成功路径在独立会话的单个事务内写候选数据并把报价
    置为 PENDING_CONFIRM，失败路径抛 ParseTaskFailure 由 worker 收敛。
    """

    provider = "openai-compatible"

    def __init__(
        self,
        settings: Settings,
        session_factory,  # noqa: ANN001 - SessionFactory 协议
        client,  # noqa: ANN001 - VisionClient 协议（含 provider/model 标识）
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._client = client
        # provider/model 跟随实际客户端：正式 provider 与测试 fixture 各自带标识，
        # parse_task 记录的因此是真实使用的“模型来源”（可观测性，SPEC §13）
        self.provider = getattr(client, "provider", "openai-compatible")
        self.model = client.model

    async def execute(self, context: ParseTaskContext) -> None:
        # 局部导入：candidate_writer / pdf 依赖本模块的类型定义，
        # 延迟到调用期导入可避免模块层循环
        from app.services.parser.candidate_writer import apply_extraction
        from app.services.parser.pdf import prepare_task_pages

        if not context.files:
            raise ParseInputError("解析任务没有任何输入文件，请重新上传后解析")
        # 步骤 1-2：页面准备（EXIF/缩放/PDF 渲染），fileKey 沿用 worker 分配
        pages = await prepare_task_pages(self._settings, context.files)
        # 步骤 3-4：模型调用 + Schema 校验（失败分类由 provider 决定）
        extraction = await self._client.extractQuote(pages)
        # 空方案（SPEC §12）：确定性失败，重试不会有不同结果
        if not extraction.plans:
            raise ParseTaskFailure(
                "未识别到报价内容，请检查图片是否清晰完整，或改用“转手动录入”"
            )
        # 步骤 5-10：脱敏、证据校验、归一化、校验/置信度与候选落库（单事务）
        async with _owned_session(self._session_factory) as db, db.begin():
            task = await db.get(ParseTask, context.task_id)
            if task is None:  # pragma: no cover - 任务被并发删除
                return
            quote = (
                await db.get(Quote, context.quote_id)
                if context.quote_id is not None
                else None
            )
            await apply_extraction(
                db,
                task=task,
                quote=quote,
                files=context.files,
                extraction=extraction,
                settings=self._settings,
            )


def build_parse_pipeline(settings: Settings, session_factory) -> VisionPipeline:  # noqa: ANN001
    """按配置装配正式流水线。

    装配优先级：VISION_FIXTURE_DIR（仅测试启用的固定假模型）→ 正式
    OpenAI 兼容 provider → 未配置时安全失败兜底。假模型走同一条
    VisionParsePipeline，端到端测试覆盖的因此是生产代码路径。
    """
    # 局部导入：fixture_client 是测试专用装配，避免正式路径的常驻导入
    from app.services.parser.fixture_client import build_fixture_client_if_configured

    fixture_client = build_fixture_client_if_configured(settings.vision_fixture_dir)
    if fixture_client is not None:
        return VisionParsePipeline(settings, session_factory, fixture_client)
    if not (
        settings.vision_base_url.strip()
        and settings.vision_api_key.strip()
        and settings.vision_model.strip()
    ):
        return UnconfiguredVisionPipeline()
    # 局部导入：openai_provider 依赖本模块的失败分类定义
    from app.services.parser.openai_provider import OpenAICompatibleVisionClient

    client = OpenAICompatibleVisionClient(
        base_url=settings.vision_base_url,
        api_key=settings.vision_api_key,
        model=settings.vision_model,
        thinking=settings.vision_thinking,
    )
    return VisionParsePipeline(settings, session_factory, client)


# 进程级单例注入点；测试通过 set_parse_pipeline 替换实现
_pipeline: VisionPipeline = UnconfiguredVisionPipeline()


def get_parse_pipeline() -> VisionPipeline:
    return _pipeline


def set_parse_pipeline(pipeline: VisionPipeline) -> None:
    global _pipeline
    _pipeline = pipeline
