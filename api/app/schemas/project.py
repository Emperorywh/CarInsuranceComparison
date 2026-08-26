"""项目相关请求/响应模型（SPEC §2.1、§10）。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from app.models.enums import (
    NetPaymentStatus,
    QuoteSource,
    QuoteStatus,
    TotalCheckStatus,
)
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


class QuoteCardSummary(CamelModel):
    """项目详情页报价卡片摘要（分组内的单张卡）。

    净支出为 null 时按 netPaymentStatus 区分“总价缺失/优惠超额”，
    前端不得把 null 当 0；totalCheckStatus=MISMATCH 必须展示异常提示。
    """

    id: int
    insurer_code: str
    insurer_name: str
    agent_name: str | None = None
    plan_label: str | None = None
    source: QuoteSource
    status: QuoteStatus
    net_payment: float | None = None
    net_payment_status: NetPaymentStatus
    official_total: float | None = None
    computed_total: float | None = None
    total_check_status: TotalCheckStatus
    # 三者险与三者医保外摘要：取已包含行中的最大保额，无则 null
    third_party_amount: float | None = None
    tp_non_medical_amount: float | None = None
    created_at: datetime


class QuoteGroup(CamelModel):
    """按“保险公司 + 保险员”分组的报价卡组（SPEC §8 / 决策 #9）。

    组内多于一份报价时 sameSourceHint=true：仅提示“同来源报价”，
    不创建版本链（MVP 无 QuoteVersion）。quotes 无默认（构造时必填）。
    """

    insurer_code: str
    insurer_name: str
    agent_name: str | None = None
    same_source_hint: bool
    quotes: list[QuoteCardSummary]


class ProjectDetail(ProjectRead):
    """项目详情：在基础字段之上附带分组报价卡数据（必填集合）。"""

    quote_groups: list[QuoteGroup]
