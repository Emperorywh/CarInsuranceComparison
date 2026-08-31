"""对比结果契约（TASK-06，SPEC §7）。

设计约定：
- 服务端只产出结构化数据与展示文本，不产出 AI 文案；五问文字全部由
  确定性规则基于结构化字段生成，测试逐字段断言（不允许模糊快照）；
- `CompareCell.value` 携带结构化值（金额 float / 文本 str / 布尔），
  `text` 为服务端格式化好的展示文本（前端直接渲染，导出长图复用）；
- 差异标签相对“用户勾选顺序第一个报价”计算（固定差异基准），
  价格归因单独以“最低净支出”为基准，两者在响应中分别标注身份；
- 金额响应值统一 float（与全站契约一致，TASK-01 决策）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.models.enums import NetPaymentStatus, TotalCheckStatus
from app.schemas.common import CamelModel

# 差异标签：↑增加 ↓减少 +新增 −缺失 =相同（SPEC §7.3）
DiffTag = Literal["UP", "DOWN", "ADD", "MISS", "SAME"]

# 六个稳定分区（SPEC §7.4 顺序即数组顺序）
SectionKey = Literal["price", "core", "additional", "packages", "services", "net"]

# 单元格数值类型：金额/保额用 float，状态/口径用文本，共享等用布尔
CellKind = Literal["money", "amount", "count", "text"]


class CompareCell(CamelModel):
    """对比表单元格：基准列 tag=None（不渲染箭头），diff 恒为 False。"""

    text: str = Field(description="展示文本（已格式化，如 ¥5,785.14 / 300 万）")
    value: float | str | bool | None = Field(
        default=None, description="结构化值（测试与前端断言用）"
    )
    tag: DiffTag | None = None
    diff: bool = Field(default=False, description="与基准列不同（前端高亮）")


class CompareRow(CamelModel):
    """对比表一行（行=指标）：cells 与 quotes 数组顺序一一对应。"""

    key: str
    label: str
    kind: CellKind = "text"
    cells: list[CompareCell]
    diff: bool = Field(description="差异行（任一非基准列与基准不同；服务端已置顶排序）")
    note: str | None = Field(default=None, description="行级提示（如信息不足说明）")


class CompareSection(CamelModel):
    """对比分区：价格 → 核心保障 → 附加险 → 额外保障 → 增值服务 → 优惠/净支出。"""

    key: SectionKey
    title: str
    rows: list[CompareRow]


class CompareQuoteMeta(CamelModel):
    """方案列元信息：用户传入顺序；两种基准身份分别标注，互不改写。"""

    quote_id: int
    display_name: str
    insurer_code: str
    insurer_name: str
    agent_name: str | None = None
    plan_label: str | None = None
    status_label: str = Field(description="状态中文名（如“合并确认中”）")
    is_diff_baseline: bool = Field(description="行差异基准（勾选顺序第一个）")
    is_price_baseline: bool = Field(description="价格归因基准（最低净支出）")
    price_rank: int | None = Field(
        default=None, description="价格排序位次（0 起；净支出缺失排最后仍有序号）"
    )
    # 异常/口径标注：官方总价异常、含用户估值、总价缺失、优惠超额等，前端不得隐藏
    annotations: list[str]


class PriceOrderEntry(CamelModel):
    """价格排序视图（净支出升序，null 排最后并按状态标注原因）。"""

    quote_id: int
    net_payment: float | None = None
    net_payment_status: NetPaymentStatus
    official_total: float | None = None
    total_check_status: TotalCheckStatus
    has_user_valuation: bool
    rank: int


class CheapestAnswer(CamelModel):
    """第一问：哪个最便宜。kind 决定文案口径（MIN/TENTATIVE/价格不足）。"""

    kind: Literal["MIN", "TENTATIVE", "INSUFFICIENT_PRICE"]
    quote_ids: list[int] = Field(description="最低价报价（可能并列）")
    net_payment: float | None = None
    text: str


class StrongestMetric(CamelModel):
    """第二问单项：关键保障额度分别比较，绝不把不同保障对象求和。"""

    key: str
    label: str
    max_amount: float | None = None
    max_quote_ids: list[int] = Field(description="取得最高额度的报价（可能并列）")
    # 有该险种但保额缺失/未知的报价（其余报价可比时提示信息不足）
    missing_quote_ids: list[int] = Field(description="有该险种但缺保额/未知的报价")
    insufficient: bool = Field(default=False, description="所有方案均无可比保额")


class IncompleteQuote(CamelModel):
    """第三问单项：商业四大主险缺失清单（交强险不计入完整性判定）。"""

    quote_id: int
    display_name: str
    missing: list[str] = Field(description="缺失/未知的商业主险中文名")
    complete: bool


class AttributionPart(CamelModel):
    """第四问价格分项拆解：eff 值任一侧缺失时 comparable=False（不当 0）。"""

    key: str
    label: str
    baseline_value: float | None = None
    other_value: float | None = None
    delta: float | None = None
    comparable: bool


class CoverageTopChange(CamelModel):
    """第四问险种级归因 Top 变化（双方明细保费完整时才给出）。"""

    code: str | None = None
    label: str
    baseline_premium: float
    other_premium: float
    delta: float


class AttributionPair(CamelModel):
    """第四问一对方案：以最低净支出为基准的逐项拆解。"""

    other_quote_id: int
    delta_net: float | None = Field(
        default=None, description="对方净支出 − 基准净支出；任一侧缺失时为 None"
    )
    parts: list[AttributionPart]
    detail_complete: bool = Field(description="双方商业险明细保费是否完整")
    top_changes: list[CoverageTopChange] = Field(description="险种级归因 Top 变化（明细完整时）")
    note: str | None = None


class AttributionAnswer(CamelModel):
    """第四问整体：归因基准身份 + 每对方案的拆解结果。"""

    price_baseline_quote_id: int | None = None
    unavailable_reason: str | None = None
    pairs: list[AttributionPair]


class ScopeDifference(CamelModel):
    """第五问：核心保障口径差异（集合/状态/保额/单座/座位/共享/倍数/条件）。"""

    code: str
    label: str
    dimension: str
    detail: str


class UnknownInfoItem(CamelModel):
    """第五问：UNKNOWN/缺失导致的“信息不足，暂无法比较”项。"""

    code: str
    label: str
    dimension: str


class IncomparableAnswer(CamelModel):
    """第五问整体：同口径提示 + 信息不足 + 未识别项数量。"""

    scope_differs: bool
    differences: list[ScopeDifference] = Field(description="核心保障口径差异明细")
    unknown_items: list[UnknownInfoItem] = Field(description="信息不足项")
    unrecognized_count: int = Field(description="已确认保留的未识别金额项总数")
    messages: list[str] = Field(description="确定性规则生成的提示文案（可为空数组）")


class FiveQuestions(CamelModel):
    """五问总结（对比页第一屏，SPEC §7.2 / PRD 65 节）。"""

    cheapest: CheapestAnswer
    strongest: list[StrongestMetric] = Field(description="第二问各指标结果")
    incomplete: list[IncompleteQuote] = Field(description="第三问各方案完整性")
    attribution: AttributionAnswer
    incomparable: IncomparableAnswer


class ComparisonResult(CamelModel):
    """GET /api/projects/{id}/compare 响应数据。"""

    project_id: int
    # 方案列：用户传入顺序（差异基准恒为第一个）
    quotes: list[CompareQuoteMeta]
    # 价格排序视图（与 quotes 顺序无关，rank 升序）
    price_order: list[PriceOrderEntry]
    diff_baseline_quote_id: int
    price_baseline_quote_id: int | None = None
    five_questions: FiveQuestions
    sections: list[CompareSection]
    disclaimer: str = Field(description="统一免责声明（页面与导出长图共用）")
