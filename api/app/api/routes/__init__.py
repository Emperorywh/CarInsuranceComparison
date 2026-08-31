"""路由注册总出口：业务路由统一挂 /api 前缀，健康检查独立豁免令牌。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import files, health, projects, quotes, split_merge

api_router = APIRouter()
# /health 不带 /api 前缀：探活与启动检查需要匿名访问
api_router.include_router(health.router)
api_router.include_router(projects.router, prefix="/api")
api_router.include_router(quotes.router, prefix="/api")
api_router.include_router(files.router, prefix="/api")
# TASK-05：多方案拆分与补传合并
api_router.include_router(split_merge.router, prefix="/api")
