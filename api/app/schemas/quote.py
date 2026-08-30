"""报价相关请求/响应模型（SPEC §2.2、§2.5–§2.7、§10）。

契约约定：
- 金额请求值一律非负、最多两位小数（Amount12/14/6）；
- 金额响应值一律 float（TASK-01 决策：避免 Decimal 序列化为字符串）；
- 价格分项“值 + 状态”成对出现：值非空一律视为 INCLUDED，
  值为空时状态只能是 NOT_INCLUDED / UNKNOWN，系统绝不把 null 当 0。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.enums import (
    AnnotationKind,
    AnnotationSourceType,
    ConfidenceLevel,
    CoverageCategory,
    DiscountType,
    ItemStatus,
    NetPaymentStatus,
    OfficialTotalStatus,
    PackageUnit,
    PriceItemStatus,
    QuoteSource,
    QuoteStatus,
    ServiceType,
    TotalCheckStatus,
)
from app.schemas.common import Amount6, Amount12, Amount14, CamelModel
from app.schemas.file import FileRead

# 初登日期为月精度（如 2022-05），只接受 YYYY-MM 文本
_FIRST_REG_DATE_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


# ---- 报价容器 ----


class QuoteCreate(CamelModel):
    """创建报价容器：预置公司存预置码，其他公司固定 OTHER 并必填公司名。"""

    insurer_code: str = Field(min_length=1, max_length=50, description="保险公司标准码")
    insurer_name: str | None = Field(
        default=None, min_length=1, max_length=100, description="其他公司的自由输入名称"
    )
    agent_name: str | None = Field(default=None, max_length=50, description="保险员称呼（可选）")
    source: QuoteSource = Field(
        description="MANUAL 创建即 PENDING_CONFIRM；UPLOADED 只建 DRAFT 容器"
    )


class VehicleConflictInfo(CamelModel):
    """报价车辆快照与项目车辆摘要的对比结果（SPEC §6.10）。

    fields 非空时确认必须显式二选一；初登日期差异只提示、不阻断。
    构造时总是显式传值（无默认），保证 OpenAPI/前端类型为必填。
    """

    fields: list[str] = Field(description="冲突字段名")
    first_reg_date_differs: bool
    resolution_required: bool


# ---- 险种行（基础车险 + 附加险 + 未识别项）----


class CoverageBase(CamelModel):
    """险种行可写字段；category 一律由标准码推导，不单独提交。

    手动新增行默认 INCLUDED（用户添加即视为投保；SPEC §6.6 状态语义
    由解析流水线与显式选择共同维护），Update 覆写为可空避免未提供时重置。
    """

    status: ItemStatus = ItemStatus.INCLUDED
    coverage_amount: Amount14 | None = None
    per_seat_amount: Amount14 | None = None
    seat_count: int | None = Field(default=None, gt=0, le=99)
    shared_coverage: bool | None = None
    premium: Amount12 | None = None
    multiplier: Amount6 | None = None
    condition: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class CoverageCreate(CoverageBase):
    """新增险种行：传标准码即归类（category 随码），不传码进入未识别区。

    手动映射未识别项 = PATCH 该行补标准码；交强险不允许作为险种行录入。
    """

    code: str | None = Field(
        default=None, max_length=50, description="标准险种码；不传则记为未识别"
    )
    raw_name: str = Field(min_length=1, max_length=200, description="原始/自定义名称")


class CoverageUpdate(CoverageBase):
    """编辑险种行：补码即完成未识别项映射，显式传 code=null 退回未识别。

    覆写基类带默认值的枚举字段为可空，避免“未提供”被误当成“重置为默认”。
    """

    code: str | None = None
    raw_name: str | None = Field(default=None, min_length=1, max_length=200)
    status: ItemStatus | None = None


class CoverageRead(CoverageBase):
    id: int
    category: CoverageCategory
    code: str | None = None
    raw_name: str
    raw_value: str | None = None
    name: str
    confidence_level: ConfidenceLevel
    source_file_id: int | None = None
    source_page: int | None = None
    source_text: str | None = None
    edited_by_user: bool
    # 保额超出常见档位时的核对提示（SPEC §6.7，仅提示不阻断）
    amount_range_hint: str | None = None


# ---- 增值服务 ----


class ServiceBase(CamelModel):
    """增值服务行字段；手动新增默认 INCLUDED（费用为 0 时应显式选 FREE）。"""

    service_type: ServiceType = ServiceType.OTHER
    status: ItemStatus = ItemStatus.INCLUDED
    count: int | None = Field(default=None, ge=0, le=9999)
    cost: Amount12 | None = None
    description: str | None = Field(default=None, max_length=2000)


class ServiceCreate(ServiceBase):
    """新增增值服务行。只有明确 0 元费用才应填 FREE（状态语义见 SPEC §6.6）。"""


class ServiceUpdate(ServiceBase):
    """编辑增值服务行：全部字段可选，仅更新显式提供的字段。"""

    service_type: ServiceType | None = None
    status: ItemStatus | None = None


class ServiceRead(ServiceBase):
    id: int
    raw_name: str | None = None
    raw_value: str | None = None
    confidence_level: ConfidenceLevel
    source_file_id: int | None = None
    source_page: int | None = None
    source_text: str | None = None
    edited_by_user: bool


# ---- 独立保障包及内部保障 ----


class PackageCoverageBase(CamelModel):
    """保障包内部保障字段；手动新增默认 INCLUDED。"""

    name: str | None = Field(default=None, max_length=200)
    status: ItemStatus = ItemStatus.INCLUDED
    coverage_amount: Amount14 | None = None
    unit: PackageUnit | None = None
    per_seat_amount: Amount14 | None = None
    seat_count: int | None = Field(default=None, gt=0, le=99)
    shared: bool | None = None
    multiplier: Amount6 | None = None
    condition: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    raw_text: str | None = Field(default=None, max_length=500)


class PackageCoverageCreate(PackageCoverageBase):
    """保障包内部保障：type 只接受 §3.3 码表，未知类型一律 OTHER。"""

    type: str = Field(max_length=50, description="保障类型码（SPEC §3.3）")


class PackageCoverageUpdate(PackageCoverageBase):
    """编辑保障包内部保障；覆写枚举默认值避免未提供字段被重置。"""

    type: str | None = None
    status: ItemStatus | None = None


class PackageCoverageRead(PackageCoverageBase):
    id: int
    type: str
    confidence_level: ConfidenceLevel
    source_file_id: int | None = None
    source_page: int | None = None
    source_text: str | None = None
    edited_by_user: bool


class PackageBase(CamelModel):
    provider: str | None = Field(default=None, max_length=100)
    premium: Amount12 | None = None
    description: str | None = Field(default=None, max_length=2000)


class PackageCreate(PackageBase):
    """新增保障包（含可选的内部保障列表，单事务创建）。"""

    name: str = Field(min_length=1, max_length=200)
    coverages: list[PackageCoverageCreate] = Field(default_factory=list)


class PackageUpdate(PackageBase):
    name: str | None = Field(default=None, min_length=1, max_length=200)


class PackageRead(PackageBase):
    id: int
    name: str
    raw_name: str | None = None
    raw_value: str | None = None
    confidence_level: ConfidenceLevel
    source_file_id: int | None = None
    source_page: int | None = None
    source_text: str | None = None
    edited_by_user: bool
    # 无默认：构造时总是显式传入，保证契约中为必填字段
    coverages: list[PackageCoverageRead]


# ---- 销售/用户标注 ----


class AnnotationBase(CamelModel):
    kind: AnnotationKind = AnnotationKind.OTHER


class AnnotationCreate(AnnotationBase):
    """新增标注。手动录入默认为用户标注；销售标注由解析流水线（TASK-04）写入。"""

    content: str = Field(min_length=1, max_length=2000)
    source_type: AnnotationSourceType = AnnotationSourceType.USER_ANNOTATION


class AnnotationUpdate(AnnotationBase):
    """编辑标注；kind 未提供时不重置。"""

    kind: AnnotationKind | None = None
    content: str | None = Field(default=None, min_length=1, max_length=2000)


class AnnotationRead(AnnotationBase):
    id: int
    content: str
    source_type: AnnotationSourceType
    source_file_id: int | None = None
    source_page: int | None = None
    edited_by_user: bool


# ---- 优惠 ----


class DiscountBase(CamelModel):
    discount_type: DiscountType
    description: str | None = Field(default=None, max_length=200)
    amount: Amount12 | None = Field(default=None, description="名义金额，仅展示不参与净支出")
    cash_equivalent: Amount12 | None = Field(
        default=None, description="折现估值；为空则该优惠不减钱（SERVICE 默认为空）"
    )
    include_in_net: bool = Field(default=False, description="是否计入净支出")


class DiscountCreate(DiscountBase):
    """新增优惠：SERVICE 类默认无折现值，不自动折现（PRD §26）。"""


class DiscountUpdate(CamelModel):
    """编辑优惠：全部字段可选，仅更新显式提供的字段。"""

    discount_type: DiscountType | None = None
    description: str | None = Field(default=None, max_length=200)
    amount: Amount12 | None = None
    cash_equivalent: Amount12 | None = None
    include_in_net: bool | None = None


class DiscountRead(DiscountBase):
    id: int


# ---- 字段来源（标量字段 evidence，SPEC §2.7）----


class FieldEvidenceRead(CamelModel):
    """报价标量字段（价格/公司/车辆信息）的来源与用户编辑标记。"""

    id: int
    field_name: str
    raw_value: str | None = None
    source_file_id: int | None = None
    source_page: int | None = None
    source_text: str | None = None
    confidence_level: ConfidenceLevel
    edited_by_user: bool


# ---- 报价完整读模型（依赖以上各层读模型，必须放在文件末尾定义）----


class QuoteRead(CamelModel):
    """报价完整结构化数据（各层明细、置信度、evidence 与冲突信息）。"""

    id: int
    project_id: int
    insurer_code: str
    insurer_name: str
    agent_name: str | None = None
    plan_label: str | None = None
    source: QuoteSource
    status: QuoteStatus
    note: str | None = None

    vehicle_model: str | None = None
    vehicle_seats: int | None = None
    first_reg_date: str | None = None
    is_nev: bool | None = None

    commercial_premium: float | None = None
    computed_commercial_premium: float | None = None
    commercial_status: PriceItemStatus
    compulsory_premium: float | None = None
    compulsory_status: PriceItemStatus
    vehicle_tax: float | None = None
    vehicle_tax_status: PriceItemStatus
    package_total: float | None = None
    computed_package_total: float | None = None
    package_status: PriceItemStatus
    other_fees: float | None = None
    other_fees_status: PriceItemStatus
    official_total: float | None = None
    official_total_status: OfficialTotalStatus
    computed_total: float | None = None
    total_check_status: TotalCheckStatus
    net_payment: float | None = None
    net_payment_status: NetPaymentStatus

    vehicle_conflict: VehicleConflictInfo | None = Field(
        default=None,
        description="车辆冲突信息；model_copy 组装时填充，前端按可空处理",
    )
    # 报价关联文件（按 sortOrder）；UPLOADED 报价才有，手动报价恒为空数组
    files: list[FileRead]
    # 各层明细无默认：build_quote_read 组装时总是显式传入，
    # 保证 OpenAPI/前端类型为必填集合
    coverages: list[CoverageRead]
    services: list[ServiceRead]
    packages: list[PackageRead]
    annotations: list[AnnotationRead]
    discounts: list[DiscountRead]
    evidences: list[FieldEvidenceRead]

    created_at: datetime
    updated_at: datetime


class QuoteUpdate(CamelModel):
    """编辑报价基本信息与价格分项（PATCH 语义：只应用显式提供的字段）。

    价格分项规则：提供非空金额 → 该分项状态置 INCLUDED；
    提供空金额 + NOT_INCLUDED/UNKNOWN → 按提供状态落库（金额清空）。
    任何组合都不允许出现“INCLUDED 但无金额”的落库结果。
    """

    agent_name: str | None = Field(default=None, max_length=50)
    plan_label: str | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=2000)
    vehicle_model: str | None = Field(default=None, max_length=100)
    vehicle_seats: int | None = Field(default=None, gt=0, le=99)
    first_reg_date: str | None = Field(
        default=None,
        pattern=_FIRST_REG_DATE_PATTERN,
        description="初登月份 YYYY-MM，可显式传 null 清空",
    )
    is_nev: bool | None = None

    commercial_premium: Amount12 | None = None
    commercial_status: PriceItemStatus | None = None
    compulsory_premium: Amount12 | None = None
    compulsory_status: PriceItemStatus | None = None
    vehicle_tax: Amount12 | None = None
    vehicle_tax_status: PriceItemStatus | None = None
    package_total: Amount12 | None = None
    package_status: PriceItemStatus | None = None
    other_fees: Amount12 | None = None
    other_fees_status: PriceItemStatus | None = None
    official_total: Amount12 | None = None


class QuoteConfirm(CamelModel):
    """确认报价（手动/单方案）。

    车辆信息与项目摘要冲突时必须显式二选一：以报价为准（回填项目摘要）
    或以项目为准（保留摘要，报价快照不变）。无冲突时可省略。
    """

    vehicle_conflict_resolution: Literal["USE_QUOTE", "KEEP_PROJECT"] | None = None


# ---- 字典端点 ----


class DictionaryOption(CamelModel):
    code: str
    label: str


class CoverageDictionaryOption(DictionaryOption):
    """险种字典项：COMPULSORY 类别只允许出现在价格分项，不可作为险种行。"""

    category: str
    row_selectable: bool


class DictionariesRead(CamelModel):
    """字典端点响应：全部字段由 build_dictionaries 显式提供（必填）。"""

    insurers: list[DictionaryOption]
    coverage_codes: list[CoverageDictionaryOption]
    package_coverage_types: list[DictionaryOption]
    service_types: list[DictionaryOption]
    annotation_kinds: list[DictionaryOption]
    discount_types: list[DictionaryOption]
    package_units: list[DictionaryOption]
    status_labels: dict[str, dict[str, str]]
