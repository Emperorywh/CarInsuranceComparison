"""SQLAlchemy 2 async 引擎与会话工厂。

- engine 惰性创建（模块级函数而非导入期对象），避免测试导入 app 包时就连数据库；
- 会话统一通过 FastAPI 依赖注入获取，路由不得自行创建 engine。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """按当前配置惰性创建全局 engine（进程内复用连接池）。"""
    global _engine, _session_factory
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取与全局 engine 绑定的会话工厂。"""
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个会话，异常时回滚，结束时关闭。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            # 出错统一回滚，避免半提交状态污染连接池
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """测试或优雅停机时释放连接池。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
