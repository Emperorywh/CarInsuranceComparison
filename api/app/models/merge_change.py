"""补传合并变更集（SPEC §2.9）。

已确认数据永不静默覆盖：解析成功只生成 ADD/CONFLICT 变更，
用户逐项 ACCEPT/KEEP 后才合入报价。MVP 不自动生成 DELETE。
"""

from __future__ import annotations

from sqlalchemy import (
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import MergeChangeKind, MergeResolution


class MergeChange(TimestampMixin, Base):
    __tablename__ = "merge_change"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quote.id", ondelete="CASCADE"), nullable=False
    )
    parse_task_id: Mapped[int] = mapped_column(
        ForeignKey("parse_task.id", ondelete="CASCADE"), nullable=False
    )

    # 实体层标识（如 coverage / service / package / scalar）+ 稳定业务键
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_key: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str] = mapped_column(Text, nullable=False)

    # 旧值/新值（JSONB，已脱敏）；ADD 的 oldValue 为 null
    old_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    kind: Mapped[MergeChangeKind] = mapped_column(
        Enum(MergeChangeKind, name="merge_change_kind"), nullable=False
    )
    resolution: Mapped[MergeResolution] = mapped_column(
        Enum(MergeResolution, name="merge_resolution"),
        nullable=False,
        default=MergeResolution.PENDING,
    )

    quote = relationship("Quote", back_populates="merge_changes")

    __table_args__ = (
        Index("ix_merge_change_quote_id", "quote_id"),
        Index("ix_merge_change_parse_task_id", "parse_task_id"),
    )
