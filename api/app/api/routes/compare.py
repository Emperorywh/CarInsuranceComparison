"""项目对比路由（TASK-06，SPEC §10）：GET /api/projects/{id}/compare。

只读接口：不修改报价数据、不读取原图、不调用模型；
数量与状态校验在服务层给出语义化错误码（COMPARE_*）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.responses import ApiResponse
from app.schemas.compare import ComparisonResult
from app.services import project_service
from app.services.comparison.service import build_project_comparison, parse_quote_ids

router = APIRouter(tags=["compare"])


@router.get(
    "/projects/{project_id}/compare",
    response_model=ApiResponse[ComparisonResult],
    responses={
        404: {"description": "项目或报价不存在"},
        422: {"description": "报价数量/归属/状态不满足对比条件"},
    },
)
async def compare_project_quotes(
    project_id: int,
    # 对外契约使用 camelCase 查询参数；FastAPI 按参数名解析为 query string
    quoteIds: str,  # noqa: N803
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ComparisonResult]:
    """多报价对比：单一总表全部指标行（quoteIds 为逗号分隔的报价编号）。"""
    await project_service.get_project(db, project_id)
    quote_ids = parse_quote_ids(quoteIds)
    result = await build_project_comparison(db, project_id, quote_ids)
    return ApiResponse.ok(result)
