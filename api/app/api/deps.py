"""路由公共依赖。"""

from __future__ import annotations

from fastapi import Request

from app.config import Settings
from app.db import get_db

# get_db 从 app.db 导入使用；独立文件便于后续扩展认证/分页等依赖


def get_app_settings(request: Request) -> Settings:
    """取创建应用时注入的配置（测试可覆盖，服务层不直接读全局单例）。"""
    return request.app.state.settings


__all__ = ["get_app_settings", "get_db"]
