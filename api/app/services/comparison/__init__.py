"""规则对比引擎与服务（TASK-06，SPEC §7）。"""

from app.services.comparison.engine import build_comparison
from app.services.comparison.service import build_project_comparison

__all__ = ["build_comparison", "build_project_comparison"]
