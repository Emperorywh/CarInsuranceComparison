"""文件资产与报价的多对多关联（SPEC §2.3）。

设计要点：
- 文件属于项目，不由某个报价独占；多方案拆分后兄弟报价通过关联表共享；
- (quoteId, fileId) 联合主键防止重复关联；sortOrder 保留报价内展示顺序；
- 删除报价只删关联，删除文件资产遵循“无引用才删”规则（TASK-03）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class QuoteFile(Base):
    __tablename__ = "quote_file"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("comparison_project.id", ondelete="CASCADE"), nullable=False
    )
    # 相对 UPLOAD_DIR 的路径；磁盘文件名随机化，不保留用户原始文件名
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    # 仅保存脱敏后的展示名；检测到敏感信息时改为通用文件名
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    # 只允许 image/jpeg、image/png、application/pdf（上传服务负责签名校验）
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # 图片固定为 1，PDF 为实际页数；页码校验依赖该值
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project = relationship("ComparisonProject", back_populates="files")

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),
        CheckConstraint("page_count >= 1", name="page_count_positive"),
        Index("ix_quote_file_project_id", "project_id"),
    )


class QuoteFileLink(Base):
    __tablename__ = "quote_file_link"

    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quote.id", ondelete="CASCADE"), primary_key=True
    )
    file_id: Mapped[int] = mapped_column(
        ForeignKey("quote_file.id", ondelete="CASCADE"), primary_key=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        # 显式命名联合主键，保持约束命名约定
        PrimaryKeyConstraint("quote_id", "file_id", name="pk_quote_file_link"),
    )
