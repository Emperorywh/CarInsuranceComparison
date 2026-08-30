"""模型提取输出的 Pydantic Schema（SPEC §4.1；TASK-04 范围 2）。

单一来源约定：
- 本模块是 §4.1 输出 Schema 的唯一实现：提示词中的 JSON Schema 由
  ``ExtractionResult.model_json_schema()`` 生成，模型返回值也由同一组
  模型校验，禁止维护第二份手写 Schema；
- §4.1 定义的全部键一律**必填**（值允许 null / UNKNOWN，不设默认值）——
  “所有定义字段都必须返回，不允许直接省略键；所有定义键缺失都判失败”
  由必填约束天然表达，缺失键抛 ValidationError 后按可重试处理；
- ``planCount != len(plans)`` 判 Schema 校验失败（SPEC §4.1 要点），
  上层同样按可重试处理（重试可能产出一致结果）；
- ``plans[].insurerName`` 是 TASK-04 的实现决策：§4.1 顶层只有一个
  insurer，而“一个批次含不同保险公司必须以明确错误停止”（TASKS.md
  范围 9）需要逐方案的公司信息。该键为可选（缺失不判失败），仅用于
  混合公司检测，不参与业务落库。

职责边界：本模块只做结构与取值域校验，不做业务映射（归一化、置信度、
落库全部在流水线后续阶段）；``extra`` 默认 ignore，容忍模型偶发的多余
说明性字段，不因注释性键整单失败。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

# ---- 取值域（与 SPEC §4.1 要点一致）----

ITEM_STATUS_VALUES = ("INCLUDED", "NOT_INCLUDED", "FREE", "NOT_APPLICABLE", "UNKNOWN")
PRICE_STATUS_VALUES = ("INCLUDED", "NOT_INCLUDED", "UNKNOWN")
OFFICIAL_TOTAL_STATUS_VALUES = ("INCLUDED", "UNKNOWN")


class EvidenceExtraction(BaseModel):
    """字段定位三元组（决策 #11）：fileKey 由后端在请求前分配。

    模型不得编造占位 fileKey/页码；无来源时整体为 null（合法值），
    后端按“无 evidence → MEDIUM”处理。
    """

    fileKey: str | None = Field(default=None, max_length=10)
    page: int | None = Field(default=None, ge=1)
    text: str | None = Field(default=None, max_length=500)


class _SelfConfident(BaseModel):
    """带模型自报置信度与证据的公共部分；两键必填（值可空）。"""

    selfConfidence: float | None = Field(default=None, ge=0, le=1)
    evidence: EvidenceExtraction | None


class InsurerExtraction(BaseModel):
    """保险公司识别结果（仅名称；码映射由后端归一化引擎完成）。"""

    name: str | None = Field(default=None, max_length=100)
    selfConfidence: float | None = Field(default=None, ge=0, le=1)
    evidence: EvidenceExtraction | None


class VehicleTextField(_SelfConfident):
    """车型（白名单字段：只允许车型，不含车牌/VIN/发动机号）。"""

    value: str | None = Field(default=None, max_length=100)
    rawValue: str | None = Field(default=None, max_length=200)


class VehicleIntField(_SelfConfident):
    value: int | None = Field(default=None, ge=1, le=99)
    rawValue: str | None = Field(default=None, max_length=200)


class VehicleDateField(_SelfConfident):
    """初登日期：月精度文本（如 2022-05），格式校验在归一化阶段做。"""

    value: str | None = Field(default=None, max_length=20)
    rawValue: str | None = Field(default=None, max_length=200)


class VehicleBoolField(_SelfConfident):
    value: bool | None
    rawValue: str | None = Field(default=None, max_length=200)


class VehicleExtraction(BaseModel):
    """车辆白名单四字段（SPEC §9.2：最小返回，不采集其他车辆信息）。"""

    model: VehicleTextField
    seatCount: VehicleIntField
    firstRegDate: VehicleDateField
    isNev: VehicleBoolField


class PriceItemExtraction(_SelfConfident):
    """价格分项五元组：{value, rawValue, status, selfConfidence, evidence}。"""

    value: float | None = Field(default=None, ge=0)
    rawValue: str | None = Field(default=None, max_length=200)
    status: str | None = None

    @model_validator(mode="after")
    def _check_status(self) -> PriceItemExtraction:
        # 状态键必须返回；允许 null（无法判断），但出现非法取值判失败
        if self.status is not None and self.status not in PRICE_STATUS_VALUES:
            raise ValueError(f"价格分项 status 非法：{self.status}")
        return self


class OfficialTotalExtraction(_SelfConfident):
    value: float | None = Field(default=None, ge=0)
    rawValue: str | None = Field(default=None, max_length=200)
    status: str | None = None

    @model_validator(mode="after")
    def _check_status(self) -> OfficialTotalExtraction:
        if self.status is not None and self.status not in OFFICIAL_TOTAL_STATUS_VALUES:
            raise ValueError(f"officialTotal status 非法：{self.status}")
        return self


class PricingExtraction(BaseModel):
    """六个价格分项：键全部必填（缺失判 Schema 失败）。"""

    commercialPremium: PriceItemExtraction
    compulsoryPremium: PriceItemExtraction
    vehicleTax: PriceItemExtraction
    packageTotal: PriceItemExtraction
    otherFees: PriceItemExtraction
    officialTotal: OfficialTotalExtraction


class CoverageItemExtraction(_SelfConfident):
    """险种行（核心/附加险共用）：状态允许五态。"""

    rawName: str = Field(min_length=1, max_length=200)
    rawValue: str | None = Field(default=None, max_length=500)
    status: str | None = None
    coverageAmount: float | None = Field(default=None, ge=0)
    premium: float | None = Field(default=None, ge=0)
    perSeatAmount: float | None = Field(default=None, ge=0)
    seatCount: int | None = Field(default=None, ge=1, le=99)
    sharedCoverage: bool | None
    multiplier: float | None = Field(default=None, ge=0)
    condition: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check_status(self) -> CoverageItemExtraction:
        if self.status is not None and self.status not in ITEM_STATUS_VALUES:
            raise ValueError(f"险种行 status 非法：{self.status}")
        return self


class ServiceItemExtraction(_SelfConfident):
    rawName: str = Field(min_length=1, max_length=200)
    rawValue: str | None = Field(default=None, max_length=500)
    status: str | None = None
    count: int | None = Field(default=None, ge=0, le=9999)
    cost: float | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check_status(self) -> ServiceItemExtraction:
        if self.status is not None and self.status not in ITEM_STATUS_VALUES:
            raise ValueError(f"服务行 status 非法：{self.status}")
        return self


class PackageCoverageExtraction(_SelfConfident):
    """保障包内部保障：type 只接受 §3.3 码表，非法值由流水线统一改 OTHER。"""

    rawText: str | None = Field(default=None, max_length=500)
    type: str | None = Field(default=None, max_length=50)
    name: str | None = Field(default=None, max_length=200)
    status: str | None = None
    coverageAmount: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=20)
    perSeatAmount: float | None = Field(default=None, ge=0)
    seatCount: int | None = Field(default=None, ge=1, le=99)
    shared: bool | None
    multiplier: float | None = Field(default=None, ge=0)
    condition: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check_status(self) -> PackageCoverageExtraction:
        if self.status is not None and self.status not in ITEM_STATUS_VALUES:
            raise ValueError(f"保障包内部保障 status 非法：{self.status}")
        return self


class SupplementalPackageExtraction(_SelfConfident):
    name: str = Field(min_length=1, max_length=200)
    rawName: str | None = Field(default=None, max_length=200)
    rawValue: str | None = Field(default=None, max_length=500)
    premium: float | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=2000)
    coverages: list[PackageCoverageExtraction]


class AnnotationExtraction(_SelfConfident):
    """销售/手写标注：与打印正文严格隔离，只进 annotations 不参与计算。"""

    content: str = Field(min_length=1, max_length=500)
    kind: str | None = Field(default=None, max_length=30)


class UnmatchedItemExtraction(_SelfConfident):
    """无法归类的金额/文本项：必须挂在所属 plan 内（避免多方案归属漂移）。"""

    rawText: str = Field(min_length=1, max_length=500)
    reason: str | None = Field(default=None, max_length=200)


class PlanExtraction(BaseModel):
    """单方案抽取结果；unmatchedItems 固定在 plan 内（SPEC §4.1 要点）。"""

    planLabel: str | None = Field(default=None, max_length=50)
    # TASK-04 实现决策：可选逐方案公司名，仅用于混合公司批次检测
    insurerName: str | None = Field(default=None, max_length=100)
    pricing: PricingExtraction
    coreCoverages: list[CoverageItemExtraction]
    additionalCoverages: list[CoverageItemExtraction]
    services: list[ServiceItemExtraction]
    supplementalPackages: list[SupplementalPackageExtraction]
    annotations: list[AnnotationExtraction]
    unmatchedItems: list[UnmatchedItemExtraction]


class ExtractionResult(BaseModel):
    """§4.1 顶层结构：insurer + vehicle + planCount + plans。"""

    insurer: InsurerExtraction
    vehicle: VehicleExtraction
    planCount: int = Field(ge=0)
    plans: list[PlanExtraction]

    @model_validator(mode="after")
    def _check_plan_count(self) -> ExtractionResult:
        # SPEC §4.1：planCount 必须等于 plans.length，不一致即 Schema 失败
        if self.planCount != len(self.plans):
            raise ValueError(
                f"planCount({self.planCount}) 与 plans 长度({len(self.plans)})不一致"
            )
        return self


def extraction_json_schema() -> dict:
    """生成提示词携带的 JSON Schema（与校验同源，禁止手写第二份）。"""
    return ExtractionResult.model_json_schema()


def parse_extraction(payload: str | bytes | dict) -> ExtractionResult:
    """校验模型返回值；任何结构/取值域问题抛 pydantic.ValidationError。

    上层（provider）负责把 ValidationError 归类为可重试失败。
    """
    if isinstance(payload, dict):
        return ExtractionResult.model_validate(payload)
    return ExtractionResult.model_validate_json(payload)
