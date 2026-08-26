"""解析任务与其输入文件（SPEC §2.4）。

不变量：
- 任务永久归属项目（拆分后仍保留回放数据）；quoteId 随报价删除置空；
- 同一报价同一时间只允许一个活动任务（PENDING/RUNNING），
  由部分唯一索引在数据库层强制，业务层 409 只是友好前置；
- rawResult 只保存白名单过滤与脱敏后的模型输出。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import ParseTaskStatus


class ParseTask(TimestampMixin, Base):
    __tablename__ = "parse_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("comparison_project.id", ondelete="CASCADE"), nullable=False
    )
    # 当前目标报价；报价被拆分或删除时置空，任务与 rawResult 保留
    quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("quote.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[ParseTaskStatus] = mapped_column(
        Enum(ParseTaskStatus, name="parse_task_status"),
        nullable=False,
        default=ParseTaskStatus.PENDING,
    )
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 已执行的总尝试次数：首次调用后为 1，最大 3
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 脱敏后的错误摘要；绝不写入模型请求正文或原始文件内容
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 白名单过滤和自由文本脱敏后的模型输出；任务成功前为 null
    raw_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project = relationship("ComparisonProject", back_populates="parse_tasks")
    quote = relationship("Quote", back_populates="parse_tasks")
    # 输入文件固定表：记录 fileKey 分配顺序，保证证据可回放
    input_files = relationship(
        "ParseTaskFile", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("attempt >= 0", name="attempt_non_negative"),
        Index("ix_parse_task_quote_id", "quote_id"),
        Index("ix_parse_task_status", "status"),
        # 数据库级互斥：同一报价最多一个活动任务（PENDING/RUNNING）。
        # PostgreSQL 部分唯一索引，Alembic 迁移中显式创建。
        Index(
            "uq_parse_task_active_quote",
            "quote_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING') AND quote_id IS NOT NULL"),
        ),
    )


class ParseTaskFile(Base):
    __tablename__ = "parse_task_file"

    task_id: Mapped[int] = mapped_column(
        ForeignKey("parse_task.id", ondelete="CASCADE"), primary_key=True
    )
    file_id: Mapped[int] = mapped_column(
        ForeignKey("quote_file.id", ondelete="CASCADE"), primary_key=True
    )
    # 输入顺序决定 fileKey 分配（F1/F2/...），保证回放可复现
    input_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        PrimaryKeyConstraint("task_id", "file_id", name="pk_parse_task_file"),
    )
