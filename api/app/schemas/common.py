"""Schema 公共基类：数据库 snake_case 与对外 JSON camelCase 的唯一转换点。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
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
