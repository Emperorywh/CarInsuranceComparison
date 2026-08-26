"""独立保障包及其内部保障（SPEC §2.6）。

隔离铁律：package_coverage 的驾乘类保障（DRIVER_ACCIDENT 等）永远
不得写入 quote_coverage 的 DRIVER_LIABILITY / PASSENGER_LIABILITY，
由归一化引擎与校验规则双层保证。
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
from app.models.enums import ItemStatus, PackageUnit
from app.models.mixins import SourceEvidenceMixin


class SupplementalPackage(SourceEvidenceMixin, TimestampMixin, Base):
    """独立保障包（如“平安车主尊享保障”），价格独立计算。"""

    __tablename__ = "supplemental_package"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quote.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    premium: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    quote = relationship("Quote", back_populates="packages")
    coverages = relationship(
        "PackageCoverage", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("premium IS NULL OR premium >= 0", name="premium_non_negative"),
        Index("ix_supplemental_package_quote_id", "quote_id"),
    )


class PackageCoverage(SourceEvidenceMixin, TimestampMixin, Base):
    """保障包内部保障行，支持单座/共享/倍数/条件等特殊规则。"""

    __tablename__ = "package_coverage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    package_id: Mapped[int] = mapped_column(
        ForeignKey("supplemental_package.id", ondelete="CASCADE"), nullable=False
    )

    # 保障类型码（SPEC §3.3）；未知类型统一 OTHER，不臆测
    type: Mapped[str] = mapped_column(Text, nullable=False, default="OTHER")
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ItemStatus] = mapped_column(
        Enum(ItemStatus, name="item_status"), nullable=False, default=ItemStatus.UNKNOWN
    )
    coverage_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    unit: Mapped[PackageUnit | None] = mapped_column(
        Enum(PackageUnit, name="package_unit"), nullable=True
    )
    per_seat_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    seat_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shared: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    multiplier: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    package = relationship("SupplementalPackage", back_populates="coverages")

    __table_args__ = (
        CheckConstraint("coverage_amount IS NULL OR coverage_amount >= 0", name="coverage_amount_non_negative"),
        CheckConstraint("per_seat_amount IS NULL OR per_seat_amount >= 0", name="per_seat_amount_non_negative"),
        CheckConstraint("multiplier IS NULL OR multiplier >= 0", name="multiplier_non_negative"),
        CheckConstraint("seat_count IS NULL OR seat_count > 0", name="seat_count_positive"),
        Index("ix_package_coverage_package_id", "package_id"),
    )
