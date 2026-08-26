"""数据库级不变量测试（初始迁移已冻结的约束）。

覆盖：关键外键级联、SET NULL、唯一约束、金额非负、活动解析任务互斥、
共享文件关联联合主键。
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ComparisonProject,
    FieldEvidence,
    ParseTask,
    ParseTaskFile,
    Quote,
    QuoteCoverage,
    QuoteFile,
    QuoteFileLink,
)
from app.models.enums import (
    ConfidenceLevel,
    CoverageCategory,
    ItemStatus,
    ParseTaskStatus,
    QuoteSource,
    QuoteStatus,
)


async def _seed_project_with_quote(db_session: AsyncSession) -> tuple[int, int]:
    """建一个项目 + 一份报价，返回 (projectId, quoteId)。"""
    project = ComparisonProject(
        name="测试项目", vehicle_name="Model Y", renewal_year=2026
    )
    db_session.add(project)
    await db_session.flush()
    quote = Quote(
        project_id=project.id,
        insurer_code="PINGAN",
        insurer_name="平安",
        source=QuoteSource.UPLOADED,
        status=QuoteStatus.DRAFT,
    )
    db_session.add(quote)
    await db_session.flush()
    return project.id, quote.id


async def test_delete_project_cascades_everything(db_session: AsyncSession) -> None:
    """删除项目：报价、文件、任务、明细、关联全部级联清除。"""
    project_id, quote_id = await _seed_project_with_quote(db_session)
    file = QuoteFile(
        project_id=project_id,
        file_path="2026/1/xxx.jpg",
        original_name="报价.jpg",
        mime="image/jpeg",
        size_bytes=100,
        page_count=1,
    )
    db_session.add(file)
    await db_session.flush()
    file_id = file.id
    db_session.add_all(
        [
            QuoteFileLink(quote_id=quote_id, file_id=file_id, sort_order=0),
            QuoteCoverage(
                quote_id=quote_id,
                category=CoverageCategory.CORE,
                raw_name="三者",
                name="三者险",
                status=ItemStatus.INCLUDED,
                confidence_level=ConfidenceLevel.HIGH,
            ),
        ]
    )
    task = ParseTask(project_id=project_id, quote_id=quote_id, status=ParseTaskStatus.PENDING)
    db_session.add(task)
    await db_session.commit()
    task_id = task.id

    project = await db_session.get(ComparisonProject, project_id)
    await db_session.delete(project)
    await db_session.commit()
    # 级联删除由数据库执行，过期身份缓存避免读到“还活着”的旧对象
    db_session.expire_all()

    assert (await db_session.get(Quote, quote_id)) is None
    assert (await db_session.get(QuoteFile, file_id)) is None
    assert (await db_session.get(ParseTask, task_id)) is None
    links = (await db_session.execute(select(QuoteFileLink))).scalars().all()
    assert links == []


async def test_delete_quote_sets_parse_task_null(db_session: AsyncSession) -> None:
    """删除报价：解析任务的 quote_id 置空但任务保留（回放数据不丢失）。"""
    project_id, quote_id = await _seed_project_with_quote(db_session)
    task = ParseTask(project_id=project_id, quote_id=quote_id, status=ParseTaskStatus.SUCCEEDED)
    db_session.add(task)
    await db_session.commit()
    task_id = task.id

    quote = await db_session.get(Quote, quote_id)
    await db_session.delete(quote)
    await db_session.commit()

    # SET NULL 由数据库执行，会话身份缓存里的旧对象不会自动刷新，先过期再读
    db_session.expire_all()
    refreshed = await db_session.get(ParseTask, task_id)
    assert refreshed is not None
    assert refreshed.quote_id is None
    assert refreshed.project_id == project_id


async def test_field_evidence_unique_per_field(db_session: AsyncSession) -> None:
    """(quote_id, field_name) 唯一：同一标量字段的第二条证据必须被拒绝。"""
    _, quote_id = await _seed_project_with_quote(db_session)
    db_session.add_all(
        [
            FieldEvidence(
                quote_id=quote_id,
                field_name="commercialPremium",
                confidence_level=ConfidenceLevel.HIGH,
            ),
            FieldEvidence(
                quote_id=quote_id,
                field_name="commercialPremium",
                confidence_level=ConfidenceLevel.MEDIUM,
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_negative_amounts_rejected(db_session: AsyncSession) -> None:
    """金额非负：负保费、负净支出都被数据库拒绝。"""
    _, quote_id = await _seed_project_with_quote(db_session)
    quote = await db_session.get(Quote, quote_id)
    quote.net_payment = Decimal("-1.00")
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_active_parse_task_mutex(db_session: AsyncSession) -> None:
    """同一报价最多一个活动任务（PENDING/RUNNING）；终态任务不占用互斥。"""
    project_id, quote_id = await _seed_project_with_quote(db_session)
    db_session.add(ParseTask(project_id=project_id, quote_id=quote_id, status=ParseTaskStatus.PENDING))
    await db_session.commit()

    # 第二个活动任务违反部分唯一索引
    db_session.add(ParseTask(project_id=project_id, quote_id=quote_id, status=ParseTaskStatus.RUNNING))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # 把第一个任务置为终态后，可以再创建新任务
    task = (
        await db_session.execute(
            select(ParseTask).where(ParseTask.quote_id == quote_id)
        )
    ).scalar_one()
    task.status = ParseTaskStatus.FAILED
    await db_session.commit()

    db_session.add(ParseTask(project_id=project_id, quote_id=quote_id, status=ParseTaskStatus.PENDING))
    await db_session.commit()  # 不再抛错


async def test_shared_file_link_composite_pk(db_session: AsyncSession) -> None:
    """共享文件：同一 (quote, file) 不能重复关联；不同报价可关联同一文件。"""
    project_id, quote_id = await _seed_project_with_quote(db_session)
    sibling = Quote(
        project_id=project_id,
        insurer_code="PICC",
        insurer_name="人保",
        source=QuoteSource.UPLOADED,
        status=QuoteStatus.DRAFT,
    )
    db_session.add(sibling)
    await db_session.flush()
    # 回滚会使身份缓存中的实例过期（过期实例属性访问会触发同步 IO），
    # 需要断言的标识先取快照
    sibling_id = sibling.id
    file = QuoteFile(
        project_id=project_id,
        file_path="2026/2/yyy.pdf",
        original_name="报价.pdf",
        mime="application/pdf",
        size_bytes=200,
        page_count=2,
    )
    db_session.add(file)
    await db_session.flush()
    file_id = file.id

    # 两个报价共享同一文件 —— 允许（多方案拆分的基础）
    db_session.add_all(
        [
            QuoteFileLink(quote_id=quote_id, file_id=file_id, sort_order=0),
            QuoteFileLink(quote_id=sibling_id, file_id=file_id, sort_order=0),
        ]
    )
    await db_session.commit()

    # 重复关联被联合主键拒绝
    db_session.add(QuoteFileLink(quote_id=quote_id, file_id=file_id, sort_order=1))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # 删除其中一个报价：其关联消失，文件与兄弟报价的关联保留
    await db_session.delete(await db_session.get(Quote, quote_id))
    await db_session.commit()
    db_session.expire_all()
    assert (await db_session.get(QuoteFile, file_id)) is not None
    remaining = (
        await db_session.execute(select(QuoteFileLink).where(QuoteFileLink.file_id == file_id))
    ).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].quote_id == sibling_id


async def test_parse_task_input_files_recorded(db_session: AsyncSession) -> None:
    """parse_task_file 固定任务输入与顺序，保证 fileKey 分配可回放。"""
    project_id, quote_id = await _seed_project_with_quote(db_session)
    file = QuoteFile(
        project_id=project_id,
        file_path="2026/3/zzz.png",
        original_name="补充.png",
        mime="image/png",
        size_bytes=50,
        page_count=1,
    )
    db_session.add(file)
    task = ParseTask(project_id=project_id, quote_id=quote_id, status=ParseTaskStatus.PENDING)
    db_session.add(task)
    await db_session.flush()
    db_session.add(ParseTaskFile(task_id=task.id, file_id=file.id, input_order=0))
    await db_session.commit()

    rows = (await db_session.execute(select(ParseTaskFile))).scalars().all()
    assert len(rows) == 1
    assert rows[0].input_order == 0
