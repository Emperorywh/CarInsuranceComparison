"""文件上传编排与解析任务服务（TASK-03 范围 4-8）。

业务不变量：
- 上传入口是“先建报价（DRAFT 容器）后传文件”；上传成功一律 202 并携带
  taskId（含校验失败在内的所有失败路径不产生半截数据）；
- 同一报价同时只允许一个活动解析任务（PENDING/RUNNING），业务层 409，
  数据库部分唯一索引兜底；
- 项目首次解析必须显式 modelProcessingConsent=true，写入 modelConsentAt
  后同一项目后续请求可省略（SPEC §2.1、§9.1）；
- 文件落盘与数据库记录在同一事务语义下：先 flush 拿 fileId 再原子落盘，
  数据库提交失败时回滚并删除本次已写入的文件目录（不留未引用文件）；
- 删除报价只删除该报价的关联；文件仍被兄弟报价或解析任务引用时保留，
  完全无引用时连数据库行与磁盘目录一起清理（SPEC §2.8）。

隐私边界：原始文件名只在本模块内存中短暂存在；数据库只存脱敏展示名；
落盘文件名随机化；错误信息不携带路径与文件正文。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.core.errors import ConflictError, NotFoundError, QuoteStateError, ValidationError
from app.models import (
    ComparisonProject,
    ParseTask,
    ParseTaskFile,
    Quote,
    QuoteFile,
    QuoteFileLink,
)
from app.models.enums import ParseTaskStatus, QuoteSource, QuoteStatus
from app.schemas.file import ParseStatusRead
from app.services.storage import local_files
from app.services.storage.validation import InspectedFile, inspect_uploads

# 活动解析任务状态：互斥约束的判定集合（数据库部分唯一索引同口径）
_ACTIVE_TASK_STATUSES = (ParseTaskStatus.PENDING, ParseTaskStatus.RUNNING)

# 允许重新解析的报价状态（SPEC §2.10）：失败重试、待确认重解析；
# CONFIRMED/MERGE_REVIEW 的补传与重解析属 TASK-05 的合并流程，此处 409
_REPARSEABLE_STATUSES = (QuoteStatus.PARSE_FAILED, QuoteStatus.PENDING_CONFIRM)


async def _get_quote_with_project(db: AsyncSession, quote_id: int) -> Quote:
    quote = await db.get(Quote, quote_id)
    if quote is None:
        raise NotFoundError(code="QUOTE_NOT_FOUND", message="报价不存在或已被删除")
    return quote


async def _get_project(db: AsyncSession, project_id: int) -> ComparisonProject:
    project = await db.get(ComparisonProject, project_id)
    if project is None:
        raise NotFoundError(code="PROJECT_NOT_FOUND", message="项目不存在或已被删除")
    return project


def _apply_model_consent(project: ComparisonProject, consent_flag: bool) -> None:
    """项目级模型传输同意门控（SPEC §10：缺失返回 422，同意即记录时间）。"""
    if project.model_consent_at is not None:
        return
    if not consent_flag:
        raise ValidationError(
            code="MODEL_CONSENT_REQUIRED",
            message=(
                "首次上传解析前，需要你同意将报价单原文件发送至所配置的视觉模型处理；"
                "不同意可改用“手动录入”"
            ),
        )
    project.model_consent_at = datetime.now(UTC)


async def _ensure_no_active_task(db: AsyncSession, quote_id: int) -> None:
    """同一报价的活动任务互斥（业务层友好 409，数据库唯一索引兜底）。"""
    active_id = await db.scalar(
        select(ParseTask.id).where(
            ParseTask.quote_id == quote_id,
            ParseTask.status.in_(_ACTIVE_TASK_STATUSES),
        )
    )
    if active_id is not None:
        raise ConflictError(
            code="PARSE_TASK_CONFLICT",
            message="该报价已有解析任务进行中，请等待完成后再试",
        )


async def create_quote_files(
    db: AsyncSession,
    quote_id: int,
    uploads: list,
    *,
    model_processing_consent: bool,
    settings: Settings,
) -> tuple[ParseTask, list[QuoteFile]]:
    """上传入口：校验 -> 落盘 -> 建文件/关联/任务记录 -> 报价进入 PARSING。

    uploads 为 FastAPI UploadFile 列表（保持浏览器提交顺序）。成功返回
    (parse_task, quote_files)；失败路径保证数据库与磁盘双干净。
    """
    quote = await _get_quote_with_project(db, quote_id)
    project = await _get_project(db, quote.project_id)

    # 状态守卫：只有 UPLOADED 来源的 DRAFT 容器可以首次上传（SPEC §2.10）
    if quote.source != QuoteSource.UPLOADED or quote.status != QuoteStatus.DRAFT:
        raise QuoteStateError(
            message="当前报价状态不允许上传文件；如需重新解析请使用重新解析入口"
        )
    await _ensure_no_active_task(db, quote.id)
    _apply_model_consent(project, model_processing_consent)

    # 全部预检先于任何落盘：任一文件不合法整批拒绝（422）
    inspected = await inspect_uploads(uploads, settings)

    # rollback 会令 ORM 属性过期，清理路径不能再读 quote.project_id；
    # 事务开始前先缓存标量值（任务范围第 1 条：数据库失败回滚未引用文件）
    project_id = quote.project_id
    created_file_ids: list[int] = []
    try:
        quote_files = await _persist_files(db, quote, inspected, settings, created_file_ids)

        # 报价进入解析中（状态机：DRAFT --上传文件--> PARSING）
        quote.status = QuoteStatus.PARSING

        task = ParseTask(project_id=quote.project_id, quote_id=quote.id)
        db.add(task)
        await db.flush()  # 拿 task.id 以写输入清单
        for order, file_id in enumerate(created_file_ids):
            db.add(ParseTaskFile(task_id=task.id, file_id=file_id, input_order=order))
        await db.commit()
    except Exception:
        # 数据库失败回滚后，清理本次已写入磁盘的文件目录（TASKS.md 范围 1：
        # “数据库失败时回滚未引用文件”）
        await db.rollback()
        for file_id in created_file_ids:
            await asyncio.to_thread(local_files.remove_file_dir, settings, project_id, file_id)
        raise
    return task, quote_files


async def _persist_files(
    db: AsyncSession,
    quote: Quote,
    inspected: list[InspectedFile],
    settings: Settings,
    created_file_ids: list[int],
) -> list[QuoteFile]:
    """逐个文件建库 -> flush 拿 id -> 线程池原子落盘 -> 回填相对路径。"""
    persisted: list[QuoteFile] = []
    for order, item in enumerate(inspected):
        # file_path 以空串占位（NOT NULL 列），落盘后立即回填真实相对路径；
        # 占位值只存在于事务中间态，提交前必然已被覆盖
        quote_file = QuoteFile(
            project_id=quote.project_id,
            file_path="",
            original_name=item.display_name,
            mime=item.mime,
            size_bytes=item.size_bytes,
            page_count=item.page_count,
        )
        db.add(quote_file)
        await db.flush()
        relative = await asyncio.to_thread(
            local_files.save_file_atomic, settings, quote.project_id, quote_file.id, item.data
        )
        quote_file.file_path = relative
        created_file_ids.append(quote_file.id)
        # 展示顺序与提交顺序一致（sortOrder 0 起）；任务输入顺序与之相同，
        # 保证 fileKey（F1/F2/...）与用户感知的文件顺序稳定对应
        db.add(QuoteFileLink(quote_id=quote.id, file_id=quote_file.id, sort_order=order))
        persisted.append(quote_file)
    return persisted


async def reparse_quote(
    db: AsyncSession,
    quote_id: int,
    *,
    model_processing_consent: bool,
    settings: Settings,
) -> ParseTask:
    """未确认报价的重新解析：输入为当前全部关联文件（link 按 sortOrder）。

    适用状态：PARSE_FAILED（失败重试）与 PENDING_CONFIRM（对候选不满意
    重新识别）；报价进入 PARSING。CONFIRMED/MERGE_REVIEW 属 TASK-05。
    """
    quote = await _get_quote_with_project(db, quote_id)
    project = await _get_project(db, quote.project_id)

    if quote.status not in _REPARSEABLE_STATUSES:
        raise QuoteStateError(message="当前报价状态不允许重新解析")
    await _ensure_no_active_task(db, quote.id)
    _apply_model_consent(project, model_processing_consent)

    # 输入范围 = 该报价当前全部关联文件（SPEC §2.10）；无文件则无从解析
    linked_files = (
        await db.execute(
            select(QuoteFile)
            .join(QuoteFileLink, QuoteFileLink.file_id == QuoteFile.id)
            .where(QuoteFileLink.quote_id == quote.id)
            .order_by(QuoteFileLink.sort_order.asc())
        )
    ).scalars().all()
    if not linked_files:
        raise ValidationError(
            code="NO_FILES_TO_PARSE", message="该报价没有已上传的文件，无法解析"
        )

    quote.status = QuoteStatus.PARSING
    task = ParseTask(project_id=quote.project_id, quote_id=quote.id)
    db.add(task)
    await db.flush()
    for order, file in enumerate(linked_files):
        db.add(ParseTaskFile(task_id=task.id, file_id=file.id, input_order=order))
    await db.commit()
    return task


async def get_parse_status(db: AsyncSession, quote_id: int) -> ParseStatusRead:
    """轮询载荷：该报价最近一次解析任务的状态（无任务时 404）。"""
    quote = await _get_quote_with_project(db, quote_id)
    task = (
        await db.execute(
            select(ParseTask)
            .where(ParseTask.quote_id == quote_id)
            .order_by(ParseTask.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if task is None:
        raise NotFoundError(code="PARSE_TASK_NOT_FOUND", message="该报价暂无解析任务")
    file_count = await db.scalar(
        select(func.count()).select_from(ParseTaskFile).where(ParseTaskFile.task_id == task.id)
    )
    return ParseStatusRead(
        task_id=task.id,
        status=task.status,
        attempt=task.attempt,
        error=task.error,
        file_count=int(file_count or 0),
        quote_status=quote.status,
        started_at=task.started_at,
        finished_at=task.finished_at,
    )


async def convert_to_manual(db: AsyncSession, quote_id: int) -> Quote:
    """解析失败后转纯手动：保留已上传文件与任务记录，报价进入 PENDING_CONFIRM。

    状态机（SPEC §2.10）：PARSE_FAILED --转纯手动--> PENDING_CONFIRM。
    转手动后报价可编辑（EDITABLE_STATUSES），文件关联保留供用户对照原单。
    """
    quote = await _get_quote_with_project(db, quote_id)
    if quote.status != QuoteStatus.PARSE_FAILED:
        raise QuoteStateError(message="只有解析失败的报价可以转手动录入")
    quote.status = QuoteStatus.PENDING_CONFIRM
    await db.commit()
    return quote


async def purge_unreferenced_files(db: AsyncSession, project_id: int, settings: Settings) -> int:
    """清理项目中已无任何引用的文件资产（SPEC §2.8 无引用规则）。

    “无引用” = 既无 quote_file_link（兄弟报价共享判定）又无 parse_task_file
    （解析任务回放判定）。满足时删除数据库行与磁盘目录；否则保留。
    在删除报价的事务提交之后调用，保证引用判定基于最终数据。
    """
    linked = select(QuoteFileLink.file_id)
    tasked = select(ParseTaskFile.file_id)
    orphans = (
        await db.execute(
            select(QuoteFile)
            .where(
                QuoteFile.project_id == project_id,
                QuoteFile.id.not_in(linked),
                QuoteFile.id.not_in(tasked),
            )
        )
    ).scalars().all()
    for file in orphans:
        file_id = file.id
        await db.delete(file)
        await asyncio.to_thread(local_files.remove_file_dir, settings, project_id, file_id)
    if orphans:
        await db.commit()
    return len(orphans)


async def load_quote_with_files(db: AsyncSession, quote_id: int) -> Quote:
    """带文件关联加载报价（转手动等接口的读模型构建用）。"""
    stmt = (
        select(Quote)
        .where(Quote.id == quote_id)
        .options(selectinload(Quote.files))
    )
    quote = (await db.execute(stmt)).scalar_one_or_none()
    if quote is None:
        raise NotFoundError(code="QUOTE_NOT_FOUND", message="报价不存在或已被删除")
    return quote
