"""进程内单 worker：解析任务的领取、互斥、attempt 与恢复（SPEC §2.4、§13；TASK-03 范围 6）。

设计不变量：
- MVP 部署为单 API 进程 + 单任务 worker，不引入 Redis/Celery；worker 串行
  领取任务，`FOR UPDATE SKIP LOCKED` 保证即使未来多进程部署也不会重复领取；
- 同一报价同时最多一个活动任务（PENDING/RUNNING）由数据库部分唯一索引兜底，
  业务层 409 只是友好前置（parse_service 负责）；
- attempt 记录任务的总尝试次数：领取即 +1，达到 MAX_ATTEMPTS 仍失败则
  终态 FAILED；启动恢复把遗留 RUNNING 重置为 PENDING，重领时若 attempt
  已耗尽则直接 FAILED，避免崩溃循环无限重试；
- 报价状态联动：任务终态 FAILED 且目标报价处于 PARSING 时进入
  PARSE_FAILED；SUCCEEDED 的候选落库与后续状态迁移全部由 pipeline
  负责（TASK-04），worker 不写任何业务候选数据；
- 隐私边界：日志只含 taskId、attempt、耗时与状态，绝不记录文件正文、
  模型请求或原始响应。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.privacy import sanitize_text
from app.models import ParseTask, ParseTaskFile, Quote, QuoteFile
from app.models.enums import ParseTaskStatus, QuoteStatus
from app.services.parser.pipeline import (
    MAX_ATTEMPTS,
    ParseTaskContext,
    ParseTaskFailure,
    ParseTaskFileInput,
    VisionPipeline,
)

logger = logging.getLogger(__name__)


class SessionFactory(Protocol):
    """worker 需要的会话工厂最小协议（与 async_sessionmaker 兼容）。"""

    def __call__(self) -> AsyncIterator[AsyncSession]: ...  # pragma: no cover


@asynccontextmanager
async def _owned_session(factory: SessionFactory):
    """把 async_sessionmaker 适配成 async with 可用的上下文。"""
    async with factory() as session:
        yield session


async def recover_stale_running(db: AsyncSession) -> int:
    """启动恢复：把遗留的 RUNNING 任务重置为 PENDING（SPEC §2.10）。

    进程上次运行可能在任务执行中被杀死；RUNNING 状态无人认领即视为遗留。
    返回重置数量，供启动日志与测试断言。attempt 不重置：恢复后仍受
    总尝试上限约束（SPEC §13“最多重试次数仍按 parse_task.attempt 控制”）。
    """
    result = await db.execute(
        update(ParseTask)
        .where(ParseTask.status == ParseTaskStatus.RUNNING)
        .values(status=ParseTaskStatus.PENDING)
    )
    await db.commit()
    return int(result.rowcount or 0)


async def claim_next_task(db: AsyncSession, pipeline: VisionPipeline) -> ParseTask | None:
    """领取最旧的 PENDING 任务并置为 RUNNING（attempt +1，记录 provider/model）。

    返回 None 表示当前没有可执行任务（包括“attempt 已耗尽的恢复任务”，
    该情形下任务会被就地置为 FAILED 并联动报价状态）。
    """
    task = (
        await db.execute(
            select(ParseTask)
            .where(ParseTask.status == ParseTaskStatus.PENDING)
            .order_by(ParseTask.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if task is None:
        return None

    # 重启恢复的特殊情形：attempt 已耗尽的任务不再执行，直接终态失败，
    # 防止“启动恢复 -> 领取 -> 崩溃”死循环（SPEC §13）
    if task.attempt >= MAX_ATTEMPTS:
        await _finish_task(db, task, ParseTaskStatus.FAILED, error="任务多次中断后恢复失败")
        logger.warning("恢复任务 attempt 已耗尽 taskId=%s attempt=%s", task.id, task.attempt)
        return None

    task.attempt += 1
    task.status = ParseTaskStatus.RUNNING
    task.started_at = datetime.now(UTC)
    task.provider = pipeline.provider
    task.model = pipeline.model
    await db.commit()
    return task


async def load_task_context(db: AsyncSession, task: ParseTask) -> ParseTaskContext:
    """组装 pipeline 执行上下文：任务输入文件按 inputOrder 固定 fileKey。"""
    rows = (
        await db.execute(
            select(ParseTaskFile, QuoteFile)
            .join(QuoteFile, QuoteFile.id == ParseTaskFile.file_id)
            .where(ParseTaskFile.task_id == task.id)
            .order_by(ParseTaskFile.input_order.asc())
        )
    ).all()
    files = [
        ParseTaskFileInput(
            file_id=file.id,
            file_key=f"F{order}",
            relative_path=file.file_path,
            mime=file.mime,
            page_count=file.page_count,
        )
        for order, (_link, file) in enumerate(rows, start=1)
    ]
    return ParseTaskContext(
        task_id=task.id,
        project_id=task.project_id,
        quote_id=task.quote_id,
        files=files,
    )


async def run_one_task(
    session_factory: SessionFactory,
    pipeline: VisionPipeline,
    *,
    retry_delay_seconds: float = 0,
) -> bool:
    """执行一个完整工作周期：领取 -> 交给 pipeline -> 落终态/重试。

    返回是否实际执行了任务（False = 队列为空）。测试直接调用本函数即可
    确定性驱动任务流转，无需启动真实后台循环。
    """
    async with _owned_session(session_factory) as db:
        task = await claim_next_task(db, pipeline)
        if task is None:
            return False
        task_id = task.id
        attempt = task.attempt
        context = await load_task_context(db, task)

    started = time.monotonic()
    try:
        await pipeline.execute(context)
    except ParseTaskFailure as failure:
        outcome = f"失败(retryable={failure.retryable})"
        error_text = sanitize_text(failure.user_message)
        if failure.retryable and attempt < MAX_ATTEMPTS:
            await _requeue(session_factory, task_id, error_text)
            logger.info(
                "解析任务失败待重试 taskId=%s attempt=%s/%s 耗时=%.2fs",
                task_id,
                attempt,
                MAX_ATTEMPTS,
                time.monotonic() - started,
            )
            if retry_delay_seconds > 0:
                await asyncio.sleep(retry_delay_seconds)
            return True
        await _finalize_failure(session_factory, task_id, attempt, error_text)
    except Exception:
        # 未预期异常按可重试处理（基础设施抖动）；对外文案固定脱敏，
        # 不回传异常内容（可能含文件正文或内部细节）
        outcome = "异常"
        error_text = "解析任务执行失败，请稍后重试或转手动录入"
        if attempt < MAX_ATTEMPTS:
            await _requeue(session_factory, task_id, error_text)
            if retry_delay_seconds > 0:
                await asyncio.sleep(retry_delay_seconds)
            return True
        await _finalize_failure(session_factory, task_id, attempt, error_text)
    else:
        outcome = "成功"
        # SUCCEEDED 只代表基础设施回调成功；候选落库与报价状态迁移由
        # pipeline（TASK-04）负责，此处不写任何业务数据
        async with _owned_session(session_factory) as db:
            fresh = await db.get(ParseTask, task_id)
            if fresh is not None:
                await _finish_task(db, fresh, ParseTaskStatus.SUCCEEDED, error=None)
        logger.info(
            "解析任务完成 taskId=%s 耗时=%.2fs", task_id, time.monotonic() - started
        )
        return True

    logger.info(
        "解析任务终态%s taskId=%s attempt=%s 耗时=%.2fs",
        outcome,
        task_id,
        attempt,
        time.monotonic() - started,
    )
    return True


async def worker_loop(
    session_factory: SessionFactory,
    pipeline: VisionPipeline,
    stop_event: asyncio.Event,
    *,
    poll_interval_seconds: float = 1.0,
    retry_delay_seconds: float = 2.0,
) -> None:
    """生产模式的 worker 主循环：串行消费任务直到 stop_event 置位。

    关停语义（SPEC §2.4“关停”）：收到停止信号后完成当前周期即退出，
    正在 RUNNING 的任务由下次启动的 recover_stale_running 接管。
    """
    while not stop_event.is_set():
        try:
            executed = await run_one_task(
                session_factory, pipeline, retry_delay_seconds=retry_delay_seconds
            )
        except Exception:  # pragma: no cover - 数据库故障等，保持循环存活
            logger.exception("worker 周期异常，稍后继续")
            executed = False
        if not executed:
            # 空转时按轮询间隔挂起；stop_event 置位立即唤醒实现快速关停
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_seconds)


async def _requeue(session_factory: SessionFactory, task_id: int, error_text: str) -> None:
    """可重试失败：任务回 PENDING 等待下次领取，记录脱敏错误摘要。"""
    async with _owned_session(session_factory) as db:
        task = await db.get(ParseTask, task_id)
        if task is None:
            return
        task.status = ParseTaskStatus.PENDING
        task.error = error_text
        await db.commit()


async def _finalize_failure(
    session_factory: SessionFactory, task_id: int, attempt: int, error_text: str
) -> None:
    """终态失败：任务 FAILED，并联动处于 PARSING 的目标报价进入 PARSE_FAILED。"""
    async with _owned_session(session_factory) as db:
        task = await db.get(ParseTask, task_id)
        if task is None:
            return
        await _finish_task(db, task, ParseTaskStatus.FAILED, error=error_text)


async def _finish_task(
    db: AsyncSession, task: ParseTask, status: ParseTaskStatus, *, error: str | None
) -> None:
    """落任务终态并联动报价状态；同一事务内提交。

    报价联动规则（SPEC §2.10）：
    - 只有当前处于 PARSING 的报价才进入 PARSE_FAILED；
    - PENDING_CONFIRM 补传失败保持原状态（保留上一次候选）；
    - CONFIRMED/MERGE_REVIEW 的合并解析失败处理是 TASK-05 的职责；
    - 任务终态时间戳 finished_at 统一记录，供排队能耗时统计。
    """
    task.status = status
    task.error = error
    task.finished_at = datetime.now(UTC)
    if task.quote_id is not None and status == ParseTaskStatus.FAILED:
        quote = await db.get(Quote, task.quote_id)
        if quote is not None and quote.status == QuoteStatus.PARSING:
            quote.status = QuoteStatus.PARSE_FAILED
    await db.commit()


async def get_task_or_404(db: AsyncSession, task_id: int) -> ParseTask:
    task = await db.get(ParseTask, task_id)
    if task is None:
        raise NotFoundError(message="解析任务不存在")
    return task
