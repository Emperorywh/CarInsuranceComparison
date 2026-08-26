"""项目 CRUD 路由（SPEC §10）。

状态码约定：创建 201；参数错误 422；不存在 404；统一响应包 {code,message,data}。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.responses import ApiResponse
from app.schemas.project import ProjectCreate, ProjectListItem, ProjectRead, ProjectUpdate
from app.services import project_service

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ApiResponse[ProjectRead], status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProjectRead]:
    """创建续保对比项目。"""
    project = await project_service.create_project(db, payload)
    return ApiResponse.ok(project)


@router.get("", response_model=ApiResponse[list[ProjectListItem]])
async def list_projects(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[ProjectListItem]]:
    """项目列表（含报价数与最低有效净支出聚合；无报价时为稳定空列表）。"""
    items = await project_service.list_projects(db)
    return ApiResponse.ok(items)


@router.get("/{project_id}", response_model=ApiResponse[ProjectRead])
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProjectRead]:
    """项目详情。"""
    project = await project_service.get_project(db, project_id)
    return ApiResponse.ok(project)


@router.patch("/{project_id}", response_model=ApiResponse[ProjectRead])
async def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProjectRead]:
    """编辑项目基础信息。"""
    project = await project_service.update_project(db, project_id, payload)
    return ApiResponse.ok(project)


@router.delete("/{project_id}", response_model=ApiResponse[None])
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[None]:
    """删除项目（不可恢复）：级联删除报价与文件记录，并预约磁盘清理。"""
    await project_service.delete_project(db, project_id)
    return ApiResponse.ok(message="项目已删除")
