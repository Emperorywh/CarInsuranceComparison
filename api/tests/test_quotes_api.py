"""报价 API 集成测试（TASK-02 验证第 2 条）。

覆盖：手动报价从 PENDING_CONFIRM 到 CONFIRMED 的完整主路径、非法状态转换、
OTHER 缺公司名、金额精度、主险/保障包隔离、车辆摘要两种冲突选择与删除报价。
"""

from __future__ import annotations

from httpx import AsyncClient

# 复用项目测试里的创建助手，保证口径一致
from tests.test_projects_api import _create_project


async def _create_quote(
    client: AsyncClient,
    project_id: int,
    **overrides,
) -> dict:
    """创建手动报价（默认平安 + 无保险员）；断言 201 后返回 data。"""
    payload = {"insurerCode": "PINGAN", "source": "MANUAL"}
    payload.update(overrides)
    response = await client.post(f"/api/projects/{project_id}/quotes", json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == "OK"
    return body["data"]


async def _create_manual_project(client: AsyncClient) -> tuple[int, dict]:
    project = await _create_project(client)
    return project["id"], project


# ---- 创建与状态守卫 ----


async def test_create_manual_quote_starts_pending_confirm(client: AsyncClient) -> None:
    """MANUAL 创建即 PENDING_CONFIRM（决策 #16）；预置公司显示名取标准名。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id, agentName="小王")
    assert quote["status"] == "PENDING_CONFIRM"
    assert quote["source"] == "MANUAL"
    assert quote["insurerCode"] == "PINGAN"
    assert quote["insurerName"] == "平安"
    assert quote["agentName"] == "小王"
    # 空表单的稳定初始态：全部价格未知、净支出缺失（不得当 0）
    assert quote["commercialStatus"] == "UNKNOWN"
    assert quote["netPayment"] is None
    assert quote["netPaymentStatus"] == "MISSING_TOTAL"
    assert quote["totalCheckStatus"] == "NOT_CHECKABLE"
    assert quote["coverages"] == []


async def test_create_uploaded_quote_is_draft_container(client: AsyncClient) -> None:
    """UPLOADED 只创建 DRAFT 容器（上传流程由 TASK-03 接通）。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(
        client, project_id, insurerCode="PICC", source="UPLOADED"
    )
    assert quote["status"] == "DRAFT"


async def test_create_other_requires_free_name(client: AsyncClient) -> None:
    """OTHER 必须带自由输入公司名；缺名 422。"""
    project_id, _ = await _create_manual_project(client)
    response = await client.post(
        f"/api/projects/{project_id}/quotes",
        json={"insurerCode": "OTHER", "source": "MANUAL"},
    )
    assert response.status_code == 422
    assert "公司名称" in response.json()["message"]

    quote = await _create_quote(
        client, project_id, insurerCode="OTHER", insurerName="利宝保险", source="MANUAL"
    )
    assert quote["insurerCode"] == "OTHER"
    assert quote["insurerName"] == "利宝保险"


async def test_create_quote_unknown_insurer_code(client: AsyncClient) -> None:
    project_id, _ = await _create_manual_project(client)
    response = await client.post(
        f"/api/projects/{project_id}/quotes",
        json={"insurerCode": "NOT_A_COMPANY", "source": "MANUAL"},
    )
    assert response.status_code == 422


async def test_create_quote_missing_project_404(client: AsyncClient) -> None:
    response = await client.post(
        "/api/projects/99999/quotes", json={"insurerCode": "PICC", "source": "MANUAL"}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "PROJECT_NOT_FOUND"


async def test_editing_draft_quote_is_rejected(client: AsyncClient) -> None:
    """非法状态转换：DRAFT 容器不允许编辑（等待上传流程），返回 409。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id, source="UPLOADED")
    response = await client.patch(
        f"/api/quotes/{quote['id']}", json={"commercialPremium": "100"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "QUOTE_STATE_CONFLICT"


async def test_get_missing_quote_404(client: AsyncClient) -> None:
    response = await client.get("/api/quotes/99999")
    assert response.status_code == 404
    assert response.json()["code"] == "QUOTE_NOT_FOUND"


# ---- 价格分项不变量与精度 ----


async def test_price_value_implies_included(client: AsyncClient) -> None:
    """提供非空金额 → 状态自动 INCLUDED（值⟺INCLUDED 不变量）。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)
    response = await client.patch(
        f"/api/quotes/{quote['id']}",
        json={"compulsoryPremium": "1045", "vehicleTax": "0"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["compulsoryStatus"] == "INCLUDED"
    # 车船税 0 且 INCLUDED：新能源免征不能误判为未知（SPEC §12）
    assert data["vehicleTax"] == 0.0
    assert data["vehicleTaxStatus"] == "INCLUDED"


async def test_price_contradictory_payload_rejected(client: AsyncClient) -> None:
    """金额与“不包含”状态矛盾 → 422；仅标 INCLUDED 无值是合法中间态。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)

    response = await client.patch(
        f"/api/quotes/{quote['id']}",
        json={"commercialPremium": "100", "commercialStatus": "NOT_INCLUDED"},
    )
    assert response.status_code == 422
    assert "矛盾" in response.json()["message"]

    # 仅标“已包含”不填金额：允许保存（等计算值回填），确认时若无任何金额则阻断
    response = await client.patch(
        f"/api/quotes/{quote['id']}", json={"commercialStatus": "INCLUDED"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["commercialStatus"] == "INCLUDED"
    assert response.json()["data"]["commercialPremium"] is None


async def test_price_amount_precision_and_negative_rejected(client: AsyncClient) -> None:
    """金额精度：超过两位小数 422；负数 422（非负 + 两位小数）。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)
    response = await client.patch(
        f"/api/quotes/{quote['id']}", json={"commercialPremium": "4392.145"}
    )
    assert response.status_code == 422

    response = await client.patch(
        f"/api/quotes/{quote['id']}", json={"commercialPremium": "-1"}
    )
    assert response.status_code == 422


async def test_official_total_status_lifecycle(client: AsyncClient) -> None:
    """官方总价：填值 → INCLUDED；清空 → UNKNOWN（枚举只有两态）。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)
    qid = quote["id"]

    response = await client.patch(f"/api/quotes/{qid}", json={"officialTotal": "5785.14"})
    assert response.json()["data"]["officialTotalStatus"] == "INCLUDED"

    response = await client.patch(f"/api/quotes/{qid}", json={"officialTotal": None})
    data = response.json()["data"]
    assert data["officialTotal"] is None
    assert data["officialTotalStatus"] == "UNKNOWN"


# ---- 险种行：隔离、映射、座位规则 ----


async def test_coverage_rules_and_isolation(client: AsyncClient) -> None:
    """交强险不可作险种行；未知码 422；无码进未识别区；补码完成映射。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)
    qid = quote["id"]

    # 交强险只落价格分项（SPEC §3.1）
    response = await client.post(
        f"/api/quotes/{qid}/coverages",
        json={"code": "COMPULSORY", "rawName": "交强险", "premium": "1045"},
    )
    assert response.status_code == 422
    assert "交强险" in response.json()["message"]

    # 主险/保障包隔离：保障包专用类型码不得作为险种行 code（SPEC §6.5）
    response = await client.post(
        f"/api/quotes/{qid}/coverages",
        json={"code": "DRIVER_ACCIDENT", "rawName": "驾乘意外"},
    )
    assert response.status_code == 422

    # 未知险种码不得猜测类别
    response = await client.post(
        f"/api/quotes/{qid}/coverages", json={"code": "NOT_A_CODE", "rawName": "x"}
    )
    assert response.status_code == 422

    # 无码 → 未识别区（UNRECOGNIZED、显示名=原始名、用户录入标记）
    response = await client.post(
        f"/api/quotes/{qid}/coverages",
        json={"rawName": "车主权益包含驾乘", "premium": "66"},
    )
    assert response.status_code == 201
    row = response.json()["data"]["coverages"][0]
    assert row["category"] == "UNRECOGNIZED"
    assert row["code"] is None
    assert row["name"] == "车主权益包含驾乘"
    assert row["editedByUser"] is True
    assert row["confidenceLevel"] == "HIGH"

    # 未识别项手动映射：PATCH 补标准码后归类为附加险
    response = await client.patch(
        f"/api/quotes/{qid}/coverages/{row['id']}",
        json={"code": "TP_NON_MEDICAL", "coverageAmount": "500000"},
    )
    assert response.status_code == 200
    mapped = response.json()["data"]["coverages"][0]
    assert mapped["category"] == "ADDITIONAL"
    assert mapped["code"] == "TP_NON_MEDICAL"
    assert mapped["name"] == "三者医保外"


async def test_coverage_seat_total_rule(client: AsyncClient) -> None:
    """座位总额：未填总额自动推导；总额与“单座×座位”矛盾 422。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)
    qid = quote["id"]

    response = await client.post(
        f"/api/quotes/{qid}/coverages",
        json={
            "code": "PASSENGER_LIABILITY",
            "rawName": "乘客险",
            "perSeatAmount": "1000",
            "seatCount": 4,
            "premium": "100",
        },
    )
    assert response.status_code == 201
    assert response.json()["data"]["coverages"][0]["coverageAmount"] == 4000.0

    response = await client.post(
        f"/api/quotes/{qid}/coverages",
        json={
            "code": "PASSENGER_LIABILITY",
            "rawName": "乘客险2",
            "perSeatAmount": "1000",
            "seatCount": 4,
            "coverageAmount": "5000",
        },
    )
    assert response.status_code == 422
    assert "单座" in response.json()["message"]


async def test_coverage_cross_quote_access_404(client: AsyncClient) -> None:
    """跨报价访问明细按不存在处理，不泄露存在性。"""
    project_id, _ = await _create_manual_project(client)
    quote_a = await _create_quote(client, project_id)
    quote_b = await _create_quote(client, project_id)
    created = await client.post(
        f"/api/quotes/{quote_a['id']}/coverages",
        json={"code": "VEHICLE_LOSS", "rawName": "车损险", "premium": "1100"},
    )
    row_id = created.json()["data"]["coverages"][0]["id"]
    response = await client.delete(f"/api/quotes/{quote_b['id']}/coverages/{row_id}")
    assert response.status_code == 404
    assert response.json()["code"] == "QUOTE_DETAIL_NOT_FOUND"


# ---- 保障包与内部保障 ----


async def test_package_with_nested_coverages(client: AsyncClient) -> None:
    """保障包及其内部保障单事务创建；未知内部类型码 422。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)
    qid = quote["id"]

    response = await client.post(
        f"/api/quotes/{qid}/packages",
        json={
            "name": "车主尊享保障",
            "premium": "348",
            "coverages": [
                {
                    "type": "DRIVER_ACCIDENT",
                    "name": "驾乘意外身故及残疾",
                    "coverageAmount": "300000",
                    "multiplier": "2",
                    "condition": "LEGAL_HOLIDAY",
                },
                {"type": "SELF_PAID_MEDICAL", "name": "自费医疗", "coverageAmount": "30000"},
            ],
        },
    )
    assert response.status_code == 201
    package = response.json()["data"]["packages"][0]
    assert package["premium"] == 348.0
    assert len(package["coverages"]) == 2
    assert package["coverages"][0]["type"] == "DRIVER_ACCIDENT"
    assert package["coverages"][0]["editedByUser"] is True

    # 内部保障 type 必须来自 §3.3 码表
    response = await client.post(
        f"/api/quotes/{qid}/packages/{package['id']}/coverages",
        json={"type": "NOT_A_TYPE", "name": "x"},
    )
    assert response.status_code == 422


# ---- 完整手动路径：从空表单到确认 ----


async def _fill_manual_quote(client: AsyncClient, quote_id: int) -> dict:
    """按 SPEC §4.1 示例填写一份完整手动报价并确认，返回确认后的 data。"""
    # 价格分项：商业险留空由明细计算，交强 1045、车船税 0、保障包由包计算
    await client.patch(
        f"/api/quotes/{quote_id}",
        json={
            "compulsoryPremium": "1045",
            "vehicleTax": "0",
            "otherFeesStatus": "NOT_INCLUDED",
            "officialTotal": "5785.14",
            "vehicleModel": "Model Y",
            "vehicleSeats": 5,
            "firstRegDate": "2022-05",
            "isNev": True,
        },
    )
    # 基础车险 + 附加险
    await client.post(
        f"/api/quotes/{quote_id}/coverages",
        json={
            "code": "THIRD_PARTY_LIABILITY",
            "rawName": "三者险",
            "coverageAmount": "3000000",
            "premium": "1237.41",
        },
    )
    await client.post(
        f"/api/quotes/{quote_id}/coverages",
        json={
            "code": "VEHICLE_LOSS",
            "rawName": "车损险",
            "coverageAmount": "147719.12",
            "premium": "1100",
        },
    )
    await client.post(
        f"/api/quotes/{quote_id}/coverages",
        json={
            "code": "DRIVER_LIABILITY",
            "rawName": "司机险",
            "coverageAmount": "10000",
            "premium": "50",
        },
    )
    await client.post(
        f"/api/quotes/{quote_id}/coverages",
        json={
            "code": "PASSENGER_LIABILITY",
            "rawName": "乘客险",
            "perSeatAmount": "1000",
            "seatCount": 4,
            "premium": "100",
        },
    )
    await client.post(
        f"/api/quotes/{quote_id}/coverages",
        json={
            "code": "TP_NON_MEDICAL",
            "rawName": "三者医保外",
            "coverageAmount": "500000",
            "premium": "36.50",
        },
    )
    # 增值服务（明确 0 元 → FREE）
    await client.post(
        f"/api/quotes/{quote_id}/services",
        json={"serviceType": "ROAD_RESCUE", "status": "FREE", "count": 2, "cost": "0"},
    )
    # 独立保障包
    await client.post(
        f"/api/quotes/{quote_id}/packages",
        json={
            "name": "车主尊享保障",
            "premium": "348",
            "coverages": [{"type": "DRIVER_ACCIDENT", "coverageAmount": "300000"}],
        },
    )
    # 销售说明
    await client.post(
        f"/api/quotes/{quote_id}/annotations",
        json={"content": "节假日90万 100%赔付", "kind": "HANDWRITTEN"},
    )
    # 优惠：300 现金计入 + 无折现服务权益（勾选也不减钱）
    await client.post(
        f"/api/quotes/{quote_id}/discounts",
        json={
            "discountType": "CASH",
            "description": "返现",
            "amount": "300",
            "cashEquivalent": "300",
            "includeInNet": True,
        },
    )
    await client.post(
        f"/api/quotes/{quote_id}/discounts",
        json={
            "discountType": "SERVICE",
            "description": "洗车5次",
            "amount": "200",
            "includeInNet": True,
        },
    )
    response = await client.post(f"/api/quotes/{quote_id}/confirm", json={})
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_full_manual_path_to_confirmed(client: AsyncClient) -> None:
    """完整主路径：空表单 → 全层录入 → 确认 → CONFIRMED，价格逐项断言。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id, agentName="小李")

    data = await _fill_manual_quote(client, quote["id"])

    assert data["status"] == "CONFIRMED"
    # computedCommercialPremium：1237.41 + 1100 + 50 + 100 + 36.50 = 2523.91
    assert data["computedCommercialPremium"] == 2523.91
    assert data["computedPackageTotal"] == 348.0
    # computedTotal = 2523.91 + 1045 + 0 + 348 + 0 = 3916.91
    assert data["computedTotal"] == 3916.91
    # 官方 5785.14 与系统计算差异大 → MISMATCH（保留两者，仅提示）
    assert data["totalCheckStatus"] == "MISMATCH"
    assert data["officialTotal"] == 5785.14
    # 净支出 = 5785.14 − 300（服务权益无折现值不减钱）
    assert data["netPayment"] == 5485.14
    assert data["netPaymentStatus"] == "OK"

    # 确认后回填项目车辆摘要（首次确认）
    project = (await client.get(f"/api/projects/{project_id}")).json()["data"]
    assert project["vehicleModel"] == "Model Y"
    assert project["vehicleSeats"] == 5
    assert project["isNev"] is True
    assert project["firstRegDate"] == "2022-05"

    # 确认后的报价仍可编辑（CONFIRMED --任何编辑--> 仍是 CONFIRMED）
    response = await client.patch(f"/api/quotes/{quote['id']}", json={"note": "已和保险员谈好"})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "CONFIRMED"


async def test_confirm_twice_is_state_conflict(client: AsyncClient) -> None:
    """非法状态转换：已确认报价再次确认返回 409。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)
    await _fill_manual_quote(client, quote["id"])
    response = await client.post(f"/api/quotes/{quote['id']}/confirm", json={})
    assert response.status_code == 409


async def test_confirm_blocks_included_without_amount(client: AsyncClient) -> None:
    """确认阻断：INCLUDED 但值与计算值皆缺 → 422 并点名分项。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)
    qid = quote["id"]

    # 商业险标为包含但既无用户金额也无明细（无计算值）
    await client.patch(f"/api/quotes/{qid}", json={"commercialStatus": "INCLUDED"})
    # 保障包标为包含但没有任何包
    await client.patch(f"/api/quotes/{qid}", json={"packageStatus": "INCLUDED"})

    response = await client.post(f"/api/quotes/{qid}/confirm", json={})
    assert response.status_code == 422
    message = response.json()["message"]
    assert "商业险" in message and "独立保障包" in message

    # 修正为“未知”后即可确认（UNKNOWN 合法，只是总额不可计算）
    await client.patch(
        f"/api/quotes/{qid}",
        json={"commercialStatus": "UNKNOWN", "packageStatus": "UNKNOWN"},
    )
    response = await client.post(f"/api/quotes/{qid}/confirm", json={})
    assert response.status_code == 200


async def test_confirm_with_unrecognized_money_item_blocks_commercial(client: AsyncClient) -> None:
    """含金额的未识别项：用户处理前阻断商业险计算（computed 保持 null）。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)
    qid = quote["id"]

    # 未识别含金额 → computedCommercialPremium 为 null（直到映射或丢弃）
    await client.post(
        f"/api/quotes/{qid}/coverages",
        json={"code": "VEHICLE_LOSS", "rawName": "车损险", "premium": "1100"},
    )
    created = await client.post(
        f"/api/quotes/{qid}/coverages", json={"rawName": "神秘附加权益", "premium": "66"}
    )
    data = created.json()["data"]
    assert data["computedCommercialPremium"] is None

    # 用户“丢弃”未识别项（删除）后恢复计算
    row_id = [c for c in data["coverages"] if c["category"] == "UNRECOGNIZED"][0]["id"]
    response = await client.delete(f"/api/quotes/{qid}/coverages/{row_id}")
    assert response.json()["data"]["computedCommercialPremium"] == 1100.0

    # 未识别项映射为标准险种后同样恢复计算
    created = await client.post(
        f"/api/quotes/{qid}/coverages", json={"rawName": "附加权益2", "premium": "50"}
    )
    data = created.json()["data"]
    row_id = [c for c in data["coverages"] if c["category"] == "UNRECOGNIZED"][0]["id"]
    response = await client.patch(
        f"/api/quotes/{qid}/coverages/{row_id}", json={"code": "SCRATCH"}
    )
    assert response.json()["data"]["computedCommercialPremium"] == 1150.0


# ---- 车辆摘要冲突两种选择 ----


async def test_vehicle_conflict_requires_explicit_resolution(client: AsyncClient) -> None:
    """冲突未选择 → 422 VEHICLE_CONFLICT_UNRESOLVED；两种选择行为正确。"""
    project_id, _ = await _create_manual_project(client)
    first = await _create_quote(client, project_id)
    await client.patch(
        f"/api/quotes/{first['id']}",
        json={"vehicleModel": "Model Y", "vehicleSeats": 5, "isNev": True},
    )
    await client.post(f"/api/quotes/{first['id']}/confirm", json={})

    second = await _create_quote(client, project_id)
    await client.patch(
        f"/api/quotes/{second['id']}",
        json={"vehicleModel": "汉EV", "vehicleSeats": 5, "isNev": True},
    )
    # GET 时能读到冲突信息（确认页据此渲染选择 UI）
    detail = (await client.get(f"/api/quotes/{second['id']}")).json()["data"]
    assert detail["vehicleConflict"]["resolutionRequired"] is True
    assert "vehicleModel" in detail["vehicleConflict"]["fields"]

    # 未选择 → 阻断
    response = await client.post(f"/api/quotes/{second['id']}/confirm", json={})
    assert response.status_code == 422
    assert response.json()["code"] == "VEHICLE_CONFLICT_UNRESOLVED"

    # 以项目为准：保留摘要，报价快照不变
    response = await client.post(
        f"/api/quotes/{second['id']}/confirm", json={"vehicleConflictResolution": "KEEP_PROJECT"}
    )
    assert response.status_code == 200
    project = (await client.get(f"/api/projects/{project_id}")).json()["data"]
    assert project["vehicleModel"] == "Model Y"
    snapshot = (await client.get(f"/api/quotes/{second['id']}")).json()["data"]
    assert snapshot["vehicleModel"] == "汉EV"


async def test_vehicle_conflict_use_quote_overwrites_summary(client: AsyncClient) -> None:
    """以报价为准：冲突字段覆盖项目摘要（含 isNev 冲突）。"""
    project_id, _ = await _create_manual_project(client)
    first = await _create_quote(client, project_id)
    await client.patch(
        f"/api/quotes/{first['id']}", json={"vehicleModel": "Model Y", "isNev": True}
    )
    await client.post(f"/api/quotes/{first['id']}/confirm", json={})

    second = await _create_quote(client, project_id)
    await client.patch(
        f"/api/quotes/{second['id']}", json={"vehicleModel": "汉EV", "isNev": False}
    )
    response = await client.post(
        f"/api/quotes/{second['id']}/confirm", json={"vehicleConflictResolution": "USE_QUOTE"}
    )
    assert response.status_code == 200
    project = (await client.get(f"/api/projects/{project_id}")).json()["data"]
    assert project["vehicleModel"] == "汉EV"
    assert project["isNev"] is False


async def test_first_reg_date_only_warns(client: AsyncClient) -> None:
    """初登日期差异只提示不阻断：无冲突字段时可直接确认。"""
    project_id, _ = await _create_manual_project(client)
    first = await _create_quote(client, project_id)
    await client.patch(f"/api/quotes/{first['id']}", json={"firstRegDate": "2022-05"})
    await client.post(f"/api/quotes/{first['id']}/confirm", json={})

    second = await _create_quote(client, project_id)
    await client.patch(f"/api/quotes/{second['id']}", json={"firstRegDate": "2021-01"})
    detail = (await client.get(f"/api/quotes/{second['id']}")).json()["data"]
    assert detail["vehicleConflict"]["firstRegDateDiffers"] is True
    assert detail["vehicleConflict"]["resolutionRequired"] is False
    response = await client.post(f"/api/quotes/{second['id']}/confirm", json={})
    assert response.status_code == 200


# ---- 优惠与净支出 ----


async def test_discount_over_amount_invalid_then_recovers(client: AsyncClient) -> None:
    """优惠超额：INVALID_DISCOUNT + netPayment=null；修正后回到 OK，不截断为 0。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)
    qid = quote["id"]
    await client.patch(f"/api/quotes/{qid}", json={"officialTotal": "1000"})

    created = await client.post(
        f"/api/quotes/{qid}/discounts",
        json={
            "discountType": "GIFT_CARD",
            "amount": "9999",
            "cashEquivalent": "9999",
            "includeInNet": True,
        },
    )
    data = created.json()["data"]
    assert data["netPayment"] is None
    assert data["netPaymentStatus"] == "INVALID_DISCOUNT"

    # 修正优惠后重新计算回到 OK
    discount_id = data["discounts"][0]["id"]
    response = await client.patch(
        f"/api/quotes/{qid}/discounts/{discount_id}",
        json={"cashEquivalent": "200"},
    )
    data = response.json()["data"]
    assert data["netPayment"] == 800.0
    assert data["netPaymentStatus"] == "OK"


async def test_discount_boundary_equals_base_is_zero(client: AsyncClient) -> None:
    """边界：折现合计恰好等于总价 → 净支出 0 而非超额。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)
    await client.patch(f"/api/quotes/{quote['id']}", json={"officialTotal": "1000"})
    response = await client.post(
        f"/api/quotes/{quote['id']}/discounts",
        json={
            "discountType": "CASH",
            "cashEquivalent": "1000",
            "includeInNet": True,
        },
    )
    data = response.json()["data"]
    assert data["netPayment"] == 0.0
    assert data["netPaymentStatus"] == "OK"


# ---- 项目详情分组卡片 ----


async def test_project_detail_groups_quotes_by_insurer_and_agent(client: AsyncClient) -> None:
    """分组展示：同「公司+保险员」一组并提示同来源；不同保险员分组。"""
    project_id, _ = await _create_manual_project(client)
    # 平安 + 小王 ×2（同组、同来源提示）；平安 + 小李 ×1；人保 ×1
    await _create_quote(client, project_id, insurerCode="PINGAN", agentName="小王")
    await _create_quote(client, project_id, insurerCode="PINGAN", agentName="小王")
    await _create_quote(client, project_id, insurerCode="PINGAN", agentName="小李")
    await _create_quote(client, project_id, insurerCode="PICC", agentName=None)

    detail = (await client.get(f"/api/projects/{project_id}")).json()["data"]
    groups = detail["quoteGroups"]
    assert len(groups) == 3
    pingan_wang = next(g for g in groups if g["agentName"] == "小王")
    assert pingan_wang["insurerName"] == "平安"
    assert len(pingan_wang["quotes"]) == 2
    assert pingan_wang["sameSourceHint"] is True
    pingan_li = next(g for g in groups if g["agentName"] == "小李")
    assert pingan_li["sameSourceHint"] is False
    picc = next(g for g in groups if g["insurerCode"] == "PICC")
    assert picc["agentName"] is None


async def test_project_detail_card_summaries(client: AsyncClient) -> None:
    """卡片摘要：净支出、总额异常标记与三者/医保外摘要。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)
    await _fill_manual_quote(client, quote["id"])

    detail = (await client.get(f"/api/projects/{project_id}")).json()["data"]
    card = detail["quoteGroups"][0]["quotes"][0]
    assert card["status"] == "CONFIRMED"
    assert card["netPayment"] == 5485.14
    assert card["totalCheckStatus"] == "MISMATCH"  # 官方总价异常必须可见
    assert card["officialTotal"] == 5785.14
    assert card["thirdPartyAmount"] == 3000000.0
    assert card["tpNonMedicalAmount"] == 500000.0
    assert card["source"] == "MANUAL"


async def test_project_min_net_payment_excludes_invalid_discount(client: AsyncClient) -> None:
    """优惠超额的报价不显示为最低价（netPayment=null 不参与聚合）。"""
    project_id, _ = await _create_manual_project(client)
    good = await _create_quote(client, project_id)
    await client.patch(f"/api/quotes/{good['id']}", json={"officialTotal": "5000"})
    await client.post(f"/api/quotes/{good['id']}/confirm", json={})

    bad = await _create_quote(client, project_id)
    await client.patch(f"/api/quotes/{bad['id']}", json={"officialTotal": "100"})
    await client.post(
        f"/api/quotes/{bad['id']}/discounts",
        json={"discountType": "CASH", "cashEquivalent": "999", "includeInNet": True},
    )
    await client.post(f"/api/quotes/{bad['id']}/confirm", json={})

    items = (await client.get("/api/projects")).json()["data"]
    assert items[0]["quoteCount"] == 2
    assert items[0]["minNetPayment"] == 5000.0


# ---- 删除与脱敏 ----


async def test_delete_quote_cascades_details(client: AsyncClient) -> None:
    """删除报价：明细级联删除，重复删除 404。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(client, project_id)
    await _fill_manual_quote(client, quote["id"])

    response = await client.delete(f"/api/quotes/{quote['id']}")
    assert response.status_code == 200
    assert response.json()["message"] == "报价已删除"

    response = await client.get(f"/api/quotes/{quote['id']}")
    assert response.status_code == 404
    # 项目仍在，只是少了一份报价
    detail = (await client.get(f"/api/projects/{project_id}")).json()["data"]
    assert detail["quoteGroups"] == []


async def test_quote_free_text_sanitized(client: AsyncClient) -> None:
    """隐私边界：公司名/保险员/险种描述中的手机号统一脱敏。"""
    project_id, _ = await _create_manual_project(client)
    quote = await _create_quote(
        client,
        project_id,
        insurerCode="OTHER",
        insurerName="利宝保险",
        agentName="王经理 13812345678",
    )
    assert quote["agentName"] == "王经理 [已脱敏:手机号]"

    response = await client.post(
        f"/api/quotes/{quote['id']}/coverages",
        json={"code": "VEHICLE_LOSS", "rawName": "车损险 京A12345"},
    )
    assert "京A12345" not in response.json()["data"]["coverages"][0]["rawName"]


# ---- 字典端点 ----


async def test_dictionaries_endpoint(client: AsyncClient) -> None:
    """单一代码来源：公司 9 项（预置 8 + 其他）、交强险不可作为险种行。"""
    response = await client.get("/api/dictionaries")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["insurers"]) == 9
    codes = {item["code"] for item in data["insurers"]}
    assert {"PICC", "PINGAN", "CPIC", "CHINALIFE_PC", "GUOYUAN", "OTHER"} <= codes
    compulsory = next(
        item for item in data["coverageCodes"] if item["code"] == "COMPULSORY"
    )
    assert compulsory["rowSelectable"] is False
    assert data["statusLabels"]["quoteStatus"]["CONFIRMED"] == "已确认"
    # 主险/保障包隔离：两套类型码不相交
    coverage_codes = {item["code"] for item in data["coverageCodes"]}
    package_types = {item["code"] for item in data["packageCoverageTypes"]}
    assert coverage_codes & package_types == set()
