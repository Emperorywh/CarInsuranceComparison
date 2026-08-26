"""项目相关请求/响应模型（SPEC §2.1、§10）。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from app.schemas.common import CamelModel


class ProjectCreate(CamelModel):
    """创建项目：字段严格限定为项目名、车辆名称、续保年份、可选到期日与备注。"""

    name: str = Field(min_length=1, max_length=100, description="项目名称，如“2026 车辆续保”")
    vehicle_name: str = Field(min_length=1, max_length=100, description="车辆名称，如 Model Y")
    renewal_year: int = Field(ge=2000, le=2100, description="续保年份")
    expire_date: date | None = Field(default=None, description="保险到期时间（可选）")
    note: str | None = Field(default=None, max_length=2000, description="备注（可选，入库前脱敏）")


class ProjectUpdate(CamelModel):
    """编辑项目：全部字段可选，仅更新显式提供的字段（PATCH 语义）。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    vehicle_name: str | None = Field(default=None, min_length=1, max_length=100)
    renewal_year: int | None = Field(default=None, ge=2000, le=2100)
    expire_date: date | None = None
    note: str | None = Field(default=None, max_length=2000)


class ProjectRead(CamelModel):
    """项目详情（含车辆摘要字段；摘要由首份确认报价回填，当前 MVP 阶段为空）。"""

    id: int
    name: str
    vehicle_name: str
    renewal_year: int
    expire_date: date | None = None
    note: str | None = None
    vehicle_model: str | None = None
    vehicle_seats: int | None = None
    first_reg_date: str | None = None
    is_nev: bool | None = None
    model_consent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProjectListItem(ProjectRead):
    """项目列表项：附带报价数与最低有效净支出聚合。

    “有效净支出”只统计已确认（CONFIRMED/MERGE_REVIEW）且 netPayment 非空的报价；
    无有效报价时 minNetPayment 为 null（稳定空状态，前端不得当作 0 处理）。
    """

    quote_count: int
    # 金额对外统一 float（两位小数内无精度损失）；Pydantic v2 会把 Decimal
    # 序列化成字符串，不适合前端直接比较大小，故在此显式收窄
    min_net_payment: float | None = None
