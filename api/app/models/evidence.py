"""报价标量字段来源（SPEC §2.7）。

只服务价格、保险公司、车辆信息等标量字段；明细行使用自身 source* 字段，
不重复写入本表。(quoteId, fieldName) 唯一由数据库约束保证。
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import ConfidenceLevel


class FieldEvidence(TimestampMixin, Base):
    __tablename__ = "field_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quote.id", ondelete="CASCADE"), nullable=False
    )

    # 标量字段名（如 commercialPremium / insurerCode / vehicleModel）
    field_name: Mapped[str] = mapped_column(Text, nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("quote_file.id", ondelete="SET NULL"), nullable=True
    )
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 入库前脱敏的最短原文摘录
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_level: Mapped[ConfidenceLevel] = mapped_column(
        Enum(ConfidenceLevel, name="confidence_level"), nullable=False
    )
    edited_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    quote = relationship("Quote", back_populates="evidences")

    __table_args__ = (
        # 同一标量字段只允许一条来源记录，避免证据漂移
        UniqueConstraint("quote_id", "field_name", name="uq_field_evidence_quote_field"),
        CheckConstraint("source_page IS NULL OR source_page >= 1", name="source_page_positive"),
        Index("ix_field_evidence_quote_id", "quote_id"),
    )
