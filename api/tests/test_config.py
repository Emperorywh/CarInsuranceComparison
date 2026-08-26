"""配置与启动期安全校验测试（SPEC §9.4）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


def test_default_loopback_without_token_is_allowed() -> None:
    """默认本机模式（回环绑定、无令牌）合法。"""
    settings = Settings(app_bind_host="127.0.0.1", local_access_token="")
    assert settings.token_required is False


def test_non_loopback_without_token_refuses_startup() -> None:
    """绑定非回环地址且未配置令牌时，配置校验直接失败（拒绝启动）。"""
    with pytest.raises(ValueError, match="LOCAL_ACCESS_TOKEN"):
        Settings(app_bind_host="0.0.0.0", local_access_token="")
    with pytest.raises(ValueError, match="LOCAL_ACCESS_TOKEN"):
        Settings(app_bind_host="192.168.1.5", local_access_token="")
    # IPv6 非回环同样拒绝
    with pytest.raises(ValueError):
        Settings(app_bind_host="::", local_access_token="")


def test_loopback_addresses_recognized() -> None:
    """回环地址集合：127.0.0.1 / localhost / ::1。"""
    for host in ("127.0.0.1", "localhost", "::1"):
        Settings(app_bind_host=host, local_access_token="")


def test_token_enables_check_regardless_of_bind() -> None:
    """令牌非空即启用校验，与绑定地址无关：本机 + 令牌也启用。"""
    settings = Settings(app_bind_host="127.0.0.1", local_access_token="secret")
    assert settings.token_required is True
    settings = Settings(app_bind_host="0.0.0.0", local_access_token="secret")
    assert settings.token_required is True


def test_create_app_rejects_insecure_settings() -> None:
    """应用工厂同样执行启动期校验，双保险防止绕过配置直接构造。"""
    with pytest.raises(ValueError):
        create_app(Settings(app_bind_host="10.0.0.8", local_access_token=""))


def test_cors_origins_parsed() -> None:
    settings = Settings(
        app_bind_host="127.0.0.1",
        allowed_origins="http://a.com, http://b.com ,",
    )
    assert settings.cors_origins == ["http://a.com", "http://b.com"]


def test_database_url_must_be_postgresql() -> None:
    """只接受 PostgreSQL 连接串，提前拦截 sqlite/mysql 误配。"""
    with pytest.raises(ValidationError):
        Settings(app_bind_host="127.0.0.1", database_url="sqlite:///./db.sqlite3")
