"""迁移循环测试：升级 → 降级 → 再升级必须可行（枚举类型被正确回收）。

注意：本模块刻意使用同步测试——alembic env.py 内部调用 asyncio.run，
不能在已运行的事件循环内嵌套执行。使用独立的第二个测试库，
避免影响其他用例已迁移的库。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
from alembic.config import Config

from alembic import command

API_ROOT = Path(__file__).resolve().parents[1]
CYCLE_DB = "car_insurance_cycle"


def _alembic_config(database_url: str) -> Config:
    os.environ["DATABASE_URL"] = database_url
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


def test_upgrade_downgrade_upgrade_cycle(test_postgres) -> None:
    """空库 → head → base → head 全程成功，且业务表与枚举数量正确。"""
    url = test_postgres.fresh_database(CYCLE_DB)
    cfg = _alembic_config(url)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    async def _verify() -> None:
        host = url.split("@")[1].split(":")[0]
        port = int(url.split("@")[1].split(":")[1].split("/")[0])
        conn = await asyncpg.connect(host=host, port=port, user="postgres", database=CYCLE_DB)
        try:
            rows = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"
            )
            tables = [row[0] for row in rows]
            # 14 张业务表 + alembic_version
            assert len(tables) == 15
            assert "alembic_version" in tables
            assert "comparison_project" in tables
            assert "merge_change" in tables
            enums = await conn.fetch(
                "SELECT typname FROM pg_type WHERE typname IN ('quote_status', 'item_status')"
            )
            assert {row[0] for row in enums} == {"quote_status", "item_status"}
        finally:
            await conn.close()

    asyncio.run(_verify())
