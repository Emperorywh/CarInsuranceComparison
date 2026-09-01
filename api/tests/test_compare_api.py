"""对比 API 集成测试（TASK-06 验证 2/3/5）。

覆盖：
- 数量/格式/重复/归属/状态校验的语义化错误；
- 手动报价从创建到确认再到对比的主路径（用户传入顺序、两种基准、单一总表结构）；
- MERGE_REVIEW 读取旧确认值、候选 merge_change 不泄漏；
- 6 报价 × 200 明细的性能口径（预热后 P95 < 500ms，打印机器/数据库条件）。
"""

from __future__ import annotations

import platform
import time
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    MergeChange,
    PackageCoverage,
    ParseTask,
    Quote,
    QuoteCoverage,
    QuoteService,
    SupplementalPackage,
)
from app.models.enums import (
    ItemStatus,
    MergeChangeKind,
    MergeResolution,
    NetPaymentStatus,
    OfficialTotalStatus,
    ParseTaskStatus,
    PriceItemStatus,
    QuoteStatus,
    TotalCheckStatus,
)
from tests.test_projects_api import _create_project
from tests.test_quotes_api import _create_quote

# ---- 数据构造助手 ----


async def _fill_complete_prices(client: AsyncClient, quote_id: int, **price_overrides) -> None:
    """把五个价格分项填成“明确包含/明确不包含”并给官方总价（可确认状态）。"""
    payload = {
        "commercialPremium": "3000.00",
        "compulsoryPremium": "950.00",
        "vehicleTax": "0.00",
        "packageTotal": "348.00",
        "officialTotal": "4298.00",
        "otherFeesStatus": "NOT_INCLUDED",
    }
    payload.update(price_overrides)
    response = await client.patch(f"/api/quotes/{quote_id}", json=payload)
    assert response.status_code == 200, response.text


async def _add_core_coverages(
    client: AsyncClient, quote_id: int, *, tp_amount: str = "3000000"
) -> None:
    """添加商业四大主险 + 三者医保外（手动录入口径）。"""
    rows: list[dict] = [
        {"code": "VEHICLE_LOSS", "rawName": "新能源汽车损失保险", "coverageAmount": "147719.12", "premium": "1477.19"},
        {"code": "THIRD_PARTY_LIABILITY", "rawName": "第三者责任险", "coverageAmount": tp_amount, "premium": "1237.41"},
        {"code": "DRIVER_LIABILITY", "rawName": "车上人员责任险（司机）", "coverageAmount": "10000", "premium": "500"},
        {
            "code": "PASSENGER_LIABILITY",
            "rawName": "车上人员责任险（乘客）",
            "perSeatAmount": "10000",
            "seatCount": 4,
            "premium": "2000",
        },
        {"code": "TP_NON_MEDICAL", "rawName": "附加医保外医疗费用责任险（第三者）", "coverageAmount": "500000", "premium": "36.5"},
    ]
    for row in rows:
        response = await client.post(f"/api/quotes/{quote_id}/coverages", json=row)
        assert response.status_code == 201, response.text  # 创建资源返回 201


async def _create_confirmed_quote(
    client: AsyncClient, project_id: int, **quote_overrides
) -> dict:
    """手动报价主链路：创建 → 填价格/险种 → 确认，返回确认后的 data。"""
    quote = await _create_quote(client, project_id, **quote_overrides)
    quote_id = quote["id"]
    await _fill_complete_prices(client, quote_id)
    await _add_core_coverages(client, quote_id)
    confirmed = await client.post(f"/api/quotes/{quote_id}/confirm", json={})
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["data"]


# ---- 参数与状态校验（验证 3）----


async def test_compare_missing_project_404(client: AsyncClient) -> None:
    response = await client.get(
        "/api/projects/99999/compare", params={"quoteIds": "1,2"}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "PROJECT_NOT_FOUND"


async def test_compare_requires_two_quotes(client: AsyncClient) -> None:
    project_id = (await _create_project(client))["id"]
    quote = await _create_quote(client, project_id)
    response = await client.get(
        f"/api/projects/{project_id}/compare", params={"quoteIds": str(quote["id"])}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "COMPARE_TOO_FEW"


async def test_compare_rejects_more_than_six(client: AsyncClient) -> None:
    project_id = (await _create_project(client))["id"]
    ids = [str((await _create_quote(client, project_id))["id"]) for _ in range(7)]
    response = await client.get(
        f"/api/projects/{project_id}/compare", params={"quoteIds": ",".join(ids)}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "COMPARE_TOO_MANY"
    assert "分批" in response.json()["message"]


async def test_compare_rejects_duplicate_ids(client: AsyncClient) -> None:
    project_id = (await _create_project(client))["id"]
    quote = await _create_quote(client, project_id)
    response = await client.get(
        f"/api/projects/{project_id}/compare",
        params={"quoteIds": f"{quote['id']},{quote['id']}"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "COMPARE_QUOTES_DUPLICATED"


async def test_compare_rejects_malformed_ids(client: AsyncClient) -> None:
    project_id = (await _create_project(client))["id"]
    response = await client.get(
        f"/api/projects/{project_id}/compare", params={"quoteIds": "1,abc"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "COMPARE_QUOTES_INVALID"


async def test_compare_rejects_quote_of_other_project(client: AsyncClient) -> None:
    project_id = (await _create_project(client))["id"]
    other_project_id = (await _create_project(client, name="另一个项目"))["id"]
    foreign = await _create_quote(client, other_project_id)
    # 本项目报价需先确认（归属校验按逐报价顺序执行，状态同样非法时会先命中）
    own = await _create_confirmed_quote(client, project_id)
    response = await client.get(
        f"/api/projects/{project_id}/compare",
        params={"quoteIds": f"{own['id']},{foreign['id']}"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "QUOTE_NOT_IN_PROJECT"


async def test_compare_rejects_non_comparable_status(client: AsyncClient) -> None:
    """DRAFT（上传容器）等非法状态不可对比（422 QUOTE_NOT_COMPARABLE）。"""
    project_id = (await _create_project(client))["id"]
    draft = await _create_quote(client, project_id, source="UPLOADED")
    confirmed = await _create_confirmed_quote(client, project_id)
    response = await client.get(
        f"/api/projects/{project_id}/compare",
        params={"quoteIds": f"{confirmed['id']},{draft['id']}"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "QUOTE_NOT_COMPARABLE"


# ---- 主路径：手动确认报价的对比（验证 5 的接口口径）----


async def test_compare_main_path_two_confirmed_quotes(client: AsyncClient) -> None:
    project_id = (await _create_project(client))["id"]
    first = await _create_confirmed_quote(client, project_id, planLabel="方案A")
    second = await _create_confirmed_quote(
        client,
        project_id,
        insurerCode="PINGAN",
        planLabel="方案B",
    )
    # 方案B 三者 500 万、商业险更贵 → 差异行有内容
    await client.patch(
        f"/api/quotes/{second['id']}",
        json={"commercialPremium": "3300.00", "officialTotal": "4598.00"},
    )
    await _add_tp_bigger(client, second["id"])

    response = await client.get(
        f"/api/projects/{project_id}/compare",
        params={"quoteIds": f"{first['id']},{second['id']}"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    # 用户传入顺序保持；差异基准=第一个，价格基准=更便宜的 A
    assert [q["quoteId"] for q in data["quotes"]] == [first["id"], second["id"]]
    assert data["quotes"][0]["isDiffBaseline"] is True
    assert data["quotes"][0]["isPriceBaseline"] is True
    assert data["quotes"][1]["isPriceBaseline"] is False
    assert data["diffBaselineQuoteId"] == first["id"]

    # 单一总表：三者保额差异行存在且为 UP（500 万 > 300 万）
    tp_row = next(r for r in data["rows"] if r["key"] == "THIRD_PARTY_LIABILITY:amount")
    assert tp_row["diff"] is True
    assert tp_row["cells"][1]["tag"] == "UP"
    assert tp_row["cells"][1]["text"] == "500 万"
    # 价格分组净支出行在总表中
    net_row = next(r for r in data["rows"] if r["key"] == "net")
    assert net_row["cells"][0]["text"].startswith("¥")

    # 免责声明随服务端下发
    assert data["disclaimer"].startswith("本工具")


async def _add_tp_bigger(client: AsyncClient, quote_id: int) -> None:
    """给方案B 额外加一条 500 万三者（同码两行取代表行 → 保额变 500 万）。"""
    response = await client.post(
        f"/api/quotes/{quote_id}/coverages",
        json={
            "code": "THIRD_PARTY_LIABILITY",
            "rawName": "机动车第三者责任保险",
            "coverageAmount": "5000000",
            "premium": "2000",
        },
    )
    assert response.status_code == 201, response.text


async def test_compare_three_quotes_preserves_order_and_ranks(client: AsyncClient) -> None:
    """3 个报价：勾选顺序、价格排序与标注互不干扰。"""
    project_id = (await _create_project(client))["id"]
    a = await _create_confirmed_quote(client, project_id, planLabel="A")
    b = await _create_confirmed_quote(client, project_id, planLabel="B")
    c = await _create_confirmed_quote(client, project_id, planLabel="C")
    # B 最便宜；C 分项全部未知且无官方总价 → MISSING_TOTAL 排最后
    await client.patch(f"/api/quotes/{b['id']}", json={"officialTotal": "3998.00"})
    await client.patch(
        f"/api/quotes/{c['id']}",
        json={
            "commercialStatus": "UNKNOWN",
            "compulsoryStatus": "UNKNOWN",
            "vehicleTaxStatus": "UNKNOWN",
            "packageStatus": "UNKNOWN",
            "officialTotal": None,
        },
    )
    response = await client.get(
        f"/api/projects/{project_id}/compare",
        params={"quoteIds": f"{a['id']},{b['id']},{c['id']}"},
    )
    data = response.json()["data"]
    # 传入顺序 = A,B,C；价格排序 = B,A,C（C 总价缺失排最后）
    assert [q["quoteId"] for q in data["quotes"]] == [a["id"], b["id"], c["id"]]
    assert [e["quoteId"] for e in data["priceOrder"]] == [b["id"], a["id"], c["id"]]
    assert any("总价缺失" in ann for ann in data["quotes"][2]["annotations"])
    # 价格基准 = 最便宜的 B
    b_meta = next(q for q in data["quotes"] if q["quoteId"] == b["id"])
    assert b_meta["isPriceBaseline"] is True


# ---- MERGE_REVIEW 使用旧确认值（验证 3）----


async def test_merge_review_reads_confirmed_values_and_hides_candidates(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """MERGE_REVIEW 对比读取旧值；PENDING merge_change 不改变结果。"""
    project_id = (await _create_project(client))["id"]
    quote = await _create_confirmed_quote(client, project_id)
    other = await _create_confirmed_quote(client, project_id)
    before = (
        await client.get(
            f"/api/projects/{project_id}/compare",
            params={"quoteIds": f"{quote['id']},{other['id']}"},
        )
    ).json()["data"]

    # 进入 MERGE_REVIEW 并种入一条 PENDING 候选变更（模拟补传解析完成；
    # 候选值 999999 绝不能出现在对比结果里）
    quote_row = (
        (await db_session.execute(select(Quote).where(Quote.id == quote["id"])))
        .scalars()
        .one()
    )
    quote_row.status = QuoteStatus.MERGE_REVIEW
    task = ParseTask(
        project_id=project_id,
        quote_id=quote["id"],
        status=ParseTaskStatus.SUCCEEDED,
        provider="fake",
        model="fake-model",
        attempt=1,
    )
    db_session.add(task)
    await db_session.flush()
    db_session.add(
        MergeChange(
            quote_id=quote["id"],
            parse_task_id=task.id,
            entity_type="scalar",
            entity_key="officialTotal",
            field_name="officialTotal",
            old_value={"value": 4298.0},
            new_value={"value": 999999},
            kind=MergeChangeKind.CONFLICT,
            resolution=MergeResolution.PENDING,
        )
    )
    await db_session.flush()

    after = (
        await client.get(
            f"/api/projects/{project_id}/compare",
            params={"quoteIds": f"{quote['id']},{other['id']}"},
        )
    ).json()["data"]
    # MERGE_REVIEW 状态可对比，且读取的旧值与之前完全一致（整表逐行同值）
    def row_values(payload: dict) -> list:
        return [(r["key"], [c["value"] for c in r["cells"]]) for r in payload["rows"]]

    assert row_values(after) == row_values(before)
    assert any("合并确认中" in ann for ann in after["quotes"][0]["annotations"])
    # 候选值 999999 不泄漏
    assert "999999" not in repr(after)


# ---- 性能口径（验证 2）：6 报价 × 200 明细，P95 < 500ms ----


async def _seed_heavy_quotes(
    client: AsyncClient, db_session: AsyncSession, project_id: int, count: int = 6
) -> list[int]:
    """直接以 ORM 批量插入 CONFIRMED 报价（每份 200 险种 + 20 服务 + 5×10 包内保障）。"""
    quote_ids: list[int] = []
    for index in range(count):
        quote = Quote(
            project_id=project_id,
            insurer_code="PICC",
            insurer_name="人保",
            plan_label=f"压测方案{index + 1}",
            source="MANUAL",
            status=QuoteStatus.CONFIRMED,
            commercial_premium=Decimal("3000.00"),
            commercial_status=PriceItemStatus.INCLUDED,
            compulsory_premium=Decimal("950.00"),
            compulsory_status=PriceItemStatus.INCLUDED,
            vehicle_tax=Decimal("0"),
            vehicle_tax_status=PriceItemStatus.INCLUDED,
            package_total=Decimal("348.00"),
            package_status=PriceItemStatus.INCLUDED,
            other_fees_status=PriceItemStatus.NOT_INCLUDED,
            official_total=Decimal(f"{5000 + index}.00"),
            official_total_status=OfficialTotalStatus.INCLUDED,
            computed_total=Decimal("4298.00"),
            total_check_status=TotalCheckStatus.PASSED,
            net_payment=Decimal(f"{5000 + index}.00"),
            net_payment_status=NetPaymentStatus.OK,
            computed_commercial_premium=Decimal("3977.19"),
        )
        db_session.add(quote)
        await db_session.flush()
        quote_ids.append(quote.id)
        # 200 条险种明细：前 4 条为主险（保额完整），其余为附加险
        codes = ["VEHICLE_LOSS", "THIRD_PARTY_LIABILITY", "DRIVER_LIABILITY", "PASSENGER_LIABILITY"]
        coverage_rows = [
            {
                "quote_id": quote.id,
                "code": codes[i] if i < 4 else None,
                "category": "CORE" if i < 4 else "ADDITIONAL",
                "raw_name": f"附加险{i}",
                "raw_value": None,
                "name": f"附加险{i}",
                "status": ItemStatus.INCLUDED.value,
                "coverage_amount": Decimal("100000"),
                "premium": Decimal("50.00"),
                "confidence_level": "HIGH",
                "edited_by_user": False,
            }
            for i in range(200)
        ]
        await db_session.execute(insert(QuoteCoverage).values(coverage_rows))
        # 20 条服务行（同类型多行取代表行的归并逻辑也要跑）
        await db_session.execute(
            insert(QuoteService).values(
                [
                    {
                        "quote_id": quote.id,
                        "service_type": "ROAD_RESCUE",
                        "status": ItemStatus.FREE.value,
                        "count": 2,
                        "cost": Decimal("0"),
                        "confidence_level": "HIGH",
                        "edited_by_user": False,
                    }
                ]
                * 20
            )
        )
        # 5 个保障包 × 10 条内部保障
        for p in range(5):
            package = SupplementalPackage(
                quote_id=quote.id,
                name=f"保障包{p}",
                premium=Decimal("100.00"),
                confidence_level="HIGH",
                edited_by_user=False,
            )
            db_session.add(package)
            await db_session.flush()
            await db_session.execute(
                insert(PackageCoverage).values(
                    [
                        {
                            "package_id": package.id,
                            "type": "DRIVER_ACCIDENT",
                            "name": f"驾乘意外{c}",
                            "status": ItemStatus.INCLUDED.value,
                            "coverage_amount": Decimal("300000"),
                            "unit": "CNY",
                            "confidence_level": "HIGH",
                            "edited_by_user": False,
                        }
                        for c in range(10)
                    ]
                )
            )
    await db_session.flush()
    return quote_ids


@pytest.mark.parametrize("runs", [12])
async def test_compare_performance_p95_under_500ms(
    client: AsyncClient, db_session: AsyncSession, runs: int
) -> None:
    """6 报价、每份 200+ 条明细：预热后多次测量，P95 < 500ms（SPEC §13）。"""
    project_id = (await _create_project(client))["id"]
    quote_ids = await _seed_heavy_quotes(client, db_session, project_id)
    url = f"/api/projects/{project_id}/compare"
    params = {"quoteIds": ",".join(str(qid) for qid in quote_ids)}

    # 预热：首次请求含连接与计划缓存冷启动，不计入统计
    warm = await client.get(url, params=params)
    assert warm.status_code == 200, warm.text

    times: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        response = await client.get(url, params=params)
        elapsed = time.perf_counter() - start
        assert response.status_code == 200
        times.append(elapsed)
    times.sort()
    p95 = times[int(round(0.95 * (runs - 1)))]
    # 报告机器/数据库条件（TASK-06 验证 2 要求记录）
    print(
        f"\n[性能] platform={platform.platform()} python={platform.python_version()} "
        f"db=embedded-postgres-17 runs={runs} p95={p95 * 1000:.1f}ms "
        f"min={times[0] * 1000:.1f}ms max={times[-1] * 1000:.1f}ms"
    )
    assert p95 < 0.5, f"对比接口 P95={p95 * 1000:.1f}ms 超过 500ms 口径"
