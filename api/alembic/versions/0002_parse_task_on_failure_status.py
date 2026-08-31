"""TASK-05：parse_task 增加失败联动恢复状态列

业务背景（SPEC §2.10 状态机扩展）：
- PENDING_CONFIRM 的重新解析/补传会把报价置为 PARSING；任务失败时必须
  回到 PENDING_CONFIRM（保留上一次候选数据），而不是被现有
  “PARSING 失败 → PARSE_FAILED”联动误伤；
- 该列记录“任务失败且目标报价仍处于 PARSING 时应恢复的状态”：
  - 首次上传 / PARSE_FAILED 重试：NULL（沿用 PARSE_FAILED）；
  - PENDING_CONFIRM 重解析/补传：PENDING_CONFIRM；
  - CONFIRMED 补传/重解析：报价全程保持 CONFIRMED（不进入 PARSING），
    本列为 NULL 且永远不会被读取。
- 成功路径不读本列：候选落库（PENDING_CONFIRM）与合并审阅（MERGE_REVIEW）
  由流水线在事务内显式迁移。

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-31

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 复用 0001 已创建的 quote_status 原生枚举类型（create_type=False），
    # 不新增枚举类型；可空 NULL 表达“沿用默认 PARSE_FAILED 联动”
    op.add_column(
        "parse_task",
        sa.Column(
            "on_failure_quote_status",
            sa.Enum(name="quote_status", create_type=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("parse_task", "on_failure_quote_status")
