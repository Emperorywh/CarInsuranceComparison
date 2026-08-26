"""统一响应包 {code, message, data}。

所有接口（含错误）都返回该结构；OpenAPI 以 response_model 中的
ApiResponse[...] 为唯一契约，前端类型由其生成，不手写第二份。
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from app.core.privacy import sanitize_text

T = TypeVar("T")

# 成功业务码；错误码见 errors.py 各异常类
OK_CODE = "OK"


class ApiResponse(BaseModel, Generic[T]):
    """统一响应包。data 为业务载荷，出错时为 null。"""

    model_config = ConfigDict(populate_by_name=True)

    code: str
    message: str
    data: T | None = None

    @classmethod
    def ok(cls, data: T | None = None, message: str = "ok") -> ApiResponse[T]:
        return cls(code=OK_CODE, message=message, data=data)

    @classmethod
    def error(cls, code: str, message: str, data: T | None = None) -> ApiResponse[T]:
        # 错误文案会携带用户输入（如字段名+原因），落响应前统一脱敏
        return cls(code=code, message=sanitize_text(message), data=data)
