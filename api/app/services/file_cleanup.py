"""文件清理服务：项目删除后的磁盘目录清理（TASK-01 预留接口，TASK-03 实现）。

职责边界：
- 数据库级联删除由外键 ON DELETE CASCADE 保证，事务提交即生效；
- 磁盘上 api/uploads/{projectId}/ 目录的物理清理存在“事务提交后才能执行”
  的时序约束，因此以回调接口形式在删除事务成功提交后触发；
- 清理在独立线程池中异步执行，不阻塞删除请求，也不因清理失败回滚删除
  （删除本就不可恢复，数据库记录已消失，磁盘目录成为无害残留）；
- 清理操作幂等：目录不存在视为成功，因此失败后重试安全。

隐私边界：清理日志只记录项目 id 与异常类型名，绝不记录磁盘绝对路径或
文件内容，避免把用户数据位置写进日志。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from app.config import Settings
from app.services.storage import local_files

logger = logging.getLogger(__name__)

# 单线程池串行清理：避免删除请求并发触发同一目录的竞态
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="file-cleanup")


class FileCleanupService(Protocol):
    """项目删除后的磁盘文件清理契约。"""

    def schedule_project_cleanup(self, project_id: int) -> None:
        """在删除事务成功提交后调用；实现必须可重试且不得抛出阻断删除流程的异常。"""
        ...


class NoopFileCleanupService:
    """空实现：只记录待清理日志（测试或未接通存储时使用）。"""

    def schedule_project_cleanup(self, project_id: int) -> None:
        logger.info("项目 %s 已删除；磁盘文件清理未接通（noop 模式）", project_id)


class LocalFileCleanupService:
    """本地磁盘实现：删除 UPLOAD_DIR/{projectId}/ 整个目录（幂等可重试）。"""

    def __init__(self, settings: Settings) -> None:
        # 持有进程配置；上传根目录在应用生命周期内不变
        self._settings = settings

    def schedule_project_cleanup(self, project_id: int) -> None:
        _executor.submit(self.cleanup_now, project_id)

    def cleanup_now(self, project_id: int) -> None:
        """同步执行清理（测试可直接调用；生产经 schedule 异步执行）。"""
        try:
            local_files.remove_project_dir(self._settings, project_id)
            logger.info("项目 %s 的磁盘文件已清理", project_id)
        except Exception as exc:  # 防御：清理绝不反向影响删除主流程
            # 不含路径内容（绝对路径可能暴露用户名/目录结构），可重试
            logger.error(
                "项目 %s 磁盘清理失败 type=%s；可手动删除上传目录或重试",
                project_id,
                type(exc).__name__,
            )


# 进程级单例；create_app 启动时替换为本地实现，测试可按需注入
_service: FileCleanupService = NoopFileCleanupService()


def get_file_cleanup_service() -> FileCleanupService:
    return _service


def set_file_cleanup_service(service: FileCleanupService) -> None:
    global _service
    _service = service
