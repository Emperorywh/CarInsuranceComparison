"""文件与解析任务相关请求/响应模型（SPEC §2.3、§2.4、§10）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.models.enums import ParseTaskStatus, QuoteStatus
from app.schemas.common import CamelModel


class FileRead(CamelModel):
    """报价关联文件的展示信息（原文件本体只能经受控 raw 接口读取）。"""

    id: int
    # 已脱敏的展示名；数据库不保存用户原始文件名
    file_name: str
    mime: str
    size_bytes: int
    page_count: int = Field(description="图片固定 1，PDF 为实际页数")
    # 原文件预览的受控相对地址（带项目归属参数）；前端须拼 API_BASE_URL 并
    # 以 X-Access-Token 头请求，绝不落入公开静态目录
    raw_url: str


class TaskCreatedRead(CamelModel):
    """创建解析任务（上传/重解析）的 202 响应载荷。"""

    task_id: int
    quote_id: int


class UploadFilesResultRead(TaskCreatedRead):
    """上传成功的 202 响应：任务标识 + 已入库文件（按提交顺序）。"""

    files: list[FileRead]


class ParseStatusRead(CamelModel):
    """解析任务轮询载荷（SPEC §10 GET /quotes/{id}/parse-status）。

    前端每 3 秒轮询一次；status 为终态（SUCCEEDED/FAILED）时停止轮询。
    error 为脱敏后的中文摘要，可直接展示。
    TASK-04：planCount 来自成功任务的脱敏 rawResult——>1 时确认页展示
    “多方案待拆分”占位提示（拆分确认视图属 TASK-05）；其余情况为 None。
    """

    task_id: int
    status: ParseTaskStatus
    attempt: int = Field(description="已执行的总尝试次数（首次为 1，最大 3）")
    error: str | None = None
    file_count: int
    quote_status: QuoteStatus
    plan_count: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
