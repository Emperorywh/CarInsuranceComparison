"""补传合并集成测试（TASK-05 验证 3、4）与解析任务失败联动。

覆盖：
- CONFIRMED 补传只解析本次新增文件、重解析覆盖全部文件；运行期间旧值
  可读、失败回到 CONFIRMED；
- 成功解析生成 ADD/CONFLICT（含同键多行整组冲突）、用户编辑项默认
  KEEP、全部解决后的原子合并与重算；
- PENDING_CONFIRM 重解析：失败回待确认保留候选，成功只覆盖未编辑候选；
- 多方案补传与公司不一致的明确失败。
"""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from app.models import MergeChange, ParseTask, ParseTaskFile, Quote, QuoteCoverage
from app.models.enums import MergeResolution, ParseTaskStatus, QuoteStatus
from app.services.parser import pipeline as pipeline_module
from app.services.parser.pipeline import ParseConfigError
from tests.files_helpers import pdf_bytes
from tests.split_merge_helpers import (
    extraction_from,
    extraction_from_payload,
    load_fixture,
    run_parse,
    setup_project_and_quote,
    upload_files,
)


@pytest.fixture(autouse=True)
def _restore_pipeline():
    yield
    from app.services.parser.pipeline import UnconfiguredVisionPipeline

    pipeline_module.set_parse_pipeline(UnconfiguredVisionPipeline())


async def _confirmed_quote(
    file_client, db_session, file_upload_settings, *, file_count: int = 2
) -> int:
    """上传-解析-确认主链路：得到一份含完整候选的 CONFIRMED 报价。"""
    _project_id, quote_id = await setup_project_and_quote(file_client)
    await upload_files(file_client, quote_id, count=file_count)
    await run_parse(db_session, file_upload_settings, [extraction_from("picc_full.json")])
    confirmed = await file_client.post(f"/api/quotes/{quote_id}/confirm", json={})
    assert confirmed.status_code == 200, confirmed.text
    return quote_id


def _changed_payload() -> dict:
    """相对 picc_full 的定点变化版（补传场景）：冲突 + 新增，无删除。"""
    payload = copy.deepcopy(load_fixture("picc_full.json"))
    plan = payload["plans"][0]
    # 标量冲突：商业险与官方总价变化；交强/车船税不变
    plan["pricing"]["commercialPremium"]["value"] = 4500.0
    plan["pricing"]["officialTotal"]["value"] = 5945.0
    # 车损不变、三者保额与保费变化（同行两个字段冲突）
    for row in plan["coreCoverages"]:
        if "第三者" in row["rawName"]:
            row["coverageAmount"] = 5000000.0
            row["premium"] = 2000.0
    # 新增附加险（划痕）→ ADD
    plan["additionalCoverages"].append(
        {
            "rawName": "附加车身划痕损失",
            "rawValue": "保费140.00元",
            "status": "INCLUDED",
            "coverageAmount": None,
            "premium": 140.0,
            "perSeatAmount": None,
            "seatCount": None,
            "sharedCoverage": False,
            "multiplier": None,
            "condition": None,
            "description": None,
            "selfConfidence": 0.9,
            "evidence": {"fileKey": "F1", "page": 1, "text": "附加车身划痕损失 140元"},
        }
    )
    # 道路救援次数变化（服务字段冲突）+ 新增代驾（ADD）
    for row in plan["services"]:
        if row["rawName"] == "道路救援":
            row["count"] = 3
    plan["services"].append(
        {
            "rawName": "代驾",
            "rawValue": "1次",
            "status": "INCLUDED",
            "count": 1,
            "cost": None,
            "description": None,
            "selfConfidence": 0.85,
            "evidence": {"fileKey": "F2", "page": 1, "text": "代驾 1次"},
        }
    )
    # 保障包保费变化（包字段冲突）
    plan["supplementalPackages"][0]["premium"] = 400.0
    return payload


async def _count_task_files(db_session, task_id: int) -> int:
    """任务输入文件数（显式查询，避免异步懒加载）。"""
    from sqlalchemy import func

    return int(
        await db_session.scalar(
            select(func.count()).select_from(ParseTaskFile).where(ParseTaskFile.task_id == task_id)
        )
    )


async def _latest_task(db_session, quote_id: int | None = None) -> ParseTask:
    stmt = select(ParseTask).order_by(ParseTask.id.desc()).limit(1)
    if quote_id is not None:
        stmt = (
            select(ParseTask)
            .where(ParseTask.quote_id == quote_id)
            .order_by(ParseTask.id.desc())
            .limit(1)
        )
    return (await db_session.execute(stmt)).scalars().first()


# ---- CONFIRMED 补传/重解析的输入范围与状态保持（范围 5）----


async def test_confirmed_upload_parses_new_files_only(
    file_client, db_session, file_upload_settings
) -> None:
    quote_id = await _confirmed_quote(file_client, db_session, file_upload_settings)
    # 旧值此刻可读（保持 CONFIRMED）
    before = (await file_client.get(f"/api/quotes/{quote_id}")).json()["data"]

    task_id = await upload_files(file_client, quote_id, count=1)
    task = await db_session.get(ParseTask, task_id)
    # 任务输入 = 仅本次新增的 1 个文件（SPEC §2.10）
    assert await _count_task_files(db_session, task_id) == 1
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.CONFIRMED
    assert task.on_failure_quote_status is None
    assert before["commercialPremium"] == 4093.91


async def test_confirmed_reparse_uses_all_files(
    file_client, db_session, file_upload_settings
) -> None:
    quote_id = await _confirmed_quote(file_client, db_session, file_upload_settings)
    response = await file_client.post(f"/api/quotes/{quote_id}/reparse", data={})
    assert response.status_code == 202
    task_id = response.json()["data"]["taskId"]
    # 重新解析输入 = 全部关联文件（2 个）
    assert await _count_task_files(db_session, task_id) == 2
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.CONFIRMED


async def test_merge_review_flow_accept(
    file_client, db_session, file_upload_settings
) -> None:
    quote_id = await _confirmed_quote(file_client, db_session, file_upload_settings)
    # 补传 1 个 2 页 PDF（fixture 证据指向 page 2，需要页码合法才能建立来源）
    upload = await file_client.post(
        f"/api/quotes/{quote_id}/files",
        files=[("files", ("补充报价单.pdf", pdf_bytes(2), "application/pdf"))],
        data={"modelProcessingConsent": "true"},
    )
    assert upload.status_code == 202
    await run_parse(db_session, file_upload_settings, [extraction_from_payload(_changed_payload())])

    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.MERGE_REVIEW
    # 旧数据未被改写（MERGE_REVIEW 期间可查看/可对比旧值）
    assert float(quote.commercial_premium) == 4093.91
    assert float(quote.official_total) == 5486.91

    preview = await file_client.get(f"/api/quotes/{quote_id}/merge-preview")
    assert preview.status_code == 200
    data = preview.json()["data"]
    assert data["pendingCount"] == len(data["changes"])
    changes = data["changes"]

    def find(entity_type, entity_key, field_name):
        return next(
            c
            for c in changes
            if c["entityType"] == entity_type
            and c["entityKey"] == entity_key
            and c["fieldName"] == field_name
        )

    # 标量 CONFLICT：商业险旧 4093.91 → 新 4500，且新值携带证据定位
    commercial = find("scalar", "commercialPremium", "commercialPremium")
    assert commercial["kind"] == "CONFLICT"
    assert commercial["oldValue"] == {"value": 4093.91, "status": "INCLUDED"}
    assert commercial["newValue"]["value"] == 4500.0
    assert commercial["newValue"]["sourceFileId"] is not None
    # 险种字段 CONFLICT：三者保额 300 万 → 500 万
    third = find("coverage", "THIRD_PARTY_LIABILITY", "coverageAmount")
    assert third["oldValue"] == 3000000.0
    assert third["newValue"]["value"] == 5000000.0
    # 新增附加险：划痕命中标准码 SCRATCH（coverage 实体 ADD）
    scratch = find("coverage", "SCRATCH", "__row__")
    assert scratch["kind"] == "ADD"
    # 服务字段冲突与新增
    rescue = find("service", "ROAD_RESCUE", "count")
    assert rescue["oldValue"] == 2 and rescue["newValue"]["value"] == 3
    driver = find("service", "DRIVER_SERVICE", "__row__")
    assert driver["kind"] == "ADD"
    # 保障包保费冲突
    package = find("package", "人保车主尊享保障", "premium")
    assert package["oldValue"] == 348.0 and package["newValue"]["value"] == 400.0
    # 未编辑项默认 ACCEPT；全量 ACCEPT 后原子合并并回 CONFIRMED
    assert all(c["defaultResolution"] == "ACCEPT" for c in changes)
    resolve = await file_client.post(
        f"/api/quotes/{quote_id}/merge-resolve",
        json={
            "resolutions": [
                {"changeId": c["id"], "resolution": "ACCEPT"} for c in changes
            ]
        },
    )
    assert resolve.status_code == 200, resolve.text
    merged = resolve.json()["data"]
    assert merged["status"] == "CONFIRMED"
    assert merged["commercialPremium"] == 4500.0
    assert merged["officialTotal"] == 5945.0
    scratch_row = next(c for c in merged["coverages"] if c["rawName"] == "附加车身划痕损失")
    assert scratch_row["premium"] == 140.0
    assert scratch_row["editedByUser"] is True  # 用户裁决采纳 → 用户已确认口径
    rescue_row = next(c for c in merged["services"] if c["serviceType"] == "ROAD_RESCUE")
    assert rescue_row["count"] == 3
    # 三者行保费同步更新（同行第二个字段冲突也被采纳）
    third_row = next(
        c for c in merged["coverages"] if c["code"] == "THIRD_PARTY_LIABILITY"
    )
    assert third_row["coverageAmount"] == 5000000.0
    assert third_row["premium"] == 2000.0
    # 净支出重算：官方总价 5945（无优惠）→ netPayment=5945
    assert merged["netPayment"] == 5945.0
    # 裁决记录保留（审计）
    resolved = (
        await db_session.execute(select(MergeChange).where(MergeChange.quote_id == quote_id))
    ).scalars().all()
    assert all(c.resolution == MergeResolution.ACCEPT for c in resolved)


async def test_merge_resolve_keep_preserves_old_values(
    file_client, db_session, file_upload_settings
) -> None:
    quote_id = await _confirmed_quote(file_client, db_session, file_upload_settings)
    await upload_files(file_client, quote_id, count=1)
    await run_parse(db_session, file_upload_settings, [extraction_from_payload(_changed_payload())])

    preview = (await file_client.get(f"/api/quotes/{quote_id}/merge-preview")).json()["data"]
    resolve = await file_client.post(
        f"/api/quotes/{quote_id}/merge-resolve",
        json={
            "resolutions": [
                {"changeId": c["id"], "resolution": "KEEP"} for c in preview["changes"]
            ]
        },
    )
    assert resolve.status_code == 200
    merged = resolve.json()["data"]
    assert merged["status"] == "CONFIRMED"
    # 全部保留旧值：新旧值原样
    assert merged["commercialPremium"] == 4093.91
    assert merged["officialTotal"] == 5486.91
    assert not any(c["rawName"] == "附加车身划痕损失" for c in merged["coverages"])


async def test_user_edited_defaults_to_keep(file_client, db_session, file_upload_settings) -> None:
    quote_id = await _confirmed_quote(file_client, db_session, file_upload_settings)
    # 用户编辑商业险保费（确认后编辑保持 CONFIRMED）
    edited = await file_client.patch(
        f"/api/quotes/{quote_id}", json={"commercialPremium": "5000.00"}
    )
    assert edited.status_code == 200
    await upload_files(file_client, quote_id, count=1)
    await run_parse(db_session, file_upload_settings, [extraction_from_payload(_changed_payload())])

    preview = (await file_client.get(f"/api/quotes/{quote_id}/merge-preview")).json()["data"]
    commercial = next(
        c
        for c in preview["changes"]
        if c["entityType"] == "scalar" and c["entityKey"] == "commercialPremium"
    )
    assert commercial["userEdited"] is True
    assert commercial["defaultResolution"] == "KEEP"
    # 用户编辑值 5000 不被静默覆盖（保持旧值，模型值 4500 仅作为新值展示）
    resolve = await file_client.post(
        f"/api/quotes/{quote_id}/merge-resolve",
        json={
            "resolutions": [
                {"changeId": c["id"], "resolution": c["defaultResolution"]}
                for c in preview["changes"]
            ]
        },
    )
    assert resolve.status_code == 200
    assert resolve.json()["data"]["commercialPremium"] == 5000.0


async def test_merge_resolve_requires_all_changes(
    file_client, db_session, file_upload_settings
) -> None:
    quote_id = await _confirmed_quote(file_client, db_session, file_upload_settings)
    await upload_files(file_client, quote_id, count=1)
    await run_parse(db_session, file_upload_settings, [extraction_from_payload(_changed_payload())])
    preview = (await file_client.get(f"/api/quotes/{quote_id}/merge-preview")).json()["data"]

    partial = await file_client.post(
        f"/api/quotes/{quote_id}/merge-resolve",
        json={"resolutions": [{"changeId": preview["changes"][0]["id"], "resolution": "KEEP"}]},
    )
    assert partial.status_code == 422
    unknown = await file_client.post(
        f"/api/quotes/{quote_id}/merge-resolve",
        json={"resolutions": [{"changeId": 999999, "resolution": "KEEP"}]},
    )
    assert unknown.status_code == 422
    # 未解决前状态不变
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.MERGE_REVIEW


async def test_merge_review_blocks_mutation(file_client, db_session, file_upload_settings) -> None:
    quote_id = await _confirmed_quote(file_client, db_session, file_upload_settings)
    await upload_files(file_client, quote_id, count=1)
    await run_parse(db_session, file_upload_settings, [extraction_from_payload(_changed_payload())])

    # MERGE_REVIEW 期间禁止编辑/再次重解析/补传（先完成合并确认）
    patch = await file_client.patch(
        f"/api/quotes/{quote_id}", json={"commercialPremium": "1.00"}
    )
    assert patch.status_code == 409
    reparse = await file_client.post(f"/api/quotes/{quote_id}/reparse", data={})
    assert reparse.status_code == 409
    upload = await file_client.post(
        f"/api/quotes/{quote_id}/files",
        files=[("files", ("再来一张.jpg", b"", "image/jpeg"))],
        data={"modelProcessingConsent": "true"},
    )
    assert upload.status_code in (409, 422)  # 状态守卫先于文件校验


async def test_failed_merge_parse_keeps_confirmed(
    file_client, db_session, file_upload_settings
) -> None:
    quote_id = await _confirmed_quote(file_client, db_session, file_upload_settings)
    await upload_files(file_client, quote_id, count=1)
    # 解析失败：报价保持 CONFIRMED，旧数据可读，无变更生成
    await run_parse(
        db_session,
        file_upload_settings,
        [ParseConfigError("视觉模型鉴权失败（HTTP 401），请检查 VISION_API_KEY 配置")],
    )
    task = await _latest_task(db_session, quote_id)
    assert task.status == ParseTaskStatus.FAILED
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.CONFIRMED
    detail = (await file_client.get(f"/api/quotes/{quote_id}")).json()["data"]
    assert detail["commercialPremium"] == 4093.91
    changes = (
        await db_session.execute(select(MergeChange).where(MergeChange.quote_id == quote_id))
    ).scalars().all()
    assert changes == []


async def test_merge_multi_plan_and_insurer_mismatch_fail(
    file_client, db_session, file_upload_settings
) -> None:
    quote_id = await _confirmed_quote(file_client, db_session, file_upload_settings)
    await upload_files(file_client, quote_id, count=1)
    # 多方案补传：明确失败（已确认报价无法承载多方案归属）
    await run_parse(
        db_session, file_upload_settings, [extraction_from("multi_plan_same_insurer.json")]
    )
    task = await _latest_task(db_session, quote_id)
    assert task.status == ParseTaskStatus.FAILED
    assert "多个方案" in task.error
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.CONFIRMED

    # 公司不一致补传：明确失败（应新建报价而不是合并进当前报价）
    await upload_files(file_client, quote_id, count=1)
    mismatched = copy.deepcopy(load_fixture("picc_full.json"))
    mismatched["insurer"]["name"] = "平安"
    await run_parse(db_session, file_upload_settings, [extraction_from_payload(mismatched)])
    task = await _latest_task(db_session, quote_id)
    assert task.status == ParseTaskStatus.FAILED
    assert "保险公司" in task.error
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.CONFIRMED


# ---- PENDING_CONFIRM 的重解析与失败联动（范围 4、验证 3）----


async def _pending_quote_with_user_edit(
    file_client, db_session, file_upload_settings
) -> int:
    """单方案解析后保持待确认，并让用户编辑商业险保费。"""
    _project_id, quote_id = await setup_project_and_quote(file_client)
    await upload_files(file_client, quote_id, count=2)
    await run_parse(db_session, file_upload_settings, [extraction_from("picc_full.json")])
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PENDING_CONFIRM
    edited = await file_client.patch(
        f"/api/quotes/{quote_id}", json={"commercialPremium": "5000.00"}
    )
    assert edited.status_code == 200
    return quote_id


async def test_pending_reparse_failure_returns_to_pending(
    file_client, db_session, file_upload_settings
) -> None:
    quote_id = await _pending_quote_with_user_edit(file_client, db_session, file_upload_settings)
    response = await file_client.post(f"/api/quotes/{quote_id}/reparse", data={})
    assert response.status_code == 202
    task = await db_session.get(ParseTask, response.json()["data"]["taskId"])
    # 待确认重解析的失败联动目标：回 PENDING_CONFIRM（不是 PARSE_FAILED）
    assert task.on_failure_quote_status == QuoteStatus.PENDING_CONFIRM
    await run_parse(
        db_session,
        file_upload_settings,
        [ParseConfigError("视觉模型鉴权失败（HTTP 401），请检查 VISION_API_KEY 配置")],
    )
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PENDING_CONFIRM
    # 上一次候选数据保留
    assert float(quote.commercial_premium) == 5000.0


async def test_pending_reparse_success_updates_unedited_only(
    file_client, db_session, file_upload_settings
) -> None:
    quote_id = await _pending_quote_with_user_edit(file_client, db_session, file_upload_settings)
    await file_client.post(f"/api/quotes/{quote_id}/reparse", data={})
    await run_parse(db_session, file_upload_settings, [extraction_from_payload(_changed_payload())])
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PENDING_CONFIRM
    # 用户编辑值保留；未编辑候选被新解析覆盖
    assert float(quote.commercial_premium) == 5000.0
    assert float(quote.official_total) == 5945.0
    rows = (
        await db_session.execute(
            select(QuoteCoverage).where(QuoteCoverage.quote_id == quote_id)
        )
    ).scalars().all()
    third = next(row for row in rows if row.code == "THIRD_PARTY_LIABILITY")
    assert float(third.coverage_amount) == 5000000.0
    # 用户编辑过的标量证据保持“用户已确认”
    from app.models import FieldEvidence

    evidence = (
        await db_session.execute(
            select(FieldEvidence).where(
                FieldEvidence.quote_id == quote_id,
                FieldEvidence.field_name == "commercialPremium",
            )
        )
    ).scalars().first()
    assert evidence.edited_by_user is True


# ---- 同键多行整组冲突（范围 6）----


async def test_same_key_multi_row_group_conflict(
    file_client, db_session, file_upload_settings
) -> None:
    quote_id = await _confirmed_quote(file_client, db_session, file_upload_settings)
    await upload_files(file_client, quote_id, count=1)
    payload = _changed_payload()
    # 新解析出现两行同码（三者险）且内容不同：整组冲突，不猜测合并
    third = next(
        row
        for row in payload["plans"][0]["coreCoverages"]
        if "第三者" in row["rawName"]
    )
    variant = copy.deepcopy(third)
    variant["premium"] = 1999.0
    payload["plans"][0]["coreCoverages"].append(variant)
    # 恢复原保额避免与既有字面量耦合：组冲突只断言 fieldName
    await run_parse(db_session, file_upload_settings, [extraction_from_payload(payload)])

    preview = (await file_client.get(f"/api/quotes/{quote_id}/merge-preview")).json()["data"]
    group = next(
        c
        for c in preview["changes"]
        if c["entityType"] == "coverage"
        and c["entityKey"] == "THIRD_PARTY_LIABILITY"
        and c["fieldName"] == "__rows__"
    )
    assert group["kind"] == "CONFLICT"
    assert len(group["newValue"]["rows"]) == 2
    # ACCEPT：整组替换（旧行删除、新组插入，行数与解析一致）
    resolve = await file_client.post(
        f"/api/quotes/{quote_id}/merge-resolve",
        json={
            "resolutions": [
                {"changeId": c["id"], "resolution": "ACCEPT"} for c in preview["changes"]
            ]
        },
    )
    assert resolve.status_code == 200
    merged = resolve.json()["data"]
    third_rows = [
        c for c in merged["coverages"] if c["code"] == "THIRD_PARTY_LIABILITY"
    ]
    assert len(third_rows) == 2


# ---- 标量 ADD：旧值缺失时不制造冲突 ----


async def test_scalar_add_when_old_absent(
    file_client, db_session, file_upload_settings
) -> None:
    # 首次解析时车船税未知（确认后旧值为空）
    payload = copy.deepcopy(load_fixture("picc_full.json"))
    payload["plans"][0]["pricing"]["vehicleTax"] = {
        "value": None,
        "rawValue": None,
        "status": "UNKNOWN",
        "selfConfidence": None,
        "evidence": None,
    }
    _project_id, quote_id = await setup_project_and_quote(file_client)
    await upload_files(file_client, quote_id, count=2)
    await run_parse(db_session, file_upload_settings, [extraction_from_payload(payload)])
    await file_client.post(f"/api/quotes/{quote_id}/confirm", json={})

    # 补传读到车船税 0 元：旧值缺失 → ADD（而非 CONFLICT）
    await upload_files(file_client, quote_id, count=1)
    changed = _changed_payload()
    changed["plans"][0]["pricing"]["vehicleTax"]["status"] = "INCLUDED"
    changed["plans"][0]["pricing"]["vehicleTax"]["value"] = 0.0
    await run_parse(db_session, file_upload_settings, [extraction_from_payload(changed)])

    preview = (await file_client.get(f"/api/quotes/{quote_id}/merge-preview")).json()["data"]
    tax = next(
        c
        for c in preview["changes"]
        if c["entityType"] == "scalar" and c["entityKey"] == "vehicleTax"
    )
    assert tax["kind"] == "ADD"
    assert tax["oldValue"] == {"value": None, "status": "UNKNOWN"}
    resolve = await file_client.post(
        f"/api/quotes/{quote_id}/merge-resolve",
        json={
            "resolutions": [
                {"changeId": c["id"], "resolution": "ACCEPT"} for c in preview["changes"]
            ]
        },
    )
    assert resolve.status_code == 200
    merged = resolve.json()["data"]
    assert merged["vehicleTax"] == 0.0
    assert merged["vehicleTaxStatus"] == "INCLUDED"


# ---- 信息不足不制造冲突 ----


async def test_unknown_new_value_generates_no_change(
    file_client, db_session, file_upload_settings
) -> None:
    quote_id = await _confirmed_quote(file_client, db_session, file_upload_settings)
    await upload_files(file_client, quote_id, count=1)
    # 新解析把官方总价读丢了（null/UNKNOWN）：不得把旧值抹掉
    payload = _changed_payload()
    payload["plans"][0]["pricing"]["officialTotal"] = {
        "value": None,
        "rawValue": None,
        "status": "UNKNOWN",
        "selfConfidence": None,
        "evidence": None,
    }
    await run_parse(db_session, file_upload_settings, [extraction_from_payload(payload)])
    preview = (await file_client.get(f"/api/quotes/{quote_id}/merge-preview")).json()["data"]
    assert not any(
        c["entityType"] == "scalar" and c["entityKey"] == "officialTotal"
        for c in preview["changes"]
    )
