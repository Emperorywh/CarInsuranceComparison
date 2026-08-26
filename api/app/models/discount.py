"""优惠/返现（SPEC §2.7，用户填写）。

净支出公式：netPayment = (officialTotal ?? computedTotal)
                     − Σ(includeInNet 且 cashEquivalent 非空 ? cashEquivalent : 0)。
名义金额 amount 仅展示用，绝不参与净支出；SERVICE 类默认无折现值。
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
from app.models.enums import DiscountType


class Discount(TimestampMixin, Base):
    __tablename__ = "discount"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quote.id", ondelete="CASCADE"), nullable=False
    )

    discount_type: Mapped[DiscountType] = mapped_column(
        Enum(DiscountType, name="discount_type"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 名义金额：仅展示
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # 折现估值：用户自愿填写，为空则该优惠不减钱
    cash_equivalent: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    include_in_net: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    quote = relationship("Quote", back_populates="discounts")

    __table_args__ = (
        CheckConstraint("amount IS NULL OR amount >= 0", name="amount_non_negative"),
        CheckConstraint(
            "cash_equivalent IS NULL OR cash_equivalent >= 0", name="cash_equivalent_non_negative"
        ),
        Index("ix_discount_quote_id", "quote_id"),
    )
