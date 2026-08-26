"""本地访问令牌中间件（SPEC §9.4）。

- LOCAL_ACCESS_TOKEN 非空即启用（与绑定地址无关）；
- 除健康检查 /health 外，全部 API 路径（含原文件与 OpenAPI/交互文档）
  都必须携带匹配的 X-Access-Token 请求头，否则 401；
- 比较使用恒定时间函数，避免计时侧信道；
- 令牌只出现在请求头，绝不进入 URL/查询串/日志。
"""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.responses import ApiResponse

# 唯一豁免路径：健康检查需要被探活匿名访问
EXEMPT_PATHS = frozenset({"/health"})

TOKEN_HEADER = "X-Access-Token"


class AccessTokenMiddleware(BaseHTTPMiddleware):
    """校验 X-Access-Token；未启用令牌（token 为空）时直接放行。"""

    def __init__(self, app, token: str) -> None:  # noqa: ANN001 - ASGI 应用类型
        super().__init__(app)
        self._token = token

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if self._token and request.url.path not in EXEMPT_PATHS:
            supplied = request.headers.get(TOKEN_HEADER, "")
            if not hmac.compare_digest(supplied.encode("utf-8"), self._token.encode("utf-8")):
                body = ApiResponse.error(
                    "UNAUTHORIZED", "缺少或错误的访问令牌，请先在页面中输入访问令牌"
                ).model_dump()
                return JSONResponse(status_code=401, content=body)
        return await call_next(request)
