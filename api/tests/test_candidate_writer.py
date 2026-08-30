"""固定 rawResult fixture 的候选落库回放测试（TASK-04 验证 1/2/3/4）。

不访问网络：直接把脱敏 fixture 交给 apply_extraction，逐字段断言落库
结果。覆盖：人保全量单方案（价格三态/座位/保障包/销售标注/未识别金额
项）、平安 PDF 多页与多文件相同页码、未知险种、非法证据、严格去重、
用户编辑保护、同公司多方案（只落 rawResult）、混合公司失败、隐私脱敏
（手机号/车牌/VIN/身份证/个人字段标签不入库）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.core.privacy import HIDDEN_TEXT, sanitize_evidence_text
from app.models import (
    ComparisonProject,
    FieldEvidence,
    ParseTask,
    Quote,
    QuoteCoverage,
    QuoteFile,
    QuoteFileLink,
    QuoteService,
    SalesAnnotation,
    SupplementalPackage,
)
from app.models.enums import (
    ConfidenceLevel,
    CoverageCategory,
    ItemStatus,
    ParseTaskStatus,
    PriceItemStatus,
    QuoteSource,
    QuoteStatus,
    TotalCheckStatus,
)
from app.services.parser.candidate_writer import (
    INSURER_MODEL_FIELD,
    EvidenceResolver,
    apply_extraction,
)
from app.services.parser.extraction_schema import (
    EvidenceExtraction,
    parse_extraction,
)
from app.services.parser.pipeline import ParseTaskFailure, ParseTaskFileInput

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "raw_results"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def parse_fixture(name: str):
    return parse_extraction(load_fixture(name))


def default_settings() -> Settings:
    return Settings(app_bind_host="127.0.0.1", local_access_token="")


def evidence(file_key: str, page: int, text: str | None) -> EvidenceExtraction:
    return EvidenceExtraction.model_validate(
        {"fileKey": file_key, "page": page, "text": text}
    )


async def prepare_quote(
    db,
    *,
    insurer_code: str = "PICC",
    insurer_name: str = "人保",
    files: list[tuple[str, int]] | None = None,
) -> tuple[ParseTask, Quote, list[ParseTaskFileInput]]:
    """构造“项目 + PARSING 报价 + 输入文件 + 运行中任务”的回放环境。"""
    project = ComparisonProject(
        name="回放项目", renewal_year=2026, vehicle_name="Model Y"
    )
    db.add(project)
    await db.flush()
    quote = Quote(
        project_id=project.id,
        insurer_code=insurer_code,
        insurer_name=insurer_name,
        source=QuoteSource.UPLOADED,
        status=QuoteStatus.PARSING,
    )
    db.add(quote)
    await db.flush()
    inputs: list[ParseTaskFileInput] = []
    for order, (mime, page_count) in enumerate(files or [("image/jpeg", 1)], start=1):
        quote_file = QuoteFile(
            project_id=project.id,
            file_path=f"fake/{quote.id}/{order}",
            original_name=f"报价单{order}",
            mime=mime,
            size_bytes=1024,
            page_count=page_count,
        )
        db.add(quote_file)
        await db.flush()
        db.add(
            QuoteFileLink(
                quote_id=quote.id, file_id=quote_file.id, sort_order=order - 1
            )
        )
        inputs.append(
            ParseTaskFileInput(
                file_id=quote_file.id,
                file_key=f"F{order}",
                relative_path=quote_file.file_path,
                mime=mime,
                page_count=page_count,
            )
        )
    task = ParseTask(
        project_id=project.id,
        quote_id=quote.id,
        status=ParseTaskStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db.add(task)
    await db.flush()
    return task, quote, inputs


async def load_coverages(db, quote_id: int) -> list[QuoteCoverage]:
    return list(
        (
            await db.execute(
                select(QuoteCoverage)
                .where(QuoteCoverage.quote_id == quote_id)
                .order_by(QuoteCoverage.id)
            )
        )
        .scalars()
    )


# ---- 人保全量单方案回放 ----


async def test_picc_full_single_plan(db_session) -> None:
    task, quote, files = await prepare_quote(
        # fixture 证据引用 F1（2 页 PDF）与 F2（1 页图片）
        db_session, files=[("application/pdf", 2), ("image/jpeg", 1)]
    )
    await apply_extraction(
        db_session,
        task=task,
        quote=quote,
        files=files,
        extraction=parse_fixture("picc_full.json"),
        settings=default_settings(),
    )

    assert quote.status == QuoteStatus.PENDING_CONFIRM

    # 价格字段逐项断言（值/状态/官方总价/系统合计/总额校验）
    assert quote.commercial_premium == Decimal("4093.91")
    assert quote.commercial_status == PriceItemStatus.INCLUDED
    assert quote.compulsory_premium == Decimal("1045.00")
    assert quote.vehicle_tax == Decimal("0.00")  # 新能源免征：0 元且 INCLUDED
    assert quote.vehicle_tax_status == PriceItemStatus.INCLUDED
    assert quote.package_total == Decimal("348.00")
    # 未识别金额项（其他保障说明 50元）阻断 computedCommercialPremium
    assert quote.computed_commercial_premium is None
    assert quote.computed_package_total == Decimal("348.00")
    assert quote.other_fees is None
    assert quote.other_fees_status == PriceItemStatus.NOT_INCLUDED
    assert quote.official_total == Decimal("5486.91")
    # eff(商业=显示值) + 交强 + 车船税0 + 保障包 + 其他费用0 = 5486.91 → PASSED
    assert quote.computed_total == Decimal("5486.91")
    assert quote.total_check_status == TotalCheckStatus.PASSED
    # 无优惠 → 净支出 = 官方总价
    assert quote.net_payment == Decimal("5486.91")

    # 车辆快照
    assert quote.vehicle_model == "Model Y"
    assert quote.vehicle_seats == 5
    assert quote.first_reg_date == "2022-05"
    assert quote.is_nev is True

    # 险种行：核心 4 + 附加 1 + 未识别 1（未匹配金额项）
    rows = await load_coverages(db_session, quote.id)
    by_key: dict[str, list[QuoteCoverage]] = {}
    for row in rows:
        by_key.setdefault(row.code or row.raw_name, []).append(row)
    assert set(by_key) == {
        "VEHICLE_LOSS",
        "THIRD_PARTY_LIABILITY",
        "DRIVER_LIABILITY",
        "PASSENGER_LIABILITY",
        "TP_NON_MEDICAL",
        "其他保障说明 50元",
    }
    third = by_key["THIRD_PARTY_LIABILITY"][0]
    assert third.category == CoverageCategory.CORE
    assert third.name == "三者险"
    assert third.premium == Decimal("1237.41")
    assert third.coverage_amount == Decimal("3000000.00")
    assert third.confidence_level == ConfidenceLevel.HIGH
    # 证据映射到真实文件：三者来自 F1 第 1 页
    f1_id = files[0].file_id
    assert third.source_file_id == f1_id
    assert third.source_page == 1
    assert third.source_text == "新能源汽车第三者责任保险 300万元"

    # 乘客座位结构保真：单座/座位/总额三值齐全且自洽（司机/乘客不互换）
    driver = by_key["DRIVER_LIABILITY"][0]
    assert driver.per_seat_amount == Decimal("10000.00")
    assert driver.seat_count == 1
    passenger = by_key["PASSENGER_LIABILITY"][0]
    assert passenger.per_seat_amount == Decimal("1000.00")
    assert passenger.seat_count == 4
    # 总额 = 单座 × 座位（SPEC §6.3 公式；示例数值 40000 与公式矛盾，
    # 以公式为准并已在完成记录中说明）
    assert passenger.coverage_amount == Decimal("4000.00")

    # 未识别金额项：进入 UNRECOGNIZED，金额落 premium 阻断计算；
    # 自报 0.5 < 0.6 → LOW（SPEC §4.2）
    unmatched = by_key["其他保障说明 50元"][0]
    assert unmatched.category == CoverageCategory.UNRECOGNIZED
    assert unmatched.premium == Decimal("50.00")
    assert unmatched.confidence_level == ConfidenceLevel.LOW

    # 服务：明确 0 元 → FREE；费用缺失 → UNKNOWN（不推断免费）
    services = (
        (
            await db_session.execute(
                select(QuoteService).where(QuoteService.quote_id == quote.id)
            )
        )
        .scalars()
        .all()
    )
    by_type = {s.service_type.value: s for s in services}
    assert by_type["ROAD_RESCUE"].status == ItemStatus.FREE
    assert by_type["ROAD_RESCUE"].cost == Decimal("0.00")
    assert by_type["INSPECTION"].status == ItemStatus.UNKNOWN

    # 保障包：整包落 supplemental_package
    packages = (
        (
            await db_session.execute(
                select(SupplementalPackage)
                .where(SupplementalPackage.quote_id == quote.id)
                .options(selectinload(SupplementalPackage.coverages))
            )
        )
        .scalars()
        .all()
    )
    assert len(packages) == 1
    package = packages[0]
    assert package.premium == Decimal("348.00")
    assert package.source_file_id == files[1].file_id  # F2
    assert len(package.coverages) == 1
    inner = package.coverages[0]
    assert inner.type == "DRIVER_ACCIDENT"
    assert inner.multiplier == Decimal("2.00")
    assert inner.condition == "LEGAL_HOLIDAY"
    # 隔离铁律：包内驾乘保障绝不生成 quote_coverage 的司机/乘客行
    assert all(
        row.code not in ("DRIVER_LIABILITY", "PASSENGER_LIABILITY")
        for row in rows
        if row.raw_name == "驾乘意外身故及残疾"
    )

    # 销售标注：只进标注表（红字“返现200元”绝不影响任何金额）
    annotations = (
        (
            await db_session.execute(
                select(SalesAnnotation).where(SalesAnnotation.quote_id == quote.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(annotations) == 1
    assert annotations[0].kind.value == "RED_TEXT"
    assert annotations[0].source_type.value == "SALES_ANNOTATION"
    assert annotations[0].source_file_id == files[1].file_id

    # 标量证据：官方总价/交强/商业来自正确文件与页码
    evidences = (
        (
            await db_session.execute(
                select(FieldEvidence).where(FieldEvidence.quote_id == quote.id)
            )
        )
        .scalars()
        .all()
    )
    by_field = {e.field_name: e for e in evidences}
    assert by_field["officialTotal"].source_file_id == f1_id
    assert by_field["officialTotal"].source_page == 2
    assert by_field["compulsoryPremium"].raw_value == "1045.00"
    assert by_field[INSURER_MODEL_FIELD].raw_value == "人保"
    assert by_field["vehicleModel"].source_file_id == f1_id
    # 商业险参与合计且总额 PASSED → 档位不受总额校验拖累
    assert by_field["commercialPremium"].confidence_level == ConfidenceLevel.HIGH


async def test_seat_amount_conflict_overrides_to_low(db_session) -> None:
    """座位三值矛盾：以“单座 × 座位”为准重算总额，该行降 LOW（SPEC §6.3）。"""
    payload = load_fixture("picc_full.json")
    plan = payload["plans"][0]
    passenger_row = next(
        row
        for row in plan["coreCoverages"]
        if "乘客" in row["rawName"]
    )
    # 模型给的总额（50000）与 单座1000×4=4000 矛盾
    passenger_row["coverageAmount"] = 50000.0

    task, quote, files = await prepare_quote(db_session)
    await apply_extraction(
        db_session,
        task=task,
        quote=quote,
        files=files,
        extraction=parse_extraction(payload),
        settings=default_settings(),
    )
    rows = await load_coverages(db_session, quote.id)
    passenger = next(row for row in rows if row.code == "PASSENGER_LIABILITY")
    # 候选值必须自洽：总额以“单座 × 座位”为准
    assert passenger.coverage_amount == Decimal("4000.00")
    assert passenger.per_seat_amount == Decimal("1000.00")
    assert passenger.seat_count == 4
    assert passenger.confidence_level == ConfidenceLevel.LOW


async def test_pingan_multipage_replay(db_session) -> None:
    """平安 fixture：多页证据、多文件相同页码、座位表达式、严格去重。"""
    task, quote, files = await prepare_quote(
        db_session,
        insurer_code="PINGAN",
        insurer_name="平安",
        files=[("application/pdf", 2), ("image/jpeg", 1)],
    )
    await apply_extraction(
        db_session,
        task=task,
        quote=quote,
        files=files,
        extraction=parse_fixture("pingan_multipage.json"),
        settings=default_settings(),
    )
    assert quote.total_check_status == TotalCheckStatus.NOT_CHECKABLE
    assert quote.computed_total is None
    assert quote.official_total == Decimal("4150.00")
    assert quote.vehicle_tax_status == PriceItemStatus.NOT_INCLUDED

    rows = await load_coverages(db_session, quote.id)
    # 严格去重：两条完全相同的三者行只保留一条（SPEC §6.4）
    third_rows = [row for row in rows if row.code == "THIRD_PARTY_LIABILITY"]
    assert len(third_rows) == 1
    # isNev=true 但出现“机动车第三者”燃油措辞 → 新能源一致性提示 MEDIUM
    assert third_rows[0].confidence_level == ConfidenceLevel.MEDIUM

    # 座位表达式兜底：模型缺 perSeat/seatCount，rawValue “0.1万/座×4” 解析
    driver = next(row for row in rows if row.code == "DRIVER_LIABILITY")
    assert driver.per_seat_amount == Decimal("1000.00")
    assert driver.seat_count == 4
    assert driver.coverage_amount == Decimal("4000.00")
    assert driver.confidence_level == ConfidenceLevel.HIGH

    # 多文件相同页码不得串文件：F1 第 2 页 vs F2 第 1 页
    f1_id, f2_id = files[0].file_id, files[1].file_id
    third = third_rows[0]
    assert third.source_file_id == f1_id and third.source_page == 2
    glass = next(row for row in rows if row.code == "GLASS_BROKEN")
    assert glass.source_file_id == f2_id and glass.source_page == 1

    # 非法类型码 → 按原文归一 AIR_ACCIDENT 且降为 MEDIUM；单位缺省按 CNY
    packages = (
        (
            await db_session.execute(
                select(SupplementalPackage)
                .where(SupplementalPackage.quote_id == quote.id)
                .options(selectinload(SupplementalPackage.coverages))
            )
        )
        .scalars()
        .all()
    )
    inner = packages[0].coverages[0]
    assert inner.type == "AIR_ACCIDENT"
    assert inner.unit.value == "CNY"
    assert inner.confidence_level == ConfidenceLevel.MEDIUM

    # 服务：代驾明确 0 元 → FREE
    services = (
        (
            await db_session.execute(
                select(QuoteService).where(QuoteService.quote_id == quote.id)
            )
        )
        .scalars()
        .all()
    )
    assert services[0].service_type.value == "DRIVER_SERVICE"
    assert services[0].status == ItemStatus.FREE


async def test_unknown_coverage_goes_unrecognized(db_session) -> None:
    """未知险种进 UNRECOGNIZED（不猜类别），含金额阻断 computed 商业险。"""
    task, quote, files = await prepare_quote(
        db_session, insurer_code="DADI", insurer_name="大地"
    )
    await apply_extraction(
        db_session,
        task=task,
        quote=quote,
        files=files,
        extraction=parse_fixture("unknown_coverage.json"),
        settings=default_settings(),
    )
    rows = await load_coverages(db_session, quote.id)
    unknown = next(row for row in rows if row.raw_name == "轮胎单独损坏保障")
    assert unknown.code is None
    assert unknown.category == CoverageCategory.UNRECOGNIZED
    assert unknown.name == "轮胎单独损坏保障"  # 未识别显示名 = 原始名
    assert unknown.confidence_level == ConfidenceLevel.MEDIUM  # 自报 0.6 + 证据 ok
    assert quote.computed_commercial_premium is None

    # unmatchedItems 无证据：无 evidence 按 MEDIUM 处理（LOW 只给非法证据/
    # 低自报——此处自报 0.4 < 0.6 仍降 LOW）
    yanbao = next(row for row in rows if row.raw_name == "延保服务说明")
    assert yanbao.confidence_level == ConfidenceLevel.LOW
    assert yanbao.source_file_id is None


async def test_illegal_evidence_drops_link_and_downgrades(db_session) -> None:
    """非法证据（未知 F9 / 越界页码）不建链且字段 LOW（SPEC §6.9）。"""
    task, quote, files = await prepare_quote(
        db_session, insurer_code="SUNSHINE", insurer_name="阳光"
    )
    await apply_extraction(
        db_session,
        task=task,
        quote=quote,
        files=files,
        extraction=parse_fixture("illegal_evidence.json"),
        settings=default_settings(),
    )
    rows = await load_coverages(db_session, quote.id)
    loss = rows[0]
    assert loss.source_file_id is None
    assert loss.source_page is None
    assert loss.confidence_level == ConfidenceLevel.LOW

    evidences = (
        (
            await db_session.execute(
                select(FieldEvidence).where(FieldEvidence.quote_id == quote.id)
            )
        )
        .scalars()
        .all()
    )
    by_field = {e.field_name: e for e in evidences}
    # 未知 fileKey（F9）→ 不伪造 sourceFileId，LOW
    insurer_ev = by_field[INSURER_MODEL_FIELD]
    assert insurer_ev.source_file_id is None
    assert insurer_ev.confidence_level == ConfidenceLevel.LOW
    # 越界页码（page 99 > 1）→ 同样不建链；参与合计但 NOT_CHECKABLE 不叠加
    commercial_ev = by_field["commercialPremium"]
    assert commercial_ev.source_file_id is None
    assert commercial_ev.source_page is None
    assert commercial_ev.confidence_level == ConfidenceLevel.LOW


# ---- 编辑保护（重解析覆盖规则）----


async def test_reparse_preserves_user_edited_rows_and_fields(db_session) -> None:
    """重解析只覆盖未编辑候选：用户改过的行/字段保留，其余被新候选替换。"""
    task, quote, files = await prepare_quote(db_session)
    await apply_extraction(
        db_session,
        task=task,
        quote=quote,
        files=files,
        extraction=parse_fixture("picc_full.json"),
        settings=default_settings(),
    )
    # 用户编辑：商业险显示价改为 5000（TASK-02 同口径：更新既有证据行，
    # 置 editedByUser=true，绝不另插同名字段的第二行）
    quote.commercial_premium = Decimal("5000.00")
    commercial_ev = (
        (
            await db_session.execute(
                select(FieldEvidence).where(
                    FieldEvidence.quote_id == quote.id,
                    FieldEvidence.field_name == "commercialPremium",
                )
            )
        )
        .scalars()
        .one()
    )
    commercial_ev.raw_value = "5000.00"
    commercial_ev.confidence_level = ConfidenceLevel.HIGH
    commercial_ev.edited_by_user = True
    # 用户编辑：三者行保费改 999（该行保留）
    rows = await load_coverages(db_session, quote.id)
    third = next(row for row in rows if row.code == "THIRD_PARTY_LIABILITY")
    third.premium = Decimal("999.00")
    third.edited_by_user = True
    await db_session.flush()

    # 二次解析（同 fixture）：用户行/字段保留，其余候选重建且不重复
    await apply_extraction(
        db_session,
        task=task,
        quote=quote,
        files=files,
        extraction=parse_fixture("picc_full.json"),
        settings=default_settings(),
    )
    assert quote.commercial_premium == Decimal("5000.00")
    rows_after = await load_coverages(db_session, quote.id)
    third_after = [row for row in rows_after if row.code == "THIRD_PARTY_LIABILITY"]
    assert len(third_after) == 1
    assert third_after[0].premium == Decimal("999.00")
    assert third_after[0].edited_by_user is True
    # 未编辑的车损行被重建（仍是新候选值），未产生成对重复
    loss_rows = [row for row in rows_after if row.code == "VEHICLE_LOSS"]
    assert len(loss_rows) == 1
    assert loss_rows[0].premium == Decimal("2460.00")
    # 证据行：用户编辑的 commercialPremium 保留，未编辑行被重建
    evidences = (
        (
            await db_session.execute(
                select(FieldEvidence).where(FieldEvidence.quote_id == quote.id)
            )
        )
        .scalars()
        .all()
    )
    commercial_rows = [
        row for row in evidences if row.field_name == "commercialPremium"
    ]
    assert len(commercial_rows) == 1
    assert commercial_rows[0].edited_by_user is True
    assert commercial_rows[0].raw_value == "5000.00"


# ---- 多方案与混合公司 ----


async def test_multi_plan_same_insurer_only_writes_raw_result(db_session) -> None:
    """同公司 planCount>1：只落 rawResult，报价回 PENDING_CONFIRM，
    不写任何 plan 明细（拆分确认属 TASK-05）。"""
    task, quote, files = await prepare_quote(
        db_session, insurer_code="PINGAN", insurer_name="平安"
    )
    await apply_extraction(
        db_session,
        task=task,
        quote=quote,
        files=files,
        extraction=parse_fixture("multi_plan_same_insurer.json"),
        settings=default_settings(),
    )
    assert quote.status == QuoteStatus.PENDING_CONFIRM
    assert task.raw_result is not None
    assert task.raw_result["planCount"] == 2
    assert len(task.raw_result["plans"]) == 2
    assert await load_coverages(db_session, quote.id) == []
    evidences = (
        (
            await db_session.execute(
                select(FieldEvidence).where(FieldEvidence.quote_id == quote.id)
            )
        )
        .scalars()
    )
    assert evidences.first() is None  # 未写任何标量证据


async def test_mixed_insurer_batch_fails_explicitly(db_session) -> None:
    """一批含不同保险公司：明确错误停止，不写 rawResult 之外的任何数据。"""
    task, quote, files = await prepare_quote(
        db_session, insurer_code="PINGAN", insurer_name="平安"
    )
    with pytest.raises(ParseTaskFailure) as exc_info:
        await apply_extraction(
            db_session,
            task=task,
            quote=quote,
            files=files,
            extraction=parse_fixture("mixed_insurers.json"),
            settings=default_settings(),
        )
    assert "不同保险公司" in exc_info.value.user_message
    assert task.raw_result is None
    assert quote.status == QuoteStatus.PARSING  # 状态交由 worker 收敛


# ---- 证据解析器与隐私 ----


def test_evidence_resolver_missing_vs_invalid() -> None:
    files = [ParseTaskFileInput(11, "F1", "p", "image/jpeg", 2)]
    resolver = EvidenceResolver(files)
    # 无证据 → missing（MEDIUM 语义）
    assert resolver.resolve(None).state == "missing"
    # 合法 fileKey + 页码 → ok 并携带真实文件 id
    ok = resolver.resolve(evidence("F1", 2, "摘录"))
    assert ok.state == "ok"
    assert ok.file_id == 11
    assert ok.page == 2
    # 未知 fileKey / 越界页码 → invalid（LOW 且不建链）
    assert resolver.resolve(evidence("F2", 1, None)).state == "invalid"
    assert resolver.resolve(evidence("F1", 3, None)).state == "invalid"


async def test_sensitive_content_sanitized_before_persist(db_session) -> None:
    """隐私：手机号/车牌/VIN/身份证/个人字段标签在落库前被脱敏（验证 4）。"""
    payload = load_fixture("picc_full.json")
    # 在未脱敏“模型输出”中注入测试敏感数据（仅存在于内存中的模拟响应）
    plan = payload["plans"][0]
    plan["coreCoverages"][0]["evidence"]["text"] = "车损确认单 车主：张三 13812345678"
    plan["annotations"][0]["content"] = "联系京A12345 车主已确认"
    plan["unmatchedItems"][0]["rawText"] = "VIN号LFV3A23C8K3124567 的延保 50元"
    payload["vehicle"]["model"]["rawValue"] = "特斯拉Model Y 身份证110101199001011234"

    task, quote, files = await prepare_quote(db_session)
    await apply_extraction(
        db_session,
        task=task,
        quote=quote,
        files=files,
        extraction=parse_extraction(payload),
        settings=default_settings(),
    )

    secrets = (
        "13812345678",
        "京A12345",
        "LFV3A23C8K3124567",
        "110101199001011234",
        "张三",
    )
    # rawResult 整树脱敏
    raw_text = json.dumps(task.raw_result, ensure_ascii=False)
    for secret in secrets:
        assert secret not in raw_text
    # 明细与证据不落敏感原文
    rows = await load_coverages(db_session, quote.id)
    for row in rows:
        for value in (row.source_text, row.description, row.raw_name):
            if value:
                for secret in secrets:
                    assert secret not in value
    annotations = (
        (await db_session.execute(select(SalesAnnotation))).scalars().all()
    )
    assert annotations
    for secret in secrets:
        assert secret not in annotations[0].content
    evidences = (
        (await db_session.execute(select(FieldEvidence))).scalars().all()
    )
    for row in evidences:
        for value in (row.source_text, row.raw_value):
            if value:
                for secret in secrets:
                    assert secret not in value


def test_hidden_text_replacement_for_unsanitizable_excerpt() -> None:
    """摘录整体命中“个人字段标签+取值”被删除时 → HIDDEN_TEXT 占位。"""
    assert sanitize_evidence_text("被保险人：李四") == HIDDEN_TEXT
    assert sanitize_evidence_text("正常摘录内容") == "正常摘录内容"
    assert sanitize_evidence_text("") is None
