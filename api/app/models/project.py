"""对比项目（SPEC §2.1）：一个项目 = 一辆车的一个续保周期。

MVP 将 PRD 的独立 Vehicle 降级为本表字段；不建 User 表，仅预留 userId。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ComparisonProject(TimestampMixin, Base):
    __tablename__ = "comparison_project"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 用户填写的项目基础信息
    name: Mapped[str] = mapped_column(Text, nullable=False)
    renewal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    expire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    vehicle_name: Mapped[str] = mapped_column(Text, nullable=False)

    # 车辆摘要：由首份确认报价回填；与后续报价冲突时必须用户显式确认，不静默覆盖
    vehicle_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    vehicle_seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_reg_date: Mapped[str | None] = mapped_column(Text, nullable=True)  # 月精度，如 2022-05
    is_nev: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # 预留：单用户无登录，子表通过项目归属继承用户
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 首次同意“原文件发送至视觉模型”的时间；为空时创建解析任务必须显式携带同意
    model_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    quotes = relationship(
        "Quote",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    files = relationship(
        "QuoteFile",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    parse_tasks = relationship(
        "ParseTask",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # 续保年份限合理区间，防止把“年份”当成金额之类的输入错误长期存库
        CheckConstraint("renewal_year BETWEEN 2000 AND 2100", name="renewal_year_range"),
        Index("ix_comparison_project_created_at", "created_at"),
    )
