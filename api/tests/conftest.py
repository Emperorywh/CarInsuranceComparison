"""pytest 共享 fixture：一次性测试 PostgreSQL、从空库迁移、事务回滚会话与 API 客户端。

设计要点：
- 测试库完全独立（嵌入式实例或 TEST_DATABASE_URL 指向的库），绝不触碰开发者数据库；
- 每个测试用例运行在可回滚事务中，用例之间互不污染；
- API 测试通过依赖注入替换数据库会话，不连接真实 engine。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.config import Settings

# 让 conftest 能导入 pg_server（tests 目录无 __init__.py 时 pytest 不自动加路径）
sys.path.insert(0, str(Path(__file__).parent))

from pg_server import EmbeddedPostgres  # noqa: E402

API_ROOT = Path(__file__).resolve().parents[1]
TEST_DB_NAME = "car_insurance_test"


class TestPostgres:
    """包装一次性 PostgreSQL：提供“销毁重建数据库”能力。"""

    def __init__(self, base_url: str, _server: EmbeddedPostgres | None) -> None:
        self.base_url = base_url  # 指向维护库 postgres 的 URL
        self._server = _server

    def database_url(self, name: str) -> str:
        return self.base_url.rsplit("/", 1)[0] + "/" + name

    async def _recreate_async(self, name: str) -> None:
        conn = await asyncpg.connect(
            host="127.0.0.1",
            port=int(self.base_url.rsplit(":", 1)[1].split("/")[0]),
            user="postgres",
            database="postgres",
        )
        # WITH (FORCE) 断开残留连接，保证可重复执行
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
        await conn.close()

    def fresh_database(self, name: str) -> str:
        """销毁并重建指定数据库（同步入口，供同步 fixture 使用）。"""
        asyncio.run(self._recreate_async(name))
        return self.database_url(name)

    async def afresh_database(self, name: str) -> str:
        """销毁并重建指定数据库（异步入口，供事件循环内的 fixture 使用）。"""
        await self._recreate_async(name)
        return self.database_url(name)

    def stop(self) -> None:
        if self._server is not None:
            self._server.stop()


def _external_base_url() -> str | None:
    """从 TEST_DATABASE_URL 推导维护库 URL（外部数据库模式）。"""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        return None
    prefix, _, dbname = url.rpartition("/")
    if not dbname or dbname == "postgres":
        return url
    return prefix + "/postgres"


@pytest.fixture(scope="session")
def test_postgres() -> TestPostgres:
    """一次性 PostgreSQL：优先用嵌入式实例；设置了 TEST_DATABASE_URL 则复用外部库。"""
    external = _external_base_url()
    if external is not None:
        server = TestPostgres(external, None)
        yield server
        return
    server_holder = EmbeddedPostgres()
    base_url = server_holder.start()
    server = TestPostgres(base_url, server_holder)
    yield server
    server.stop()


@pytest.fixture(scope="session")
def database_url(test_postgres: TestPostgres) -> str:
    """一次性测试库：每次测试会话从零重建，独立于任何开发数据。"""
    return test_postgres.fresh_database(TEST_DB_NAME)


@pytest.fixture(scope="session")
def migrated(database_url: str) -> str:
    """从空库执行 Alembic 迁移到 head（这本身就是 TASK-01 的验证项之一）。"""
    os.environ["DATABASE_URL"] = database_url
    cfg = Config(str(API_ROOT / "alembic.ini"))
    # 用绝对路径，避免受 pytest 工作目录影响
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    return database_url


@pytest.fixture
async def db_engine(migrated: str) -> AsyncSession:
    """每个用例独立的 async engine。

    NullPool：测试引擎不做连接池化，避免连接绑定到已销毁的事件循环。
    """
    engine = create_async_engine(migrated, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine) -> AsyncSession:
    """可自由 commit 的测试会话：外层事务 + savepoint，用例结束整体回滚。

    join_transaction_mode="create_savepoint" 让被测代码里的 commit() 只提交
    保存点，保证用例间数据隔离（服务层无需感知测试事务）。
    """
    async with db_engine.connect() as connection:
        outer = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.rollback()
            await outer.rollback()


def _test_settings(**overrides) -> Settings:  # type: ignore[name-defined]
    from app.config import Settings

    defaults = dict(app_bind_host="127.0.0.1", local_access_token="")
    defaults.update(overrides)
    return Settings(**defaults)


def _make_client(db_session: AsyncSession, settings) -> AsyncClient:  # noqa: ANN001
    from app.api.deps import get_db
    from app.main import create_app

    app = create_app(settings)

    async def _override() -> AsyncSession:
        yield db_session

    app.dependency_overrides[get_db] = _override
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    )


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncClient:
    """默认本机模式（无令牌）的 API 客户端。"""
    async with _make_client(db_session, _test_settings()) as http:
        yield http


@pytest.fixture
async def token_client(db_session: AsyncSession) -> AsyncClient:
    """启用 LOCAL_ACCESS_TOKEN 的 API 客户端（模拟局域网模式）。"""
    async with _make_client(db_session, _test_settings(local_access_token="test-token-123")) as http:
        http.headers["X-Access-Token"] = "test-token-123"
        yield http
