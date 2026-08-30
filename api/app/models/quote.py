"""报价（SPEC §2.2）：价格字段合并进 quote 表，不建独立 QuotePrice。

不变量：
- 金额一律 numeric 非负（CheckConstraint）；
- 状态字段与金额字段配合表达三态语义，任何 null 都不得当 0；
- 交强险/车船税只落价格字段与 field_evidence，不生成 quote_coverage 行。
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    NetPaymentStatus,
    OfficialTotalStatus,
    PriceItemStatus,
    QuoteSource,
    QuoteStatus,
    TotalCheckStatus,
)

# 非负金额检查统一约束名后缀，迁移与错误提示保持一致
_PRICE_COLUMNS = (
    "commercial_premium",
    "computed_commercial_premium",
    "compulsory_premium",
    "vehicle_tax",
    "package_total",
    "computed_package_total",
    "other_fees",
    "official_total",
    "computed_total",
    "net_payment",
)


class Quote(TimestampMixin, Base):
    __tablename__ = "quote"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("comparison_project.id", ondelete="CASCADE"), nullable=False
    )

    # 公司码必填：预置公司存预置码，选“其他”固定存 OTHER（不存在 NULL 态）
    insurer_code: Mapped[str] = mapped_column(Text, nullable=False)
    insurer_name: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_label: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[QuoteSource] = mapped_column(
        Enum(QuoteSource, name="quote_source"), nullable=False
    )
    status: Mapped[QuoteStatus] = mapped_column(
        Enum(QuoteStatus, name="quote_status"), nullable=False, default=QuoteStatus.DRAFT
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 预留：第二阶段版本链分组
    version_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # 每份报价自己的车辆快照，用于阻止不同车辆误入同一项目
    vehicle_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    vehicle_seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_reg_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_nev: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # ---- 价格分项（显示值 / 系统计算值回退，见 SPEC §2.2 计算口径）----
    commercial_premium: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    computed_commercial_premium: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    compulsory_premium: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    vehicle_tax: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    package_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    computed_package_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    other_fees: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    commercial_status: Mapped[PriceItemStatus] = mapped_column(
        Enum(PriceItemStatus, name="price_item_status"),
        nullable=False,
        default=PriceItemStatus.UNKNOWN,
    )
    compulsory_status: Mapped[PriceItemStatus] = mapped_column(
        Enum(PriceItemStatus, name="price_item_status", create_type=False),
        nullable=False,
        default=PriceItemStatus.UNKNOWN,
    )
    vehicle_tax_status: Mapped[PriceItemStatus] = mapped_column(
        Enum(PriceItemStatus, name="price_item_status", create_type=False),
        nullable=False,
        default=PriceItemStatus.UNKNOWN,
    )
    package_status: Mapped[PriceItemStatus] = mapped_column(
        Enum(PriceItemStatus, name="price_item_status", create_type=False),
        nullable=False,
        default=PriceItemStatus.UNKNOWN,
    )
    other_fees_status: Mapped[PriceItemStatus] = mapped_column(
        Enum(PriceItemStatus, name="price_item_status", create_type=False),
        nullable=False,
        default=PriceItemStatus.UNKNOWN,
    )

    official_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    official_total_status: Mapped[OfficialTotalStatus] = mapped_column(
        Enum(OfficialTotalStatus, name="official_total_status"),
        nullable=False,
        default=OfficialTotalStatus.UNKNOWN,
    )

    computed_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_check_status: Mapped[TotalCheckStatus] = mapped_column(
        Enum(TotalCheckStatus, name="total_check_status"),
        nullable=False,
        default=TotalCheckStatus.NOT_CHECKABLE,
    )

    net_payment: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    net_payment_status: Mapped[NetPaymentStatus] = mapped_column(
        Enum(NetPaymentStatus, name="net_payment_status"),
        nullable=False,
        default=NetPaymentStatus.MISSING_TOTAL,
    )

    project = relationship("ComparisonProject", back_populates="quotes")

    @property
    def quality_warnings(self) -> list[str]:
        """读模型占位属性：质量警告由 quote_service.build_quote_read 计算填充。

        QuoteRead 递归校验要求 ORM 上存在同名的必填字段（与 TASK-03 给
        QuoteFile 加 file_name/raw_url 展示属性同一模式）；真实值在读模型
        组装时经 model_copy(update=...) 注入。
        """
        return []

    # 明细各层：删除报价时级联删除（磁盘文件清理遵循无引用规则，见 TASK-03）
    coverages = relationship(
        "QuoteCoverage", cascade="all, delete-orphan", passive_deletes=True
    )
    services = relationship("QuoteService", cascade="all, delete-orphan", passive_deletes=True)
    packages = relationship(
        "SupplementalPackage", cascade="all, delete-orphan", passive_deletes=True
    )
    annotations = relationship(
        "SalesAnnotation", cascade="all, delete-orphan", passive_deletes=True
    )
    discounts = relationship("Discount", cascade="all, delete-orphan", passive_deletes=True)
    evidences = relationship("FieldEvidence", cascade="all, delete-orphan", passive_deletes=True)
    merge_changes = relationship(
        "MergeChange", cascade="all, delete-orphan", passive_deletes=True
    )
    # 通过 quote_file_link 的多对多文件关联（secondary 由关联表名解析）。
    # 只读视图：关联行的增删由关联表自身的级联策略处理；
    # 展示顺序固定按 sortOrder（用户上传顺序），保证前端文件条顺序稳定
    files = relationship(
        "QuoteFile",
        secondary="quote_file_link",
        viewonly=True,
        order_by="QuoteFileLink.sort_order",
    )
    parse_tasks = relationship(
        "ParseTask",
        back_populates="quote",
        # 报价删除时任务保留（回放数据），quote_id 由数据库置空
        passive_deletes=True,
    )

    __table_args__ = (
        # 全部金额字段非负：数据库层兜底，业务层校验之外的最后一道防线
        *(
            CheckConstraint(f"{column} IS NULL OR {column} >= 0", name=f"{column}_non_negative")
            for column in _PRICE_COLUMNS
        ),
        CheckConstraint("vehicle_seats IS NULL OR vehicle_seats > 0", name="vehicle_seats_positive"),
        Index("ix_quote_project_id", "project_id"),
        Index("ix_quote_status", "status"),
    )
