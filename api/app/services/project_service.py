"""项目领域服务：CRUD 与列表聚合。

隐私边界：用户自由文本（项目名/车辆名称/备注）入库前统一经
app.core.privacy.sanitize_text 脱敏，路由层不得绕过。
"""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProjectNotFoundError
from app.core.privacy import sanitize_text
from app.models import ComparisonProject, Quote
from app.models.enums import QuoteStatus
from app.schemas.project import ProjectCreate, ProjectListItem, ProjectRead, ProjectUpdate
from app.services.file_cleanup import get_file_cleanup_service


async def get_project(db: AsyncSession, project_id: int) -> ComparisonProject:
    """按 id 取项目；不存在时抛 404 业务错误。"""
    project = await db.get(ComparisonProject, project_id)
    if project is None:
        raise ProjectNotFoundError()
    return project


async def create_project(db: AsyncSession, payload: ProjectCreate) -> ComparisonProject:
    """创建项目；三个自由文本字段统一脱敏后入库。"""
    project = ComparisonProject(
        name=sanitize_text(payload.name),
        vehicle_name=sanitize_text(payload.vehicle_name),
        renewal_year=payload.renewal_year,
        expire_date=payload.expire_date,
        note=sanitize_text(payload.note) if payload.note else None,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


async def update_project(
    db: AsyncSession, project_id: int, payload: ProjectUpdate
) -> ComparisonProject:
    """部分更新：只应用显式提供的字段（model_fields_set 区分“未传”与“传 null”）。"""
    project = await get_project(db, project_id)
    provided = payload.model_fields_set
    if "name" in provided and payload.name is not None:
        project.name = sanitize_text(payload.name)
    if "vehicle_name" in provided and payload.vehicle_name is not None:
        project.vehicle_name = sanitize_text(payload.vehicle_name)
    if "renewal_year" in provided and payload.renewal_year is not None:
        project.renewal_year = payload.renewal_year
    if "expire_date" in provided:
        # 允许把到期日显式清空
        project.expire_date = payload.expire_date
    if "note" in provided:
        project.note = sanitize_text(payload.note) if payload.note else None
    await db.commit()
    await db.refresh(project)
    return project


async def delete_project(db: AsyncSession, project_id: int) -> None:
    """删除项目：数据库级联清掉报价/文件/任务记录；提交成功后通知文件清理服务。

    删除不可恢复；磁盘目录清理由 FileCleanupService（TASK-03 实现）接手，
    清理失败不影响数据库删除结果，但会被记录日志供重试。
    """
    project = await get_project(db, project_id)
    await db.delete(project)
    await db.commit()
    get_file_cleanup_service().schedule_project_cleanup(project_id)


async def list_projects(db: AsyncSession) -> list[ProjectListItem]:
    """项目列表：按创建时间倒序，附带报价数与最低有效净支出。

    聚合口径：
    - quoteCount 统计项目下全部报价（含草稿/待确认）；
    - minNetPayment 只统计已确认（CONFIRMED/MERGE_REVIEW）且净支出非空的报价，
      与对比页“可对比状态”口径一致，避免未确认草稿误导首页“最低价”。
    """
    comparable_net_payment = case(
        (
            Quote.status.in_((QuoteStatus.CONFIRMED, QuoteStatus.MERGE_REVIEW)),
            Quote.net_payment,
        ),
    )
    stmt = (
        select(
            ComparisonProject,
            func.count(Quote.id).label("quote_count"),
            func.min(comparable_net_payment).label("min_net_payment"),
        )
        .outerjoin(Quote, Quote.project_id == ComparisonProject.id)
        .group_by(ComparisonProject.id)
        .order_by(ComparisonProject.created_at.desc(), ComparisonProject.id.desc())
    )
    rows = (await db.execute(stmt)).all()
    items: list[ProjectListItem] = []
    for project, quote_count, min_net_payment in rows:
        base = ProjectRead.model_validate(project)
        items.append(
            ProjectListItem(
                **base.model_dump(),
                quote_count=int(quote_count),
                min_net_payment=(
                    float(min_net_payment) if min_net_payment is not None else None
                ),
            )
        )
    return items
