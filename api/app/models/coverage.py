"""险种行（SPEC §2.5）：基础车险 + 附加险 + 未识别项统一存放。

铁律：
- 交强险不生成行（只落 quote 价格字段与 field_evidence）；
- 保障包内部驾乘类保障不得写入本表（隔离校验，PRD §59）；
- 金额非负由数据库 CheckConstraint 兜底。
"""

from __future__ import annotations

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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import CoverageCategory, ItemStatus
from app.models.mixins import SourceEvidenceMixin


class QuoteCoverage(SourceEvidenceMixin, TimestampMixin, Base):
    __tablename__ = "quote_coverage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quote.id", ondelete="CASCADE"), nullable=False
    )

    # 标准险种码；映射失败为 NULL 并进入 UNRECOGNIZED
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[CoverageCategory] = mapped_column(
        Enum(CoverageCategory, name="coverage_category"), nullable=False
    )

    # 原始名称必存（原始数据与标准数据并存原则）
    raw_name: Mapped[str] = mapped_column(Text, nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 标准显示名；UNRECOGNIZED 时等于 rawName
    name: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[ItemStatus] = mapped_column(
        Enum(ItemStatus, name="item_status"), nullable=False, default=ItemStatus.UNKNOWN
    )

    # 保额（元）与乘客险单座结构：总额 = 单座 × 座位（业务层换算）
    coverage_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    per_seat_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    seat_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shared_coverage: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    premium: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # 特殊保障规则：如节假日翻倍 multiplier=2、condition=LEGAL_HOLIDAY
    multiplier: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    quote = relationship("Quote", back_populates="coverages")

    __table_args__ = (
        CheckConstraint("coverage_amount IS NULL OR coverage_amount >= 0", name="coverage_amount_non_negative"),
        CheckConstraint("per_seat_amount IS NULL OR per_seat_amount >= 0", name="per_seat_amount_non_negative"),
        CheckConstraint("premium IS NULL OR premium >= 0", name="premium_non_negative"),
        CheckConstraint("multiplier IS NULL OR multiplier >= 0", name="multiplier_non_negative"),
        CheckConstraint("seat_count IS NULL OR seat_count > 0", name="seat_count_positive"),
        # 未识别行不允许带标准码，防止半归类状态
        CheckConstraint(
            "category <> 'UNRECOGNIZED' OR code IS NULL",
            name="unrecognized_has_no_code",
        ),
        Index("ix_quote_coverage_quote_id", "quote_id"),
        Index("ix_quote_coverage_code", "code"),
    )
