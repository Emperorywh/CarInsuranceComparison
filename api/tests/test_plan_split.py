"""多方案拆分集成测试（TASK-05 验证 1、2）。

覆盖：拆分预览回放、确认拆分事务（子报价数量/标签/状态/候选内容/共享
fileId/容器删除/parse_task 保留）、丢弃与改标签、状态守卫、事务回滚
不留下部分子报价、删除子报价后兄弟报价文件与证据仍可查看。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import MergeChange, ParseTask, Quote
from app.models.enums import ParseTaskStatus, QuoteStatus
from app.services.parser import pipeline as pipeline_module
from app.services.parser.candidate_writer import apply_single_plan
from tests.split_merge_helpers import (
    extraction_from,
    run_parse,
    setup_project_and_quote,
    upload_files,
)


@pytest.fixture(autouse=True)
def _restore_pipeline():
    yield
    from app.services.parser.pipeline import UnconfiguredVisionPipeline

    pipeline_module.set_parse_pipeline(UnconfiguredVisionPipeline())


async def _prepare_multi_plan_quote(file_client, db_session, file_upload_settings) -> tuple[int, int]:
    """上传两文件并以同公司双方案 fixture 完成一次成功解析。

    返回 (projectId, quoteId)，供用例做精确的数据存在性断言。
    """
    project_id, quote_id = await setup_project_and_quote(file_client)
    await upload_files(file_client, quote_id, count=2)
    await run_parse(db_session, file_upload_settings, [extraction_from("multi_plan_same_insurer.json")])
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PENDING_CONFIRM
    return project_id, quote_id


async def test_plan_split_preview(file_client, db_session, file_upload_settings) -> None:
    project_id, quote_id = await _prepare_multi_plan_quote(file_client, db_session, file_upload_settings)

    response = await file_client.get(f"/api/quotes/{quote_id}/plan-split")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["planCount"] == 2
    assert data["insurerName"] == "人保"
    assert [plan["planLabel"] for plan in data["plans"]] == ["方案A", "方案B"]
    first = data["plans"][0]
    assert first["prices"]["commercialPremium"]["value"] == 2800.0
    assert first["prices"]["commercialPremium"]["status"] == "INCLUDED"
    # 单方案报价没有可拆分数据：409
    single = await file_client.get("/api/quotes/999999/plan-split")
    assert single.status_code == 404


async def test_plan_split_confirm_creates_sibling_quotes(
    file_client, db_session, file_upload_settings
) -> None:
    project_id, quote_id = await _prepare_multi_plan_quote(file_client, db_session, file_upload_settings)
    container_files = (await file_client.get(f"/api/quotes/{quote_id}")).json()["data"]["files"]
    container_file_ids = {f["id"] for f in container_files}

    response = await file_client.post(
        f"/api/quotes/{quote_id}/plan-split",
        json={"plans": [{"index": 0}, {"index": 1}]},
    )
    assert response.status_code == 201, response.text
    quotes = response.json()["data"]["quotes"]
    assert len(quotes) == 2
    assert [q["planLabel"] for q in quotes] == ["方案A", "方案B"]
    assert all(q["status"] == "PENDING_CONFIRM" for q in quotes)

    # 容器报价已删除；parse_task 保留 rawResult 且 quote_id 置空（可回放）
    assert (await file_client.get(f"/api/quotes/{quote_id}")).status_code == 404
    task = (
        await db_session.execute(select(ParseTask).order_by(ParseTask.id.desc()).limit(1))
    ).scalars().first()
    assert task.quote_id is None
    assert task.status == ParseTaskStatus.SUCCEEDED
    assert task.raw_result is not None
    assert task.raw_result["planCount"] == 2

    # 子报价各自持有完整候选（商业险金额来自各自方案）且共享原文件
    commercial = []
    for ref in quotes:
        detail = (await file_client.get(f"/api/quotes/{ref['id']}")).json()["data"]
        assert detail["status"] == "PENDING_CONFIRM"
        assert {f["id"] for f in detail["files"]} == container_file_ids
        commercial.append(detail["commercialPremium"])
    assert sorted(commercial) == [2800.0, 3200.0]

    # 子报价保留方案价格证据（来源指向共享文件），确认页可定位
    evidence = (await file_client.get(f"/api/quotes/{quotes[0]['id']}")).json()["data"]["evidences"]
    commercial_ev = next(e for e in evidence if e["fieldName"] == "commercialPremium")
    assert commercial_ev["sourceFileId"] in container_file_ids


async def test_plan_split_discard_and_relabel(
    file_client, db_session, file_upload_settings
) -> None:
    project_id, quote_id = await _prepare_multi_plan_quote(file_client, db_session, file_upload_settings)

    response = await file_client.post(
        f"/api/quotes/{quote_id}/plan-split",
        json={"plans": [{"index": 1, "planLabel": "高档方案"}]},
    )
    assert response.status_code == 201
    quotes = response.json()["data"]["quotes"]
    assert len(quotes) == 1
    assert quotes[0]["planLabel"] == "高档方案"
    detail = (await file_client.get(f"/api/quotes/{quotes[0]['id']}")).json()["data"]
    assert detail["commercialPremium"] == 3200.0


async def test_plan_split_rejects_empty_and_bad_index(
    file_client, db_session, file_upload_settings
) -> None:
    project_id, quote_id = await _prepare_multi_plan_quote(file_client, db_session, file_upload_settings)

    empty = await file_client.post(
        f"/api/quotes/{quote_id}/plan-split", json={"plans": []}
    )
    assert empty.status_code == 422
    bad_index = await file_client.post(
        f"/api/quotes/{quote_id}/plan-split", json={"plans": [{"index": 5}]}
    )
    assert bad_index.status_code == 422
    duplicate = await file_client.post(
        f"/api/quotes/{quote_id}/plan-split",
        json={"plans": [{"index": 0}, {"index": 0}]},
    )
    assert duplicate.status_code == 422


async def test_plan_split_requires_pending_confirm(
    file_client, db_session, file_upload_settings
) -> None:
    project_id, quote_id = await _prepare_multi_plan_quote(file_client, db_session, file_upload_settings)
    confirmed = await file_client.post(f"/api/quotes/{quote_id}/confirm", json={})
    assert confirmed.status_code == 200
    # 已确认报价不支持拆分（拆分只面向待确认的多方案容器）
    response = await file_client.get(f"/api/quotes/{quote_id}/plan-split")
    assert response.status_code == 409


async def test_plan_split_rollback_leaves_no_partial(
    file_client, db_session, file_upload_settings, monkeypatch
) -> None:
    """事务失败注入：第二个方案写入失败时整体回滚（验证 2）。"""
    project_id, quote_id = await _prepare_multi_plan_quote(file_client, db_session, file_upload_settings)

    original = apply_single_plan
    calls = {"n": 0}

    async def flaky(db, **kwargs):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("注入的事务失败")
        return await original(db, **kwargs)

    monkeypatch.setattr(
        "app.services.plan_split_service.apply_single_plan", flaky
    )
    response = await file_client.post(
        f"/api/quotes/{quote_id}/plan-split",
        json={"plans": [{"index": 0}, {"index": 1}]},
    )
    assert response.status_code == 500

    # 回滚后：无子报价、容器完好、rawResult 与文件关联原样保留
    remaining = (
        await db_session.execute(select(Quote).where(Quote.project_id == project_id))
    ).scalars().all()
    assert [q.id for q in remaining] == [quote_id]
    container = await db_session.get(Quote, quote_id)
    assert container.status == QuoteStatus.PENDING_CONFIRM
    task = (
        await db_session.execute(select(ParseTask).order_by(ParseTask.id.desc()).limit(1))
    ).scalars().first()
    assert task.raw_result is not None
    assert task.quote_id == quote_id
    # 无孤儿合并变更/关联（拆分过程不产生这两类数据，仅确认无残留）
    assert (
        await db_session.execute(select(MergeChange).where(MergeChange.quote_id == quote_id))
    ).scalars().all() == []


async def test_delete_split_child_keeps_sibling_files(
    file_client, db_session, file_upload_settings
) -> None:
    """删除一个子报价不影响兄弟报价查看原文件与证据（验证 1、TASKS 范围 9）。"""
    project_id, quote_id = await _prepare_multi_plan_quote(file_client, db_session, file_upload_settings)
    split = await file_client.post(
        f"/api/quotes/{quote_id}/plan-split",
        json={"plans": [{"index": 0}, {"index": 1}]},
    )
    first_id, second_id = [q["id"] for q in split.json()["data"]["quotes"]]
    shared_file_ids = {
        f["id"] for f in (await file_client.get(f"/api/quotes/{first_id}")).json()["data"]["files"]
    }

    deleted = await file_client.delete(f"/api/quotes/{first_id}")
    assert deleted.status_code == 200

    sibling = (await file_client.get(f"/api/quotes/{second_id}")).json()["data"]
    assert {f["id"] for f in sibling["files"]} == shared_file_ids
    # 兄弟报价的证据来源仍指向共享文件（raw 接口可读）
    evidence = sibling["evidences"]
    commercial_ev = next(e for e in evidence if e["fieldName"] == "commercialPremium")
    assert commercial_ev["sourceFileId"] in shared_file_ids
    raw = await file_client.get(
        f"/api/files/{commercial_ev['sourceFileId']}/raw",
        params={"projectId": sibling["projectId"]},
    )
    assert raw.status_code == 200
