"""增值服务行（SPEC §2.7）。

状态语义：只有原文明确包含且费用为 0 才记 FREE；费用缺失是 UNKNOWN，不推断。
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
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
from app.models.enums import ItemStatus, ServiceType
from app.models.mixins import SourceEvidenceMixin


class QuoteService(SourceEvidenceMixin, TimestampMixin, Base):
    __tablename__ = "quote_service"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quote.id", ondelete="CASCADE"), nullable=False
    )

    service_type: Mapped[ServiceType] = mapped_column(
        Enum(ServiceType, name="service_type"), nullable=False, default=ServiceType.OTHER
    )
    status: Mapped[ItemStatus] = mapped_column(
        Enum(ItemStatus, name="item_status"), nullable=False, default=ItemStatus.UNKNOWN
    )
    count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    quote = relationship("Quote", back_populates="services")

    __table_args__ = (
        CheckConstraint("count IS NULL OR count >= 0", name="count_non_negative"),
        CheckConstraint("cost IS NULL OR cost >= 0", name="cost_non_negative"),
        Index("ix_quote_service_quote_id", "quote_id"),
    )
