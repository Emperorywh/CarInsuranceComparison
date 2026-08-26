"""路由公共依赖。"""

from __future__ import annotations

# get_db 从 app.db 导入使用；独立文件便于后续扩展认证/分页等依赖
from app.db import get_db

__all__ = ["get_db"]
