"""统一业务错误与错误码。

约定（SPEC §10）：
- HTTP 状态码表达传输层语义（401/404/409/422 …）；
- 响应包内 code 表达业务错误码（语义化字符串），供前端精准提示；
- 所有对外 message 为简体中文，写入前经统一脱敏。
"""

from __future__ import annotations


class AppError(Exception):
    """业务错误基类：由全局异常处理器转换为统一响应包。"""

    status_code: int = 400
    code: str = "BAD_REQUEST"
    message: str = "请求处理失败"

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        if code:
            self.code = code


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "资源不存在"


class ProjectNotFoundError(NotFoundError):
    code = "PROJECT_NOT_FOUND"
    message = "项目不存在或已被删除"


class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"
    message = "请求参数不合法"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"
    message = "缺少或错误的访问令牌，请先在页面中输入访问令牌"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "请求与当前状态冲突"
