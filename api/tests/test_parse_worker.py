"""解析任务 worker 生命周期测试（TASK-03 验证 2）。

用可注入的确定性假 pipeline 驱动真实 worker 代码路径：
- PENDING -> RUNNING -> SUCCEEDED/FAILED 全程状态与 attempt 断言；
- 可重试失败自动回队、总尝试耗尽终态失败并联动报价进入 PARSE_FAILED；
- 配置类不可重试失败一次即终态；
- 启动恢复：遗留 RUNNING 重置 PENDING，attempt 耗尽的恢复任务直接失败；
- 日志不包含文件正文与原始文件名（隐私边界）。

此处 SUCCEEDED 只代表基础设施回调成功：断言不产生任何候选业务数据。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import pytest

from app.models import ParseTask, Quote
from app.models.enums import ParseTaskStatus, QuoteStatus
from app.services.parser import pipeline as pipeline_module
from app.services.parser.pipeline import ParseConfigError, ParseRetryableError
from app.services.parser.worker import recover_stale_running, run_one_task
from tests.files_helpers import jpeg_bytes


@pytest.fixture(autouse=True)
def _restore_pipeline():
    """每个用例后恢复默认兜底 pipeline，避免污染其他测试。"""
    yield
    from app.services.parser.pipeline import UnconfiguredVisionPipeline

    pipeline_module.set_parse_pipeline(UnconfiguredVisionPipeline())


# ---- 假 pipeline（确定性，不访问网络）----


class FakePipeline:
    """按脚本顺序产出结果；记录收到的上下文供断言。"""

    provider = "fake-provider"
    model = "fake-model"

    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = list(outcomes)
        self.contexts: list = []

    async def execute(self, context) -> None:  # noqa: ANN001
        self.contexts.append(context)
        outcome = self.outcomes.pop(0)
        if outcome == "ok":
            return
        if outcome == "retry":
            raise ParseRetryableError("模型请求超时，已自动重试")
        if outcome == "config":
            raise ParseConfigError("视觉模型尚未配置：请检查 VISION_* 配置")
        if outcome == "boom":
            raise RuntimeError("internal detail should not leak")
        raise AssertionError(f"未知脚本项 {outcome}")


def _session_factory(db_session):
    """把测试共享会话包装成 worker 需要的会话工厂。"""

    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


async def _create_uploading_quote(file_client) -> tuple[int, int]:
    """经真实上传链路准备“报价 + 文件 + PENDING 任务”。"""
    response = await file_client.post(
        "/api/projects",
        json={"name": "worker 项目", "vehicleName": "Model Y", "renewalYear": 2026},
    )
    project_id = response.json()["data"]["id"]
    response = await file_client.post(
        f"/api/projects/{project_id}/quotes",
        json={"insurerCode": "PINGAN", "source": "UPLOADED"},
    )
    quote_id = response.json()["data"]["id"]
    upload = await file_client.post(
        f"/api/quotes/{quote_id}/files",
        files=[("files", ("车损.jpg", jpeg_bytes(), "image/jpeg"))],
        data={"modelProcessingConsent": "true"},
    )
    assert upload.status_code == 202
    task_id = upload.json()["data"]["taskId"]
    return quote_id, task_id


# ---- 生命周期 ----


async def test_success_lifecycle(file_client, db_session) -> None:
    """PENDING -> RUNNING(attempt=1) -> SUCCEEDED；报价保持 PARSING。"""
    quote_id, task_id = await _create_uploading_quote(file_client)
    pipeline = FakePipeline(["ok"])
    pipeline_module.set_parse_pipeline(pipeline)
    executed = await run_one_task(_session_factory(db_session), pipeline)
    assert executed is True

    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.SUCCEEDED
    assert task.attempt == 1
    assert task.provider == "fake-provider"
    assert task.model == "fake-model"
    assert task.started_at is not None
    assert task.finished_at is not None
    assert task.error is None

    # 基础设施回调成功 ≠ 候选落库：报价不进入 PENDING_CONFIRM（TASK-04 分支）
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PARSING

    # pipeline 收到 fileKey 分配与输入清单
    assert len(pipeline.contexts) == 1
    context = pipeline.contexts[0]
    assert context.quote_id == quote_id
    assert [f.file_key for f in context.files] == ["F1"]


async def test_retryable_failures_exhaust_then_parse_failed(file_client, db_session) -> None:
    """可重试失败：前两次回队，第三次终态 FAILED + 报价 PARSE_FAILED。"""
    quote_id, task_id = await _create_uploading_quote(file_client)
    pipeline = FakePipeline(["retry", "retry", "retry"])
    pipeline_module.set_parse_pipeline(pipeline)
    factory = _session_factory(db_session)
    try:
        assert await run_one_task(factory, pipeline) is True  # attempt 1
        task = await db_session.get(ParseTask, task_id)
        assert task.status == ParseTaskStatus.PENDING  # 回队等待重试
        assert task.attempt == 1

        assert await run_one_task(factory, pipeline) is True  # attempt 2
        assert await run_one_task(factory, pipeline) is True  # attempt 3 -> 终态
    finally:
        pipeline_module.set_parse_pipeline(FakePipeline([]))

    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.FAILED
    assert task.attempt == 3
    assert task.error == "模型请求超时，已自动重试"

    # 报价状态联动：PARSING -> PARSE_FAILED（可重试解析或转手动）
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PARSE_FAILED

    # 三次尝试 pipeline 都收到上下文
    assert len(pipeline.contexts) == 3


async def test_config_error_fails_immediately(file_client, db_session) -> None:
    """配置类不可重试失败：一次即 FAILED，错误文案脱敏可展示。"""
    quote_id, task_id = await _create_uploading_quote(file_client)
    pipeline = FakePipeline(["config"])
    pipeline_module.set_parse_pipeline(pipeline)
    factory = _session_factory(db_session)
    try:
        assert await run_one_task(factory, pipeline) is True
    finally:
        pipeline_module.set_parse_pipeline(FakePipeline([]))

    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.FAILED
    assert task.attempt == 1  # 不消耗重试
    assert "VISION_" in task.error
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PARSE_FAILED


async def test_unexpected_error_is_retryable_then_succeeds(file_client, db_session) -> None:
    """未预期异常按可重试处理；重试成功后任务 SUCCEEDED 且 error 清空。"""
    _quote_id, task_id = await _create_uploading_quote(file_client)
    pipeline = FakePipeline(["boom", "ok"])
    pipeline_module.set_parse_pipeline(pipeline)
    factory = _session_factory(db_session)
    try:
        assert await run_one_task(factory, pipeline) is True  # 异常 -> 回队
        task = await db_session.get(ParseTask, task_id)
        assert task.status == ParseTaskStatus.PENDING
        # 对外错误不泄露异常内部细节
        assert "internal detail" not in (task.error or "")

        assert await run_one_task(factory, pipeline) is True  # 重试成功
    finally:
        pipeline_module.set_parse_pipeline(FakePipeline([]))

    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.SUCCEEDED
    assert task.error is None


async def test_recover_stale_running(file_client, db_session) -> None:
    """启动恢复：遗留 RUNNING 重置 PENDING；attempt 耗尽的恢复任务直接 FAILED。"""
    quote_id, task_id = await _create_uploading_quote(file_client)

    # 模拟上次进程在任务执行中被杀死：RUNNING + attempt=1
    task = await db_session.get(ParseTask, task_id)
    task.status = ParseTaskStatus.RUNNING
    task.attempt = 1
    await db_session.commit()

    recovered = await recover_stale_running(db_session)
    assert recovered == 1
    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.PENDING
    assert task.attempt == 1  # attempt 不重置

    # 恢复后正常执行：attempt 续计
    pipeline = FakePipeline(["ok"])
    pipeline_module.set_parse_pipeline(pipeline)
    try:
        await run_one_task(_session_factory(db_session), pipeline)
    finally:
        pipeline_module.set_parse_pipeline(FakePipeline([]))
    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.SUCCEEDED
    assert task.attempt == 2


async def test_recovered_task_with_exhausted_attempts_fails(file_client, db_session) -> None:
    """attempt 已耗尽的 RUNNING 恢复任务：领取时直接 FAILED，不再执行。"""
    quote_id, task_id = await _create_uploading_quote(file_client)
    task = await db_session.get(ParseTask, task_id)
    task.status = ParseTaskStatus.RUNNING
    task.attempt = 3  # 已达上限却在 RUNNING 中断
    await db_session.commit()

    await recover_stale_running(db_session)
    pipeline = FakePipeline([])
    pipeline_module.set_parse_pipeline(pipeline)
    try:
        # 无可执行任务：返回 False，但耗尽任务被就地终态化
        executed = await run_one_task(_session_factory(db_session), pipeline)
    finally:
        pipeline_module.set_parse_pipeline(FakePipeline([]))
    assert executed is False
    assert pipeline.contexts == []  # 未真正执行

    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.FAILED
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PARSE_FAILED


async def test_worker_logs_contain_no_file_content(
    file_client, db_session, caplog
) -> None:
    """隐私边界：任务日志不含文件正文、原始文件名或敏感内容（TASKS.md 验证 2）。"""
    sensitive_name = "京A12345保单.jpg"
    response = await file_client.post(
        "/api/projects",
        json={"name": "日志项目", "vehicleName": "Model Y", "renewalYear": 2026},
    )
    project_id = response.json()["data"]["id"]
    response = await file_client.post(
        f"/api/projects/{project_id}/quotes",
        json={"insurerCode": "PICC", "source": "UPLOADED"},
    )
    quote_id = response.json()["data"]["id"]
    upload = await file_client.post(
        f"/api/quotes/{quote_id}/files",
        files=[("files", (sensitive_name, jpeg_bytes(), "image/jpeg"))],
        data={"modelProcessingConsent": "true"},
    )
    _task_id = upload.json()["data"]["taskId"]

    pipeline = FakePipeline(["ok"])
    pipeline_module.set_parse_pipeline(pipeline)
    try:
        with caplog.at_level(logging.DEBUG, logger="app.services.parser.worker"):
            await run_one_task(_session_factory(db_session), pipeline)
    finally:
        pipeline_module.set_parse_pipeline(FakePipeline([]))

    logs = caplog.text
    assert sensitive_name not in logs  # 原始文件名绝不入日志
    # JPEG 文件字节标志性片段不出现
    assert b"\xff\xd8\xff".hex() not in logs.lower()
