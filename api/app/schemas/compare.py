"""对比结果契约（TASK-06，SPEC §7；对比页为单一总表）。

设计约定：
- 服务端只产出结构化数据与展示文本，不产出 AI 文案；
- `CompareCell.value` 携带结构化值（金额 float / 文本 str / 布尔），
  `text` 为服务端格式化好的展示文本（前端直接渲染，导出长图复用）；
- 差异标签相对“用户勾选顺序第一个报价”计算（固定差异基准），
  价格基准（最低净支出）单独标注身份，两者互不改写；
- 金额响应值统一 float（与全站契约一致，TASK-01 决策）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.models.enums import NetPaymentStatus, TotalCheckStatus
from app.schemas.common import CamelModel

# 差异标签：↑增加 ↓减少 +新增 −缺失 =相同（SPEC §7.3）
DiffTag = Literal["UP", "DOWN", "ADD", "MISS", "SAME"]

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
    is_price_baseline: bool = Field(description="价格基准（净支出最低）")
    price_rank: int | None = Field(
        default=None, description="价格排序位次（0 起；净支出缺失排最后仍有序号）"
    )
    # 异常/口径标注：官方总价异常、含用户估值、总价缺失、优惠超额、
    # 合并确认中、未识别保障未参与对比等，前端不得隐藏
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


class ComparisonResult(CamelModel):
    """GET /api/projects/{id}/compare 响应数据：单一总表的全部指标行。"""

    project_id: int
    # 方案列：用户传入顺序（差异基准恒为第一个）
    quotes: list[CompareQuoteMeta]
    # 价格排序视图（与 quotes 顺序无关，rank 升序）
    price_order: list[PriceOrderEntry]
    diff_baseline_quote_id: int
    price_baseline_quote_id: int | None = None
    # 单一总表：价格 → 核心保障 → 附加险 → 额外保障 → 增值服务 → 优惠/净支出
    # 的指标行按此顺序拼接，各分组内差异行置顶（页面与导出长图同源）
    rows: list[CompareRow]
    disclaimer: str = Field(description="统一免责声明（页面与导出长图共用）")
