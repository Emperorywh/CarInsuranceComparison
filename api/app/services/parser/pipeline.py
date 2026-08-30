"""解析流水线协议、失败分类与注入点（SPEC §2.4、§4；TASK-03 范围 6）。

职责边界：
- worker（worker.py）负责任务领取、attempt 计数、状态迁移与报价状态联动；
- pipeline 只负责“一次任务的实际解析工作”：读取文件、调用视觉模型、
  写入脱敏候选数据。TASK-03 不实现任何模型调用，只冻结接口；
- 正式能力缺失时使用 UnconfiguredVisionPipeline 兜底：任务安全失败并给出
  脱敏的配置提示，绝不假装成功（TASKS.md 范围 6）；
- 测试通过 set_parse_pipeline 注入确定性假 pipeline，不访问网络。

失败分类（SPEC §12）：
- 不可重试：配置缺失、鉴权/参数类 4xx、空方案等——重试不会有不同结果；
- 可重试：超时、网络错误、429/5xx、Schema 校验失败——总尝试不超过 3 次。
所有对外错误文案都必须是脱敏后的中文提示，不得携带模型请求正文或原文。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# 单个任务的最大总尝试次数（首次 + 重试 2 次，SPEC §2.4 attempt 语义）
MAX_ATTEMPTS = 3


@dataclass(slots=True, frozen=True)
class ParseTaskFileInput:
    """任务的一个输入文件（fileKey 由后端按 inputOrder 分配：F1/F2/...）。"""

    file_id: int
    file_key: str
    relative_path: str
    mime: str
    page_count: int


@dataclass(slots=True, frozen=True)
class ParseTaskContext:
    """worker 交给 pipeline 的一次任务上下文。

    session_factory 供 TASK-04 的 pipeline 在独立事务中写入候选数据，
    避免 worker 的领取会话被长耗时解析占住。
    """

    task_id: int
    project_id: int
    quote_id: int | None
    files: list[ParseTaskFileInput]


class ParseTaskFailure(Exception):
    """pipeline 抛出的任务失败基类：携带可重试性与脱敏后的用户文案。"""

    retryable: bool = False

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class ParseConfigError(ParseTaskFailure):
    """配置缺失/非法：不可重试，提示用户检查 VISION_* 配置。"""

    retryable = False


class ParseRetryableError(ParseTaskFailure):
    """超时/网络/限流/5xx/Schema 失败：可重试（总尝试 ≤ MAX_ATTEMPTS）。"""

    retryable = True


class VisionPipeline(Protocol):
    """视觉解析流水线统一协议（TASK-04 的 OpenAI 兼容实现也走这里）。"""

    # 记录进 parse_task 的供应商与模型标识（真实使用的，非用户配置原文）
    provider: str
    model: str

    async def execute(self, context: ParseTaskContext) -> None:
        """执行一次解析。

        成功时 pipeline 自行负责候选数据落库（TASK-04）并正常返回；
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


# 进程级单例注入点；测试通过 set_parse_pipeline 替换实现
_pipeline: VisionPipeline = UnconfiguredVisionPipeline()


def get_parse_pipeline() -> VisionPipeline:
    return _pipeline


def set_parse_pipeline(pipeline: VisionPipeline) -> None:
    global _pipeline
    _pipeline = pipeline
