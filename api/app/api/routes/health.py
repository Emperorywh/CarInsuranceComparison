"""健康检查：唯一豁免访问令牌的端点（SPEC §9.4）。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.responses import ApiResponse

router = APIRouter(tags=["health"])


class HealthData(BaseModel):
    status: str


@router.get("/health", response_model=ApiResponse[HealthData])
async def health() -> ApiResponse[HealthData]:
    """存活探针；不触碰数据库，供容器/脚本判断服务是否起来。"""
    return ApiResponse.ok(HealthData(status="ok"))
