"""文件清理服务接口（预留，TASK-03 接通磁盘实现）。

职责边界：
- 数据库级联删除由外键 ON DELETE CASCADE 保证，事务提交即生效；
- 磁盘上 api/uploads/{projectId}/ 目录的物理清理存在“事务提交后才能执行”
  的时序约束，因此以回调接口形式预留；TASK-01 使用空实现（记录日志），
  TASK-03 替换为真实实现并处理失败重试。
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class FileCleanupService(Protocol):
    """项目删除后的磁盘文件清理契约。"""

    def schedule_project_cleanup(self, project_id: int) -> None:
        """在删除事务成功提交后调用；实现必须可重试且不得抛出阻断删除流程的异常。"""
        ...


class NoopFileCleanupService:
    """TASK-01 的空实现：只记录待清理日志，磁盘清理由 TASK-03 接通。"""

    def schedule_project_cleanup(self, project_id: int) -> None:
        logger.info("项目 %s 已删除；磁盘文件清理待 TASK-03 文件服务接通", project_id)


# 进程级单例；TASK-03/测试通过 set_file_cleanup_service 替换实现
_service: FileCleanupService = NoopFileCleanupService()


def get_file_cleanup_service() -> FileCleanupService:
    return _service


def set_file_cleanup_service(service: FileCleanupService) -> None:
    global _service
    _service = service
