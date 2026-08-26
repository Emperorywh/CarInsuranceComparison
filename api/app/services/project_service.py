"""项目领域服务：CRUD、列表聚合与详情分组报价卡。

隐私边界：用户自由文本（项目名/车辆名称/备注）入库前统一经
app.core.privacy.sanitize_text 脱敏，路由层不得绕过。
"""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ProjectNotFoundError
from app.core.privacy import sanitize_text
from app.models import ComparisonProject, Quote
from app.models.enums import CoverageCategory, ItemStatus, QuoteStatus
from app.schemas.project import (
    ProjectCreate,
    ProjectDetail,
    ProjectListItem,
    ProjectRead,
    ProjectUpdate,
    QuoteCardSummary,
    QuoteGroup,
)
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


async def build_project_detail(db: AsyncSession, project: ComparisonProject) -> ProjectDetail:
    """组装项目详情：基础字段 + 按“保险公司 + 保险员”分组的报价卡。

    分组规则（SPEC §8 / 决策 #9）：同「公司+保险员」的报价并列展示并提示
    “同来源报价”，只提示不创建版本链；卡片摘要取已包含行中的最大保额。
    """
    stmt = (
        select(Quote)
        .where(Quote.project_id == project.id)
        .options(selectinload(Quote.coverages))
        .order_by(Quote.created_at.asc(), Quote.id.asc())
    )
    quotes = (await db.execute(stmt)).scalars().all()

    def summary_amount(quote: Quote, code: str) -> float | None:
        # 三者/医保外摘要：已包含行中取最大保额；缺失保持 null（不当 0）
        amounts = [
            row.coverage_amount
            for row in quote.coverages
            if row.code == code
            and row.status == ItemStatus.INCLUDED
            and row.category != CoverageCategory.UNRECOGNIZED
            and row.coverage_amount is not None
        ]
        return float(max(amounts)) if amounts else None

    # 分组键 = 公司码 + 公司显示名 + 保险员：OTHER 的自由输入名不同则不同组
    groups: dict[tuple[str, str, str], list[QuoteCardSummary]] = {}
    for quote in quotes:
        key = (quote.insurer_code, quote.insurer_name, quote.agent_name or "")
        groups.setdefault(key, []).append(
            QuoteCardSummary(
                id=quote.id,
                insurer_code=quote.insurer_code,
                insurer_name=quote.insurer_name,
                agent_name=quote.agent_name,
                plan_label=quote.plan_label,
                source=quote.source,
                status=quote.status,
                net_payment=(
                    float(quote.net_payment) if quote.net_payment is not None else None
                ),
                net_payment_status=quote.net_payment_status,
                official_total=(
                    float(quote.official_total) if quote.official_total is not None else None
                ),
                computed_total=(
                    float(quote.computed_total) if quote.computed_total is not None else None
                ),
                total_check_status=quote.total_check_status,
                third_party_amount=summary_amount(quote, "THIRD_PARTY_LIABILITY"),
                tp_non_medical_amount=summary_amount(quote, "TP_NON_MEDICAL"),
                created_at=quote.created_at,
            )
        )

    # 组间顺序 = 组内最早报价的出现顺序（quotes 已按 created_at 排序，稳定可预期）
    quote_groups = [
        QuoteGroup(
            insurer_code=code,
            insurer_name=name,
            agent_name=agent or None,
            # 同组多份报价只提示“同来源”，不创建版本链（决策 #9）
            same_source_hint=len(cards) > 1,
            quotes=cards,
        )
        for (code, name, agent), cards in groups.items()
    ]
    base = ProjectRead.model_validate(project)
    return ProjectDetail(**base.model_dump(), quote_groups=quote_groups)


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
