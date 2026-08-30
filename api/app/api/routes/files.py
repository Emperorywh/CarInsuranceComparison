"""文件资产与解析任务路由（SPEC §10；TASK-03 范围 4-5）。

状态码口径（TASKS.md 验证 1 的硬性约定）：
- 报价容器创建（既有接口）返回 201；
- 上传接口 POST /quotes/{id}/files 无论何种成功路径一律返回 202 并携带
  taskId，绝不引入 201 分支；
- 同一报价已有活动解析任务 409；缺少模型传输同意 422；
- 原文件接口 GET /files/{fileId}/raw 校验访问令牌（统一中间件）与文件
  项目归属，以 inline 流返回，绝不挂到公开静态目录。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_db
from app.config import Settings
from app.core.responses import ApiResponse
from app.models import QuoteFile
from app.schemas.file import (
    FileRead,
    ParseStatusRead,
    TaskCreatedRead,
    UploadFilesResultRead,
)
from app.schemas.quote import QuoteRead
from app.services import parse_service, quote_service
from app.services.storage import local_files

router = APIRouter(tags=["files"])


@router.post(
    "/quotes/{quote_id}/files",
    response_model=ApiResponse[UploadFilesResultRead],
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_quote_files(
    quote_id: int,
    files: list[UploadFile] = File(..., description="JPEG/PNG/PDF，最多 12 个"),
    model_processing_consent: bool = Form(
        False, alias="modelProcessingConsent", description="首次解析的模型传输同意"
    ),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[UploadFilesResultRead]:
    """多文件上传并创建解析任务（202）。前置建报价容器仍是 201 接口。"""
    task, quote_files = await parse_service.create_quote_files(
        db,
        quote_id,
        files,
        model_processing_consent=model_processing_consent,
        settings=settings,
    )
    return ApiResponse.ok(
        UploadFilesResultRead(
            task_id=task.id,
            quote_id=quote_id,
            files=[FileRead.model_validate(f) for f in quote_files],
        )
    )


@router.get("/quotes/{quote_id}/parse-status", response_model=ApiResponse[ParseStatusRead])
async def get_parse_status(
    quote_id: int,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ParseStatusRead]:
    """解析任务轮询：任务状态、已尝试次数、脱敏错误摘要与文件数。"""
    return ApiResponse.ok(await parse_service.get_parse_status(db, quote_id))


@router.post(
    "/quotes/{quote_id}/reparse",
    response_model=ApiResponse[TaskCreatedRead],
    status_code=status.HTTP_202_ACCEPTED,
)
async def reparse_quote(
    quote_id: int,
    model_processing_consent: bool = Form(
        False, alias="modelProcessingConsent", description="首次解析的模型传输同意"
    ),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[TaskCreatedRead]:
    """未确认报价的重新解析（输入为全部关联文件）；活动任务冲突 409。"""
    task = await parse_service.reparse_quote(
        db,
        quote_id,
        model_processing_consent=model_processing_consent,
        settings=settings,
    )
    return ApiResponse.ok(TaskCreatedRead(task_id=task.id, quote_id=quote_id))


@router.post("/quotes/{quote_id}/convert-manual", response_model=ApiResponse[QuoteRead])
async def convert_quote_to_manual(
    quote_id: int,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[QuoteRead]:
    """解析失败转纯手动：保留已上传文件，报价进入 PENDING_CONFIRM。"""
    await parse_service.convert_to_manual(db, quote_id)
    full = await quote_service.load_quote_full(db, quote_id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.get("/files/{file_id}/raw")
async def get_raw_file(
    file_id: int,
    project_id: int = Query(..., alias="projectId", description="文件归属项目，用于归属校验"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> FileResponse:
    """原文件受控读取：校验令牌（统一中间件）+ 项目归属，inline 流返回。

    归属不一致与不存在统一按 404 处理，不向客户端泄露文件存在性；
    绝不通过 Next.js public/ 或 FastAPI StaticFiles 暴露上传目录。
    """
    file = await db.get(QuoteFile, file_id)
    if file is None or file.project_id != project_id:
        raise local_files.FilePathError()
    absolute = local_files.resolve_absolute(settings, file.file_path)
    if not absolute.is_file():
        raise local_files.FilePathError()
    return FileResponse(
        absolute,
        media_type=file.mime,
        headers={"Content-Disposition": "inline"},
    )
