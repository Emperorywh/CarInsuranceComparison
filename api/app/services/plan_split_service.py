"""多方案拆分（SPEC §2.8；TASK-05 范围 1-3）。

业务不变量：
- 同公司 planCount > 1 的成功解析只落脱敏 rawResult（TASK-04）；本模块
  提供“拆分确认视图 + 确认拆分事务”两个能力：
  - 预览从 rawResult 回放各方案摘要（planLabel、价格、关键保障）；
  - 确认在单个数据库事务内为每个保留方案创建平级 PENDING_CONFIRM
    子报价，复用 candidate_writer.apply_single_plan 写入候选结构化数据，
    并为每个子报价复制与容器相同的 quote_file_link（文件资产不复制）；
  - 成功后删除容器报价，parse_task.quote_id 因 ON DELETE SET NULL 置空，
    rawResult 与任务输入文件引用全部保留可回放；
- 只允许同一保险公司的多方案拆分：混合公司批次在流水线阶段已被明确
  失败（TASK-04），本模块从 rawResult 回放，天然不会出现混合公司；
- 任何失败整体回滚：不留下部分子报价、孤儿关联或丢失 rawResult。

隐私边界：rawResult 已整树脱敏；用户改写的方案标签入库前统一脱敏；
本模块错误文案为固定中文提示，不携带原文。
"""

from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import ConflictError, NotFoundError, QuoteStateError, ValidationError
from app.core.privacy import sanitize_text
from app.models import (
    ParseTask,
    Quote,
    QuoteFileLink,
)
from app.models.enums import ParseTaskStatus, QuoteSource, QuoteStatus
from app.schemas.split_merge import (
    PlanSplitCoverageSummary,
    PlanSplitPlanPreview,
    PlanSplitPreviewRead,
    PlanSplitPriceItem,
    PlanSplitQuoteRead,
    PlanSplitRequest,
    PlanSplitResultRead,
)
from app.services.parser.candidate_writer import EvidenceResolver, apply_single_plan
from app.services.parser.extraction_schema import ExtractionResult
from app.services.parser.worker import load_task_context

# 默认方案标签：模型未给标签时按“方案 1/2/...”生成（展示层口径）
_DEFAULT_PLAN_LABEL = "方案 {n}"


async def _get_split_context(db: AsyncSession, quote_id: int) -> tuple[Quote, ParseTask, list]:
    """拆分入口公共校验：容器状态 + 最新成功任务的 rawResult 回放。

    返回 (容器报价, 解析任务, ExtractionResult)。任何不满足拆分前提的
    情况都以语义化错误停止：
    - 报价不存在 → 404；状态非待确认 → 409（已确认报价不存在拆分语义）；
    - 没有成功任务或 planCount <= 1 → 409（无可拆分的方案数据）；
    - rawResult 结构无法回放（极端脱敏边界）→ 422，引导手动录入。
    """
    quote = await db.get(Quote, quote_id)
    if quote is None:
        raise NotFoundError(code="QUOTE_NOT_FOUND", message="报价不存在或已被删除")
    if quote.status != QuoteStatus.PENDING_CONFIRM:
        raise QuoteStateError(message="只有待确认的报价支持多方案拆分")

    task = (
        await db.execute(
            select(ParseTask)
            .where(
                ParseTask.quote_id == quote_id,
                ParseTask.status == ParseTaskStatus.SUCCEEDED,
                ParseTask.raw_result.is_not(None),
            )
            .order_by(ParseTask.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if task is None or not isinstance(task.raw_result.get("plans"), list):
        raise ConflictError(code="NO_SPLITTABLE_PLANS", message="该报价没有可拆分的多方案解析结果")
    plan_count = task.raw_result.get("planCount")
    if not isinstance(plan_count, int) or plan_count <= 1 or len(task.raw_result["plans"]) <= 1:
        raise ConflictError(code="NO_SPLITTABLE_PLANS", message="该报价没有可拆分的多方案解析结果")

    # 回放校验：rawResult 是脱敏后的完整模型输出，结构与写入时一致；
    # 极端情况下敏感内容整段脱敏可能让必填文本变成空串，此时明确失败
    try:
        extraction = ExtractionResult.model_validate(task.raw_result)
    except PydanticValidationError:
        raise ValidationError(
            code="RAW_RESULT_NOT_REPLAYABLE",
            message="解析结果无法回放（关键内容已在脱敏时隐藏），请改用手动录入",
        ) from None
    if len(extraction.plans) <= 1:
        raise ConflictError(code="NO_SPLITTABLE_PLANS", message="该报价没有可拆分的多方案解析结果")
    return quote, task, extraction


def _plan_preview(index: int, extraction: ExtractionResult) -> PlanSplitPlanPreview:
    """从回放结果构建单方案预览卡片（纯函数，只读不落库）。"""
    plan = extraction.plans[index]
    prices = {
        name: PlanSplitPriceItem(value=item.value, status=item.status)
        for name, item in (
            ("commercialPremium", plan.pricing.commercialPremium),
            ("compulsoryPremium", plan.pricing.compulsoryPremium),
            ("vehicleTax", plan.pricing.vehicleTax),
            ("packageTotal", plan.pricing.packageTotal),
            ("otherFees", plan.pricing.otherFees),
            ("officialTotal", plan.pricing.officialTotal),
        )
    }

    def coverage_summaries(items) -> list[PlanSplitCoverageSummary]:  # noqa: ANN001
        return [
            PlanSplitCoverageSummary(
                name=row.rawName,
                status=row.status,
                coverage_amount=row.coverageAmount,
                premium=row.premium,
            )
            for row in items
        ]

    return PlanSplitPlanPreview(
        index=index,
        plan_label=plan.planLabel,
        prices=prices,
        core_coverages=coverage_summaries(plan.coreCoverages),
        additional_coverages=coverage_summaries(plan.additionalCoverages),
        package_summaries=[
            f"{package.name} {package.premium}元" if package.premium is not None else package.name
            for package in plan.supplementalPackages
        ],
        service_summaries=[service.rawName for service in plan.services],
        annotation_count=len(plan.annotations),
        unmatched_count=len(plan.unmatchedItems),
    )


async def get_plan_split_preview(
    db: AsyncSession, quote_id: int
) -> PlanSplitPreviewRead:
    """拆分确认视图：展示各 planLabel、价格与关键保障摘要（TASKS 范围 1）。"""
    quote, task, extraction = await _get_split_context(db, quote_id)
    return PlanSplitPreviewRead(
        quote_id=quote.id,
        task_id=task.id,
        plan_count=len(extraction.plans),
        insurer_name=quote.insurer_name,
        plans=[_plan_preview(index, extraction) for index in range(len(extraction.plans))],
    )


async def confirm_plan_split(
    db: AsyncSession,
    quote_id: int,
    payload: PlanSplitRequest,
    *,
    settings: Settings,
) -> PlanSplitResultRead:
    """确认拆分：单事务内为保留方案创建子报价并删除容器（TASKS 范围 2）。

    事务步骤（任一步失败整体回滚）：
      1) 公共校验 + rawResult 回放；
      2) 为每个保留方案创建子报价（继承容器的公司/保险员/来源，状态
         PENDING_CONFIRM，planLabel 用用户改写值）并写入该方案候选数据；
      3) 为每个子报价复制容器的全部 quote_file_link（文件资产共享）；
      4) 删除容器报价（明细/关联级联删除，parse_task.quote_id 置空）；
      5) 提交。
    """
    quote, _task, extraction = await _get_split_context(db, quote_id)

    # 保留方案至少一个（全部丢弃没有业务意义，直接 422）
    for item in payload.plans:
        if item.index >= len(extraction.plans):
            raise ValidationError(
                code="PLAN_INDEX_OUT_OF_RANGE",
                message=f"方案序号 {item.index} 不存在，请刷新拆分预览后重试",
            )

    # 任务输入文件（fileKey 分配顺序）重建证据解析器，保证子报价的
    # sourceFileId 与原任务一致（多方案共享同一批原文件）
    context = await load_task_context(db, _task)
    resolver = EvidenceResolver(context.files)

    # 容器文件关联快照：每个子报价共享同一组 (fileId, sortOrder)
    container_links = (
        await db.execute(
            select(QuoteFileLink)
            .where(QuoteFileLink.quote_id == quote.id)
            .order_by(QuoteFileLink.sort_order.asc())
        )
    ).scalars().all()

    try:
        created: list[Quote] = []
        for item in payload.plans:
            plan = extraction.plans[item.index]
            label = (sanitize_text(item.plan_label.strip()) if item.plan_label and item.plan_label.strip() else None)
            if not label:
                label = plan.planLabel or _DEFAULT_PLAN_LABEL.format(n=item.index + 1)
            child = Quote(
                project_id=quote.project_id,
                # 公司继承容器（同公司多方案）；模型识别公司作为证据写入
                # 子报价，确认页的公司冲突二选一照常生效
                insurer_code=quote.insurer_code,
                insurer_name=quote.insurer_name,
                agent_name=quote.agent_name,
                plan_label=label,
                source=QuoteSource.UPLOADED,
                status=QuoteStatus.PENDING_CONFIRM,
            )
            db.add(child)
            await db.flush()  # 拿子报价 id，候选写入与关联复制都需要
            await apply_single_plan(
                db, quote=child, plan=plan, extraction=extraction,
                resolver=resolver, settings=settings,
            )
            for link in container_links:
                db.add(
                    QuoteFileLink(
                        quote_id=child.id, file_id=link.file_id, sort_order=link.sort_order
                    )
                )
            created.append(child)

        # 删除容器报价：明细与关联级联删除；parse_task.quote_id 由数据库
        # SET NULL，rawResult 与输入文件引用保留（SPEC §2.8 归属规则）
        await db.delete(quote)
        await db.commit()
    except Exception:
        # 拆分必须是全有或全无：回滚后不留下部分子报价或孤儿关联
        await db.rollback()
        raise

    return PlanSplitResultRead(
        quotes=[_quote_ref(child) for child in created],
    )


def _quote_ref(quote: Quote) -> PlanSplitQuoteRead:
    return PlanSplitQuoteRead(
        id=quote.id,
        project_id=quote.project_id,
        insurer_code=quote.insurer_code,
        insurer_name=quote.insurer_name,
        agent_name=quote.agent_name,
        plan_label=quote.plan_label,
        status=quote.status,
    )
