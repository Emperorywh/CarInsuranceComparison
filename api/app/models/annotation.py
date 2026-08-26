"""销售/用户标注（SPEC §2.7）：红色文字、箭头、手写标注等。

默认不参与任何结构化对比与金额计算；确认页单独 Tab 展示并附提示文案。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Enum, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import AnnotationKind, AnnotationSourceType


class SalesAnnotation(TimestampMixin, Base):
    __tablename__ = "sales_annotation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quote.id", ondelete="CASCADE"), nullable=False
    )

    # 入库前统一脱敏的标注内容
    content: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[AnnotationKind] = mapped_column(
        Enum(AnnotationKind, name="annotation_kind"), nullable=False, default=AnnotationKind.OTHER
    )
    source_type: Mapped[AnnotationSourceType] = mapped_column(
        Enum(AnnotationSourceType, name="annotation_source_type"),
        nullable=False,
        default=AnnotationSourceType.SALES_ANNOTATION,
    )

    source_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("quote_file.id", ondelete="SET NULL"), nullable=True
    )
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    edited_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    quote = relationship("Quote", back_populates="annotations")

    __table_args__ = (
        Index("ix_sales_annotation_quote_id", "quote_id"),
    )
