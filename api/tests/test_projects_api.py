"""项目 API 集成测试：完整主路径（空列表 → 创建 → 查看 → 编辑 → 删除）与边界。"""

from __future__ import annotations

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Quote
from app.models.enums import QuoteSource, QuoteStatus


async def _create_project(client: AsyncClient, **overrides) -> dict:
    payload = {
        "name": "2026 车辆续保",
        "vehicleName": "Model Y",
        "renewalYear": 2026,
        "expireDate": "2026-05-31",
        "note": "多家比价",
    }
    payload.update(overrides)
    response = await client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == "OK"
    return body["data"]


async def test_empty_list_is_stable(client: AsyncClient) -> None:
    """无项目时返回稳定的空状态（data 为空列表，不报错）。"""
    response = await client.get("/api/projects")
    assert response.status_code == 200
    body = response.json()
    assert body == {"code": "OK", "message": "ok", "data": []}


async def test_create_and_get_project(client: AsyncClient) -> None:
    """创建 → 详情：字段 camelCase 往返一致。"""
    created = await _create_project(client)
    assert created["name"] == "2026 车辆续保"
    assert created["vehicleName"] == "Model Y"
    assert created["renewalYear"] == 2026
    assert created["expireDate"] == "2026-05-31"
    assert created["createdAt"]
    assert created["modelConsentAt"] is None

    response = await client.get(f"/api/projects/{created['id']}")
    assert response.status_code == 200
    assert response.json()["data"] == created


async def test_create_validation_errors_are_chinese(client: AsyncClient) -> None:
    """422 参数错误：统一响应包 + 中文提示。"""
    response = await client.post(
        "/api/projects", json={"name": "", "vehicleName": "Model Y", "renewalYear": 2026}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "参数校验失败" in body["message"]

    # 续保年份超出合理区间
    response = await client.post(
        "/api/projects", json={"name": "x", "vehicleName": "y", "renewalYear": 1999}
    )
    assert response.status_code == 422


async def test_get_missing_project_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/projects/99999")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "PROJECT_NOT_FOUND"


async def test_update_project_partial(client: AsyncClient) -> None:
    """PATCH 部分更新：只改名称，其他字段不受影响；可显式清空到期日。"""
    created = await _create_project(client)
    response = await client.patch(
        f"/api/projects/{created['id']}", json={"name": "2026 续保（更新）"}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "2026 续保（更新）"
    assert data["vehicleName"] == "Model Y"
    assert data["expireDate"] == "2026-05-31"

    # 显式传 null 清空可选日期
    response = await client.patch(
        f"/api/projects/{created['id']}", json={"expireDate": None}
    )
    assert response.status_code == 200
    assert response.json()["data"]["expireDate"] is None


async def test_delete_project_then_404(client: AsyncClient) -> None:
    """删除 → 再访问 404。"""
    created = await _create_project(client)
    response = await client.delete(f"/api/projects/{created['id']}")
    assert response.status_code == 200
    assert response.json()["message"] == "项目已删除"

    response = await client.get(f"/api/projects/{created['id']}")
    assert response.status_code == 404
    # 重复删除同样 404
    response = await client.delete(f"/api/projects/{created['id']}")
    assert response.status_code == 404


async def test_create_sanitizes_note(client: AsyncClient) -> None:
    """隐私边界：备注中的手机号在入库前统一脱敏，响应中不出现原文。"""
    created = await _create_project(client, note="保险员电话 13812345678，报价发微信")
    assert created["note"] == "保险员电话 [已脱敏:手机号]，报价发微信"
    assert "13812345678" not in created["note"]


async def test_update_sanitizes_free_text(client: AsyncClient) -> None:
    """编辑同样经过统一脱敏，不能绕过。"""
    created = await _create_project(client)
    response = await client.patch(
        f"/api/projects/{created['id']}",
        json={"vehicleName": "京A12345 的车"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["vehicleName"] == "[已脱敏:车牌] 的车"


async def test_list_aggregates_quote_count_and_min_net_payment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """列表聚合：报价数统计全部报价；最低净支出只统计已确认且非空的报价。"""
    created = await _create_project(client)
    project_id = created["id"]

    def build_quote(status: QuoteStatus, net_payment: Decimal | None) -> Quote:
        return Quote(
            project_id=project_id,
            insurer_code="PINGAN",
            insurer_name="平安",
            source=QuoteSource.UPLOADED,
            status=status,
            net_payment=net_payment,
            net_payment_status="OK" if net_payment is not None else "MISSING_TOTAL",
        )

    db_session.add_all(
        [
            # 已确认 5420 —— 应作为最低净支出
            build_quote(QuoteStatus.CONFIRMED, Decimal("5420.00")),
            # 草稿 100 —— 不计入最低净支出（未确认）
            build_quote(QuoteStatus.PENDING_CONFIRM, Decimal("100.00")),
            # 已确认但无净支出 —— 忽略
            build_quote(QuoteStatus.CONFIRMED, None),
        ]
    )
    await db_session.commit()

    response = await client.get("/api/projects")
    assert response.status_code == 200
    items = response.json()["data"]
    assert len(items) == 1
    item = items[0]
    assert item["quoteCount"] == 3
    assert item["minNetPayment"] == 5420.00


async def test_list_min_net_payment_null_when_no_confirmed(client: AsyncClient, db_session: AsyncSession) -> None:
    """只有草稿时 minNetPayment 为 null（稳定空状态，不是 0）。"""
    created = await _create_project(client)
    db_session.add(
        Quote(
            project_id=created["id"],
            insurer_code="PICC",
            insurer_name="人保",
            source=QuoteSource.MANUAL,
            status=QuoteStatus.DRAFT,
        )
    )
    await db_session.commit()

    items = (await client.get("/api/projects")).json()["data"]
    assert items[0]["quoteCount"] == 1
    assert items[0]["minNetPayment"] is None
