"""访问令牌中间件行为测试（SPEC §9.4）。

矩阵：
- 本机模式（无令牌）：全部端点可匿名访问；
- 令牌模式（本机或非回环绑定行为一致）：健康检查匿名可访问，
  业务 API / OpenAPI / 交互文档无令牌 401、错误令牌 401、正确令牌成功。
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_public_without_token(client: AsyncClient) -> None:
    """默认本机模式：健康检查匿名可访问。"""
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "OK"
    assert body["data"]["status"] == "ok"


async def test_local_mode_allows_anonymous(client: AsyncClient) -> None:
    """默认本机模式：业务 API 无需令牌。"""
    response = await client.get("/api/projects")
    assert response.status_code == 200
    assert response.json()["data"] == []


async def test_token_mode_health_still_public(token_client: AsyncClient) -> None:
    """令牌模式：健康检查依旧匿名可访问（唯一豁免）。"""
    token_client.headers.pop("X-Access-Token", None)
    response = await token_client.get("/health")
    assert response.status_code == 200


async def test_token_mode_business_requires_token(token_client: AsyncClient) -> None:
    """令牌模式：无令牌 401、错误令牌 401、正确令牌 200。"""
    saved = dict(token_client.headers)
    token_client.headers.pop("X-Access-Token", None)
    response = await token_client.get("/api/projects")
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "UNAUTHORIZED"
    assert "访问令牌" in body["message"]

    token_client.headers["X-Access-Token"] = "wrong-token"
    response = await token_client.get("/api/projects")
    assert response.status_code == 401

    token_client.headers.clear()
    token_client.headers.update(saved)
    response = await token_client.get("/api/projects")
    assert response.status_code == 200


async def test_token_mode_protects_docs(token_client: AsyncClient) -> None:
    """令牌模式：OpenAPI 与交互文档同样受保护。"""
    saved = dict(token_client.headers)
    token_client.headers.pop("X-Access-Token", None)
    for path in ("/docs", "/redoc", "/openapi.json"):
        response = await token_client.get(path)
        assert response.status_code == 401, path
    token_client.headers.clear()
    token_client.headers.update(saved)


async def test_cors_headers_on_unauthorized(token_client: AsyncClient) -> None:
    """令牌模式的 401 响应也必须带 CORS 头（中间件顺序保证）。"""
    token_client.headers.pop("X-Access-Token", None)
    response = await token_client.get(
        "/api/projects",
        headers={"Origin": "http://localhost:3000", **token_client.headers},
    )
    assert response.status_code == 401
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
