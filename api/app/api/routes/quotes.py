"""报价与字典路由（SPEC §10）。

状态码约定：创建资源 201；参数/Schema 错误 422；不存在 404；
状态机冲突 409；统一响应包 {code, message, data}。
所有明细层写操作的响应都是重算后的完整 QuoteRead（前端据此整体刷新）。
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_db
from app.config import Settings
from app.core.responses import ApiResponse
from app.schemas.quote import (
    AnnotationCreate,
    AnnotationUpdate,
    CoverageCreate,
    CoverageUpdate,
    DictionariesRead,
    DiscountCreate,
    DiscountUpdate,
    PackageCoverageCreate,
    PackageCoverageUpdate,
    PackageCreate,
    PackageUpdate,
    QuoteConfirm,
    QuoteCreate,
    QuoteRead,
    QuoteUpdate,
    ServiceCreate,
    ServiceUpdate,
)
from app.services import dictionaries as dictionary_service
from app.services import quote_service

router = APIRouter(tags=["quotes"])


def _tolerance(settings: Settings) -> Decimal:
    """总额校验容差（配置值 float → Decimal，避免二进制浮点误差）。"""
    return Decimal(str(settings.total_check_tolerance))


# ---- 字典（单一代码来源，前端展示值由此驱动）----


@router.get("/dictionaries", response_model=ApiResponse[DictionariesRead])
async def get_dictionaries() -> ApiResponse[DictionariesRead]:
    """标准险种/公司/保障包类型/服务类型/标注形式/优惠类型与状态标签。"""
    return ApiResponse.ok(DictionariesRead.model_validate(dictionary_service.build_dictionaries()))


# ---- 报价容器 ----


@router.post(
    "/projects/{project_id}/quotes",
    response_model=ApiResponse[QuoteRead],
    status_code=status.HTTP_201_CREATED,
)
async def create_quote(
    project_id: int,
    payload: QuoteCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[QuoteRead]:
    """创建报价容器：MANUAL 直接进入待确认；UPLOADED 只建 DRAFT 容器。"""
    quote = await quote_service.create_quote(db, project_id, payload)
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.get("/quotes/{quote_id}", response_model=ApiResponse[QuoteRead])
async def get_quote(
    quote_id: int,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[QuoteRead]:
    """报价完整结构化数据（各层明细、置信度、evidence、冲突信息）。"""
    quote = await quote_service.load_quote_full(db, quote_id)
    return ApiResponse.ok(quote_service.build_quote_read(quote))


@router.patch("/quotes/{quote_id}", response_model=ApiResponse[QuoteRead])
async def update_quote(
    quote_id: int,
    payload: QuoteUpdate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    """编辑基本信息与价格分项；价格变更后事务内重算。"""
    quote = await quote_service.update_quote(db, quote_id, payload, _tolerance(settings))
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.delete("/quotes/{quote_id}", response_model=ApiResponse[None])
async def delete_quote(
    quote_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[None]:
    """删除报价及全部明细；无引用文件资产随之清理（兄弟报价共享文件保留）。"""
    await quote_service.delete_quote(db, quote_id, settings)
    return ApiResponse.ok(message="报价已删除")


@router.post("/quotes/{quote_id}/confirm", response_model=ApiResponse[QuoteRead])
async def confirm_quote(
    quote_id: int,
    payload: QuoteConfirm,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    """确认报价：价格分项完整性校验 + 车辆摘要冲突二选一。"""
    quote = await quote_service.confirm_quote(db, quote_id, payload, _tolerance(settings))
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


# ---- 险种行 ----


@router.post(
    "/quotes/{quote_id}/coverages",
    response_model=ApiResponse[QuoteRead],
    status_code=status.HTTP_201_CREATED,
)
async def create_coverage(
    quote_id: int,
    payload: CoverageCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    """新增险种行（基础车险/附加险/未识别项统一入口）。"""
    quote = await quote_service.create_coverage(db, quote_id, payload, _tolerance(settings))
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.patch("/quotes/{quote_id}/coverages/{row_id}", response_model=ApiResponse[QuoteRead])
async def update_coverage(
    quote_id: int,
    row_id: int,
    payload: CoverageUpdate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    """编辑险种行；补标准码即完成未识别项的手动映射。"""
    quote = await quote_service.update_coverage(
        db, quote_id, row_id, payload, _tolerance(settings)
    )
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.delete("/quotes/{quote_id}/coverages/{row_id}", response_model=ApiResponse[QuoteRead])
async def delete_coverage(
    quote_id: int,
    row_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    """删除险种行（含未识别项的“丢弃”操作）。"""
    quote = await quote_service.delete_coverage(db, quote_id, row_id, _tolerance(settings))
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


# ---- 增值服务 ----


@router.post(
    "/quotes/{quote_id}/services",
    response_model=ApiResponse[QuoteRead],
    status_code=status.HTTP_201_CREATED,
)
async def create_service(
    quote_id: int,
    payload: ServiceCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    quote = await quote_service.create_service(db, quote_id, payload, _tolerance(settings))
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.patch("/quotes/{quote_id}/services/{row_id}", response_model=ApiResponse[QuoteRead])
async def update_service(
    quote_id: int,
    row_id: int,
    payload: ServiceUpdate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    quote = await quote_service.update_service(
        db, quote_id, row_id, payload, _tolerance(settings)
    )
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.delete("/quotes/{quote_id}/services/{row_id}", response_model=ApiResponse[QuoteRead])
async def delete_service(
    quote_id: int,
    row_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    quote = await quote_service.delete_service(db, quote_id, row_id, _tolerance(settings))
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


# ---- 独立保障包及内部保障 ----


@router.post(
    "/quotes/{quote_id}/packages",
    response_model=ApiResponse[QuoteRead],
    status_code=status.HTTP_201_CREATED,
)
async def create_package(
    quote_id: int,
    payload: PackageCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    """新增保障包（可携带内部保障列表，单事务创建）。"""
    quote = await quote_service.create_package(db, quote_id, payload, _tolerance(settings))
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.patch("/quotes/{quote_id}/packages/{package_id}", response_model=ApiResponse[QuoteRead])
async def update_package(
    quote_id: int,
    package_id: int,
    payload: PackageUpdate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    quote = await quote_service.update_package(
        db, quote_id, package_id, payload, _tolerance(settings)
    )
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.delete("/quotes/{quote_id}/packages/{package_id}", response_model=ApiResponse[QuoteRead])
async def delete_package(
    quote_id: int,
    package_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    quote = await quote_service.delete_package(
        db, quote_id, package_id, _tolerance(settings)
    )
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.post(
    "/quotes/{quote_id}/packages/{package_id}/coverages",
    response_model=ApiResponse[QuoteRead],
    status_code=status.HTTP_201_CREATED,
)
async def create_package_coverage(
    quote_id: int,
    package_id: int,
    payload: PackageCoverageCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    quote = await quote_service.create_package_coverage(
        db, quote_id, package_id, payload, _tolerance(settings)
    )
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.patch(
    "/quotes/{quote_id}/packages/{package_id}/coverages/{coverage_id}",
    response_model=ApiResponse[QuoteRead],
)
async def update_package_coverage(
    quote_id: int,
    package_id: int,
    coverage_id: int,
    payload: PackageCoverageUpdate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    quote = await quote_service.update_package_coverage(
        db, quote_id, package_id, coverage_id, payload, _tolerance(settings)
    )
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.delete(
    "/quotes/{quote_id}/packages/{package_id}/coverages/{coverage_id}",
    response_model=ApiResponse[QuoteRead],
)
async def delete_package_coverage(
    quote_id: int,
    package_id: int,
    coverage_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    quote = await quote_service.delete_package_coverage(
        db, quote_id, package_id, coverage_id, _tolerance(settings)
    )
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


# ---- 销售/用户标注 ----


@router.post(
    "/quotes/{quote_id}/annotations",
    response_model=ApiResponse[QuoteRead],
    status_code=status.HTTP_201_CREATED,
)
async def create_annotation(
    quote_id: int,
    payload: AnnotationCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    quote = await quote_service.create_annotation(db, quote_id, payload, _tolerance(settings))
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.patch("/quotes/{quote_id}/annotations/{row_id}", response_model=ApiResponse[QuoteRead])
async def update_annotation(
    quote_id: int,
    row_id: int,
    payload: AnnotationUpdate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    quote = await quote_service.update_annotation(
        db, quote_id, row_id, payload, _tolerance(settings)
    )
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.delete("/quotes/{quote_id}/annotations/{row_id}", response_model=ApiResponse[QuoteRead])
async def delete_annotation(
    quote_id: int,
    row_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    quote = await quote_service.delete_annotation(
        db, quote_id, row_id, _tolerance(settings)
    )
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


# ---- 优惠 ----


@router.post(
    "/quotes/{quote_id}/discounts",
    response_model=ApiResponse[QuoteRead],
    status_code=status.HTTP_201_CREATED,
)
async def create_discount(
    quote_id: int,
    payload: DiscountCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    """新增优惠；响应携带重算后的净支出。"""
    quote = await quote_service.create_discount(db, quote_id, payload, _tolerance(settings))
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.patch("/quotes/{quote_id}/discounts/{row_id}", response_model=ApiResponse[QuoteRead])
async def update_discount(
    quote_id: int,
    row_id: int,
    payload: DiscountUpdate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    """编辑优惠；响应携带重算后的净支出。"""
    quote = await quote_service.update_discount(
        db, quote_id, row_id, payload, _tolerance(settings)
    )
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))


@router.delete("/quotes/{quote_id}/discounts/{row_id}", response_model=ApiResponse[QuoteRead])
async def delete_discount(
    quote_id: int,
    row_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[QuoteRead]:
    quote = await quote_service.delete_discount(db, quote_id, row_id, _tolerance(settings))
    full = await quote_service.load_quote_full(db, quote.id)
    return ApiResponse.ok(quote_service.build_quote_read(full))
