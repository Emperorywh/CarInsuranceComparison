"""Schema 公共基类：数据库 snake_case 与对外 JSON camelCase 的唯一转换点。"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """字段名按 to_camel 生成别名：ORM 属性 snake_case，接口 JSON camelCase。

    serialize_by_alias 保证响应输出 camelCase；populate_by_name 允许
    内部代码用 snake_case 构造（如聚合查询结果）。
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        from_attributes=True,
    )


def _decimal_as_float(value: Decimal) -> float:
    """金额序列化统一输出 JSON number。

    TASK-01 决策：Pydantic v2 默认把 Decimal 序列化成字符串，不适合
    前端比较大小；两位小数内 float 无精度损失，故所有金额响应一律 float。
    """
    return float(value)


# ---- 金额请求类型（SPEC §10：非负、最多两位小数，位数与数据库 numeric 对齐）----
# decimal_places 拒绝超过两位的小数；ge=0 由数据库 CheckConstraint 二次兜底；
# PlainSerializer 保证复用这些注解的读模型也输出 float 而非字符串
Amount12 = Annotated[
    Decimal,
    PlainSerializer(_decimal_as_float, return_type=float),
    Field(ge=0, max_digits=12, decimal_places=2),
]
Amount14 = Annotated[
    Decimal,
    PlainSerializer(_decimal_as_float, return_type=float),
    Field(ge=0, max_digits=14, decimal_places=2),
]
Amount6 = Annotated[
    Decimal,
    PlainSerializer(_decimal_as_float, return_type=float),
    Field(ge=0, max_digits=6, decimal_places=2),
]
