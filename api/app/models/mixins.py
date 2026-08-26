"""明细行共用字段混入：来源定位、置信度与用户编辑保护（SPEC §5）。"""

from __future__ import annotations

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enums import ConfidenceLevel


class SourceEvidenceMixin:
    """险种/服务/保障包等明细行的来源与编辑保护字段。

    - sourceFileId 只允许指向本任务输入过的文件（业务层校验后写入）；
    - editedByUser 置位后该行不再被重新解析静默覆盖。
    """

    source_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("quote_file.id", ondelete="SET NULL"), nullable=True
    )
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 入库前已脱敏的最短原文摘录
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_level: Mapped[ConfidenceLevel] = mapped_column(
        Enum(ConfidenceLevel, name="confidence_level"), nullable=False
    )
    edited_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
