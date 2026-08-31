"""多方案拆分与补传合并的请求/响应模型（SPEC §2.8、§2.9；TASK-05）。

契约约定：
- 拆分预览从已脱敏的 parse_task.rawResult 回放读取，逐方案给出价格分项
  与关键保障摘要；摘要值只是展示快照，真正的候选数据在拆分事务内由
  单方案写入逻辑重新构建；
- merge 变更的 oldValue/newValue 是 JSONB 直通结构（行快照或字段值），
  金额已序列化为 float；ACCEPT/KEEP 由用户逐项显式提交。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from app.models.enums import MergeChangeKind, MergeResolution, QuoteStatus
from app.schemas.common import CamelModel

# ---- 多方案拆分（SPEC §2.8）----


class PlanSplitPriceItem(CamelModel):
    """拆分预览中的单个价格分项摘要（来自脱敏 rawResult 的回放）。"""

    value: float | None = None
    status: str | None = None


class PlanSplitCoverageSummary(CamelModel):
    """拆分预览中的险种摘要行（只含确认方案差异所需的少量字段）。"""

    name: str
    status: str | None = None
    coverage_amount: float | None = None
    premium: float | None = None


class PlanSplitPlanPreview(CamelModel):
    """单个方案的拆分预览卡片数据。

    index 是该方案在 rawResult.plans 中的位置，拆分确认请求凭它回指；
    planLabel 默认取模型值，用户可在确认前改名。
    """

    index: int = Field(ge=0, description="方案在 rawResult.plans 中的下标")
    plan_label: str | None = Field(default=None, description="模型识别的方案标签")
    prices: dict[str, PlanSplitPriceItem]
    core_coverages: list[PlanSplitCoverageSummary] = Field(default_factory=list)
    additional_coverages: list[PlanSplitCoverageSummary] = Field(default_factory=list)
    # 保障包/服务的轻量摘要（“名称 保费元”文本），数量少、不参与结构化对比
    package_summaries: list[str] = Field(default_factory=list)
    service_summaries: list[str] = Field(default_factory=list)
    annotation_count: int = Field(default=0, description="销售标注条数（仅提示）")
    unmatched_count: int = Field(default=0, description="未识别项条数（含金额时阻断计算）")


class PlanSplitPreviewRead(CamelModel):
    """GET /quotes/{id}/plan-split 响应：多方案拆分确认视图数据。"""

    quote_id: int
    task_id: int = Field(description="提供 rawResult 的成功解析任务")
    plan_count: int
    insurer_name: str = Field(description="容器报价的公司显示名（拆分后子报价继承）")
    plans: list[PlanSplitPlanPreview]


class PlanSplitItemInput(CamelModel):
    """拆分确认中的单个保留方案：回指 rawResult 下标 + 用户可改的标签。"""

    index: int = Field(ge=0)
    plan_label: str | None = Field(
        default=None, max_length=50, description="用户改写后的方案标签；空则沿用模型值"
    )


class PlanSplitRequest(CamelModel):
    """POST /quotes/{id}/plan-split 请求：仅列出的方案会被保留创建子报价。"""

    plans: list[PlanSplitItemInput] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique_index(self) -> PlanSplitRequest:
        indexes = [item.index for item in self.plans]
        if len(indexes) != len(set(indexes)):
            raise ValueError("拆分方案下标重复")
        return self


class PlanSplitQuoteRead(CamelModel):
    """拆分创建出的子报价引用（前端据此跳转逐份确认）。"""

    id: int
    project_id: int
    insurer_code: str
    insurer_name: str
    agent_name: str | None = None
    plan_label: str | None = None
    status: QuoteStatus


class PlanSplitResultRead(CamelModel):
    """POST /quotes/{id}/plan-split 响应：子报价列表（容器报价已删除）。"""

    quotes: list[PlanSplitQuoteRead]


# ---- 补传合并（SPEC §2.9）----


class MergeChangeRead(CamelModel):
    """单条待确认变更。

    - kind=ADD：旧值不存在（oldValue 为 null），ACCEPT 表示插入新值；
    - kind=CONFLICT：同业务键新旧内容不同；同键多行时 fieldName 为
      __rows__（整组替换，不猜测逐行合并）；
    - userEdited 表示旧值/旧行被用户编辑过，此时 defaultResolution=KEEP
      （用户编辑永不静默覆盖），但仍展示变更由用户显式确认；
    - source* 是新值的证据定位（文件/页码/摘录），无合法证据时为空。
    """

    id: int
    entity_type: str = Field(description="scalar / coverage / unrecognized / service / package")
    entity_key: str
    entity_label: str = Field(description="中文展示名（价格分项名/险种名/服务名/包名）")
    field_name: str
    kind: MergeChangeKind
    old_value: Any | None = None
    new_value: Any | None = None
    source_file_id: int | None = None
    source_page: int | None = None
    source_text: str | None = None
    user_edited: bool
    resolution: MergeResolution
    default_resolution: Literal["ACCEPT", "KEEP"]


class MergePreviewRead(CamelModel):
    """GET /quotes/{id}/merge-preview 响应：待确认变更清单。"""

    quote_id: int
    quote_status: QuoteStatus
    task_id: int = Field(description="产生本批变更的解析任务")
    changes: list[MergeChangeRead]
    pending_count: int


class MergeResolveItem(CamelModel):
    """单条变更的裁决：ACCEPT 采纳新值 / KEEP 保留旧值。"""

    change_id: int
    resolution: Literal[MergeResolution.ACCEPT, MergeResolution.KEEP]


class MergeResolveRequest(CamelModel):
    """POST /quotes/{id}/merge-resolve 请求：必须覆盖全部待确认变更。"""

    resolutions: list[MergeResolveItem] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique_change(self) -> MergeResolveRequest:
        ids = [item.change_id for item in self.resolutions]
        if len(ids) != len(set(ids)):
            raise ValueError("变更裁决存在重复项")
        return self
