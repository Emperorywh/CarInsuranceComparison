"""多方案拆分与补传合并路由（SPEC §10；TASK-05）。

状态码约定（与既有接口一致）：查询 200；拆分创建子报价 201；合并解决
返回重算后的完整报价 200；状态机冲突 409；参数/裁决不完整 422。
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_db
from app.config import Settings
from app.core.responses import ApiResponse
from app.schemas.quote import QuoteRead
from app.schemas.split_merge import (
    MergePreviewRead,
    MergeResolveRequest,
    PlanSplitPreviewRead,
    PlanSplitRequest,
    PlanSplitResultRead,
)
from app.services import merge_service, plan_split_service, quote_service

router = APIRouter(tags=["split-merge"])


def _tolerance(settings: Settings) -> Decimal:
    """总额校验容差（配置值 float → Decimal，避免二进制浮点误差）。"""
    return Decimal(str(settings.total_check_tolerance))


# ---- 多方案拆分（SPEC §2.8）----


@router.get(
    "/quotes/{quote_id}/plan-split",
    response_model=ApiResponse[PlanSplitPreviewRead],
)
async def get_plan_split_preview(
    quote_id: int,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PlanSplitPreviewRead]:
    """拆分确认视图：各方案的标签、价格与关键保障摘要（来自成功任务的脱敏 rawResult）。"""
    return ApiResponse.ok(await plan_split_service.get_plan_split_preview(db, quote_id))


@router.post(
    "/quotes/{quote_id}/plan-split",
    response_model=ApiResponse[PlanSplitResultRead],
    status_code=status.HTTP_201_CREATED,
)
async def confirm_plan_split(
    quote_id: int,
    payload: PlanSplitRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[PlanSplitResultRead]:
    """确认拆分：单事务内为保留方案创建平级待确认子报价并删除容器报价。"""
    return ApiResponse.ok(
        await plan_split_service.confirm_plan_split(db, quote_id, payload, settings=settings)
    )


# ---- 补传合并（SPEC §2.9）----


@router.get(
    "/quotes/{quote_id}/merge-preview",
    response_model=ApiResponse[MergePreviewRead],
)
async def get_merge_preview(
    quote_id: int,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MergePreviewRead]:
    """待确认变更清单：旧值、新值、来源、用户编辑标识与默认裁决。"""
    return ApiResponse.ok(await merge_service.get_merge_preview(db, quote_id))


@router.post(
    "/quotes/{quote_id}/merge-resolve",
    response_model=ApiResponse[QuoteRead],
)
async def resolve_merge(
    quote_id: int,
    payload: MergeResolveRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    """逐项 ACCEPT/KEEP；全部解决后原子合并、重算并回到已确认状态。"""
    quote = await merge_service.resolve_merge(db, quote_id, payload, _tolerance(settings))
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))
