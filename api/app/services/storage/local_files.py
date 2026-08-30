"""项目级本地文件存储服务（SPEC §2.3、§9.4；TASK-03 范围 1）。

磁盘布局与安全不变量：
- 布局固定为 ``{UPLOAD_DIR}/{projectId}/{fileId}/{随机文件名}.{ext}``，
  数据库 quote_file.file_path 只存相对 UPLOAD_DIR 的 POSIX 风格路径；
- 磁盘文件名一律随机化（secrets 随机十六进制），用户原始文件名只以
  脱敏后的展示名保存在 original_name，绝不进入路径；
- 写入使用“同目录临时文件 + os.replace 原子移动”，进程中途崩溃不会
  留下半写的最终文件；
- 所有路径拼接结果必须解析回 upload_path 之内（防穿越校验），
  数据库中的 file_path 只能由本模块生成，不信任外部输入拼接。
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path, PurePosixPath

from app.config import Settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)

# 临时文件后缀：与最终文件同目录，保证 os.replace 同卷原子移动
_TEMP_SUFFIX = ".tmp"


class FilePathError(AppError):
    """文件路径越界或资产缺失（对外按 404 处理，不泄露磁盘布局）。"""

    status_code = 404
    code = "FILE_NOT_FOUND"
    message = "文件不存在或已被清理"


def random_file_stem() -> str:
    """生成随机磁盘文件名主干：32 位十六进制，无任何用户输入成分。"""
    return secrets.token_hex(16)


def relative_file_path(project_id: int, file_id: int, disk_name: str) -> str:
    """构造数据库存储的相对路径（POSIX 风格，跨平台稳定）。"""
    return str(PurePosixPath(str(project_id), str(file_id), disk_name))


def resolve_absolute(settings: Settings, relative_path: str) -> Path:
    """把数据库中的相对路径解析为绝对路径，并强制校验未越出上传根目录。

    防穿越：resolve 后必须仍位于 upload_path 之下，否则按文件不存在处理；
    该异常映射 404，绝不向客户端回显磁盘结构。
    """
    root = settings.upload_path
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:  # pragma: no cover - 仅当数据库被直接篡改时触发
        raise FilePathError() from exc
    return candidate


def save_file_atomic(settings: Settings, project_id: int, file_id: int, data: bytes) -> str:
    """把文件内容写入 ``{projectId}/{fileId}/{随机名}`` 并返回相对路径。

    流程（同步函数，CPU/磁盘密集，调用方须放线程池执行）：
    1. 创建文件专属目录；
    2. 先写 ``{随机名}.tmp`` 临时文件；
    3. os.replace 原子移动为最终名（同卷，Windows/POSIX 均为原子操作）。
    失败时尽力清理临时文件，最终文件要么完整存在要么不存在。
    """
    disk_name = f"{random_file_stem()}{_pick_extension(data)}"
    target_dir = settings.upload_path / str(project_id) / str(file_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    temp_path = target_dir / f"{disk_name}{_TEMP_SUFFIX}"
    final_path = target_dir / disk_name
    try:
        with open(temp_path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, final_path)
    except OSError:
        # 写入中断时清掉半成品临时文件；最终文件只有 replace 成功才会出现
        temp_path.unlink(missing_ok=True)
        raise
    return relative_file_path(project_id, file_id, disk_name)


def remove_file_dir(settings: Settings, project_id: int, file_id: int) -> None:
    """删除单个文件的专属目录（幂等；目录不存在视为已清理成功）。"""
    target = settings.upload_path / str(project_id) / str(file_id)
    _remove_dir_quietly(target)


def remove_project_dir(settings: Settings, project_id: int) -> None:
    """删除整个项目的上传目录（幂等；项目删除事务提交后调用）。"""
    target = settings.upload_path / str(project_id)
    _remove_dir_quietly(target)


def _remove_dir_quietly(target: Path) -> None:
    """尽力删除目录；失败只记录不含路径内容的错误（目录名即项目/文件 id）。"""
    import shutil

    try:
        if target.exists():
            shutil.rmtree(target)
    except OSError as exc:
        # 隐私边界：日志不得包含磁盘绝对路径，只记录 id 与异常类型，便于重试
        logger.error("文件目录清理失败 id=%s type=%s", target.name, type(exc).__name__)


# 各支持格式的魔数签名，用于按文件内容推断安全扩展名（SPEC §9.5）
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"%PDF-", ".pdf"),
)


def _pick_extension(data: bytes) -> str:
    """按魔数挑选磁盘扩展名；理论上调用前已完成格式校验，未命中按原始字节存。"""
    for magic, ext in _SIGNATURES:
        if data.startswith(magic):
            return ext
    return ""
