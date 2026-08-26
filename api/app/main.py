"""FastAPI ASGI 入口。

职责：配置加载与启动期安全校验、CORS、访问令牌中间件、
统一异常处理、路由注册与安全日志。业务逻辑一律在 routes/services 中。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.core.responses import ApiResponse
from app.core.security import TOKEN_HEADER, AccessTokenMiddleware

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建应用实例；settings 注入供测试覆盖配置。"""
    settings = settings or get_settings()
    # 启动期安全校验（非回环绑定且无令牌时抛异常 -> 进程拒绝启动）
    settings.validate_security()

    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 后续 Task 在此接入：遗留 RUNNING 解析任务恢复、worker 启停等
        yield

    app = FastAPI(
        title="车险报价对比助手 API",
        version="0.1.0",
        description="MVP 后端接口；FastAPI 自动生成的 OpenAPI 是唯一请求/响应契约。",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # 中间件顺序：后添加者在外层。令牌校验在内、CORS 在外，
    # 保证 401 响应也带 CORS 头，浏览器端才能统一捕获处理
    app.add_middleware(AccessTokenMiddleware, token=settings.local_access_token.strip())
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["Content-Type", TOKEN_HEADER],
    )

    app.include_router(api_router)

    _register_exception_handlers(app)

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse.error(exc.code, exc.message).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 422 参数校验错误：取第一条错误拼中文提示；错误详情可能包含用户输入，统一脱敏
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first.get("loc", []) if part != "body")
        message = f"参数校验失败：{loc} {first.get('msg', '不合法')}".strip()
        return JSONResponse(
            status_code=422,
            content=ApiResponse.error("VALIDATION_ERROR", message).model_dump(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse.error(
                f"HTTP_{exc.status_code}", str(exc.detail) or "请求失败"
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # 未预期异常：日志记录（经安全过滤器脱敏），对外不泄露堆栈细节
        logger.exception("未处理异常 path=%s", request.url.path)
        return JSONResponse(
            status_code=500,
            content=ApiResponse.error("INTERNAL_ERROR", "服务器内部错误，请稍后重试").model_dump(),
        )


# uvicorn app.main:app 引用的模块级实例（读取当前环境配置）
app = create_app()
