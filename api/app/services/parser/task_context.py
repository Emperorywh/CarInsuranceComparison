"""解析任务的共享类型与失败分类（TASK-03 冻结接口；TASK-04 扩展）。

独立成模块的原因：pipeline.py 需要在模块层导入 candidate_writer / pdf
等实现模块，而这些模块又需要本文件定义的上下文与异常类型；把“词汇表”
放在无业务依赖的 task_context 中可避免循环导入。pipeline.py 对这些
名字做再导出，既有代码（worker、测试）的导入路径保持不变。

失败分类（SPEC §12）：
- 不可重试：配置缺失、输入文件损坏、鉴权/参数类 4xx、空方案、
  混合公司批次等——重试不会有不同结果；
- 可重试：超时、网络错误、429/5xx、Schema 校验失败——总尝试不超过
  MAX_ATTEMPTS（首次 + 重试 2 次）。
所有对外错误文案都必须是脱敏后的中文提示，不得携带模型请求正文或原文。
"""

from __future__ import annotations

from dataclasses import dataclass

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

    session_factory 由 pipeline 装配方传入：TASK-04 的正式流水线在独立
    会话与事务中写入候选数据，避免 worker 的领取会话被长耗时解析占住。
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


class ParseInputError(ParseTaskFailure):
    """输入文件无法解码/渲染等确定性问题：不可重试（重试不会有不同结果）。"""

    retryable = False


class ParseRetryableError(ParseTaskFailure):
    """超时/网络/限流/5xx/Schema 失败：可重试（总尝试 ≤ MAX_ATTEMPTS）。"""

    retryable = True
