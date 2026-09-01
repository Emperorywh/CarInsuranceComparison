"""解析流水线集成测试（TASK-04 验证 5）与页面准备/provider 单测。

- 集成：注入假 VisionClient 驱动真实 VisionParsePipeline + worker，
  走“上传 → 领取 → 页面准备 → 假模型 → 候选落库 → 状态迁移”全链路；
- provider：httpx MockTransport 验证失败分类（401 不重试、429 可重试、
  非 JSON 可重试、合法内容返回结构化结果）；
- 页面准备：EXIF 方向纠正、长边缩放、PDF 逐页渲染与 fileKey/page 分配。
"""

from __future__ import annotations

import io
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.models import ParseTask, Quote
from app.models.enums import ParseTaskStatus, QuoteStatus
from app.services.parser import pipeline as pipeline_module
from app.services.parser.extraction_schema import parse_extraction
from app.services.parser.openai_provider import OpenAICompatibleVisionClient
from app.services.parser.pdf import _prepare_image, _prepare_pages_sync
from app.services.parser.pipeline import (
    ParseConfigError,
    ParseInputError,
    ParseRetryableError,
    ParseTaskFileInput,
    VisionParsePipeline,
    build_parse_pipeline,
)
from app.services.parser.worker import run_one_task
from tests.files_helpers import jpeg_bytes, pdf_bytes
from tests.test_candidate_writer import load_fixture

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "raw_results"


@pytest.fixture(autouse=True)
def _restore_pipeline():
    yield
    from app.services.parser.pipeline import UnconfiguredVisionPipeline

    pipeline_module.set_parse_pipeline(UnconfiguredVisionPipeline())


class FakeVisionClient:
    """按脚本产出 ExtractionResult 或异常的确定性假供应商。"""

    provider = "fake-vision"
    model = "fake-model"

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.pages_seen: list[list] = []

    async def extractQuote(self, pages):
        self.pages_seen.append(pages)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def extraction_from(name: str):
    return parse_extraction(json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")))


def _session_factory(db_session):
    """在共享连接上打开独立会话（savepoint 语义）。

    与 TASK-03 假 pipeline 不同，正式 VisionParsePipeline 在 execute 内部
    使用 `async with db.begin()` 显式开事务；共享同一个 AsyncSession 会
    与外层事务冲突，因此每次给 pipeline 一个绑定同一 Connection 的新
    会话（join_transaction_mode="create_savepoint" 保证用例可回滚）。
    """

    from sqlalchemy.ext.asyncio import AsyncSession

    @asynccontextmanager
    async def _factory():
        async with AsyncSession(
            bind=db_session.bind,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            yield session

    return _factory


async def _upload_two_files(file_client) -> tuple[int, int, Settings]:
    """经真实上传链路准备 JPG + 2 页 PDF 的 PENDING 任务。"""
    response = await file_client.post(
        "/api/projects",
        json={"name": "流水线项目", "vehicleName": "Model Y", "renewalYear": 2026},
    )
    project_id = response.json()["data"]["id"]
    response = await file_client.post(
        f"/api/projects/{project_id}/quotes",
        json={"insurerCode": "PICC", "source": "UPLOADED"},
    )
    quote_id = response.json()["data"]["id"]
    upload = await file_client.post(
        f"/api/quotes/{quote_id}/files",
        files=[
            ("files", ("商业险.jpg", jpeg_bytes(), "image/jpeg")),
            ("files", ("条款.pdf", pdf_bytes(2), "application/pdf")),
        ],
        data={"modelProcessingConsent": "true"},
    )
    assert upload.status_code == 202
    task_id = upload.json()["data"]["taskId"]
    return quote_id, task_id, file_client


# ---- 集成：全链路成功与状态迁移 ----


async def test_pipeline_success_writes_candidates(file_client, db_session, file_upload_settings) -> None:
    quote_id, task_id, _ = await _upload_two_files(file_client)
    client = FakeVisionClient([extraction_from("picc_full.json")])
    pipeline = VisionParsePipeline(file_upload_settings, _session_factory(db_session), client)
    pipeline_module.set_parse_pipeline(pipeline)

    executed = await run_one_task(_session_factory(db_session), pipeline)
    assert executed is True

    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.SUCCEEDED
    assert task.raw_result is not None
    assert task.raw_result["insurer"]["name"] == "人保"
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PENDING_CONFIRM
    assert quote.commercial_premium is not None

    # 单次多图调用：JPG 1 页 + PDF 2 页 = 3 页，fileKey/page 后端分配
    assert len(client.pages_seen) == 1
    pages = client.pages_seen[0]
    assert [(p["fileKey"], p["page"]) for p in pages] == [
        ("F1", 1),
        ("F2", 1),
        ("F2", 2),
    ]
    # 全部重编码为 PNG（PDF 渲染与图片统一入模格式）
    assert all(p["content"].startswith(b"\x89PNG") for p in pages)
    assert all(p["mimeType"] == "image/png" for p in pages)


async def test_pipeline_retry_on_retryable_failure(file_client, db_session, file_upload_settings) -> None:
    quote_id, task_id, _ = await _upload_two_files(file_client)
    client = FakeVisionClient(
        [ParseRetryableError("模型请求超时，已自动重试"), extraction_from("picc_full.json")]
    )
    pipeline = VisionParsePipeline(file_upload_settings, _session_factory(db_session), client)
    pipeline_module.set_parse_pipeline(pipeline)

    await run_one_task(_session_factory(db_session), pipeline)
    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.PENDING  # 回队等待下次领取
    assert task.attempt == 1
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PARSING

    await run_one_task(_session_factory(db_session), pipeline)
    # 其他会话已推进任务状态；清掉本会话身份映射缓存再取最新值
    db_session.expire_all()
    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.SUCCEEDED
    assert task.attempt == 2


async def test_pipeline_auth_error_fails_without_retry(file_client, db_session, file_upload_settings) -> None:
    quote_id, task_id, _ = await _upload_two_files(file_client)
    client = FakeVisionClient([ParseConfigError("视觉模型鉴权失败（HTTP 401），请检查 VISION_API_KEY 配置")])
    pipeline = VisionParsePipeline(file_upload_settings, _session_factory(db_session), client)
    pipeline_module.set_parse_pipeline(pipeline)

    await run_one_task(_session_factory(db_session), pipeline)
    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.FAILED
    assert task.attempt == 1  # 鉴权失败不重试
    assert "401" in task.error
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PARSE_FAILED


async def test_pipeline_empty_plans_fails(file_client, db_session, file_upload_settings) -> None:
    quote_id, task_id, _ = await _upload_two_files(file_client)
    client = FakeVisionClient([extraction_from("multi_plan_same_insurer.json").model_copy(update={"plans": [], "planCount": 0})])
    pipeline = VisionParsePipeline(file_upload_settings, _session_factory(db_session), client)
    pipeline_module.set_parse_pipeline(pipeline)

    await run_one_task(_session_factory(db_session), pipeline)
    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.FAILED
    assert "未识别到报价内容" in task.error
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PARSE_FAILED


async def test_pipeline_multi_plan_placeholder(file_client, db_session, file_upload_settings) -> None:
    """同公司多方案：任务成功、报价回 PENDING_CONFIRM、parse-status 暴露 planCount。"""
    quote_id, task_id, _ = await _upload_two_files(file_client)
    client = FakeVisionClient([extraction_from("multi_plan_same_insurer.json")])
    pipeline = VisionParsePipeline(file_upload_settings, _session_factory(db_session), client)
    pipeline_module.set_parse_pipeline(pipeline)

    await run_one_task(_session_factory(db_session), pipeline)
    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.SUCCEEDED
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PENDING_CONFIRM

    status_response = await file_client.get(f"/api/quotes/{quote_id}/parse-status")
    data = status_response.json()["data"]
    assert data["planCount"] == 2
    assert data["quoteStatus"] == "PENDING_CONFIRM"


async def test_pipeline_mixed_insurer_fails(file_client, db_session, file_upload_settings) -> None:
    quote_id, task_id, _ = await _upload_two_files(file_client)
    client = FakeVisionClient([extraction_from("mixed_insurers.json")])
    pipeline = VisionParsePipeline(file_upload_settings, _session_factory(db_session), client)
    pipeline_module.set_parse_pipeline(pipeline)

    await run_one_task(_session_factory(db_session), pipeline)
    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.FAILED
    assert "不同保险公司" in task.error
    quote = await db_session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.PARSE_FAILED


async def test_pipeline_corrupt_input_fails_without_retry(file_client, db_session, file_upload_settings) -> None:
    """存储后文件被破坏：确定性输入错误不可重试，直接终态失败。"""
    quote_id, task_id, _ = await _upload_two_files(file_client)
    # 把 F1 的磁盘内容破坏成非法图片字节（模拟存储层损坏）
    from sqlalchemy import select as _select

    from app.models import QuoteFile
    from app.services.storage import local_files

    first_file = (
        await db_session.execute(_select(QuoteFile).order_by(QuoteFile.id))
    ).scalars().first()
    absolute = local_files.resolve_absolute(file_upload_settings, first_file.file_path)
    absolute.write_bytes(b"\xff\xd8\xff not-a-real-jpeg")

    client = FakeVisionClient([])
    pipeline = VisionParsePipeline(file_upload_settings, _session_factory(db_session), client)
    pipeline_module.set_parse_pipeline(pipeline)

    await run_one_task(_session_factory(db_session), pipeline)
    task = await db_session.get(ParseTask, task_id)
    assert task.status == ParseTaskStatus.FAILED
    assert task.attempt == 1  # 输入损坏重试无意义
    assert client.pages_seen == []  # 未到达模型调用


# ---- provider 失败分类（MockTransport，不访问网络）----


def _mock_client(handler) -> OpenAICompatibleVisionClient:
    """带 MockTransport 的 provider 实例（不访问网络）。"""
    return OpenAICompatibleVisionClient(
        base_url="https://vision.example.com/v1",
        api_key="k",
        model="m",
        transport=httpx.MockTransport(handler),
    )


async def test_provider_maps_401_to_config_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    client = _mock_client(handler)
    with pytest.raises(ParseConfigError) as exc_info:
        await client.extractQuote([])
    assert "401" in exc_info.value.user_message
    assert "VISION_API_KEY" in exc_info.value.user_message


async def test_provider_maps_429_to_retryable() -> None:
    client = _mock_client(lambda request: httpx.Response(429, json={}))
    with pytest.raises(ParseRetryableError):
        await client.extractQuote([])


async def test_provider_maps_5xx_to_retryable() -> None:
    client = _mock_client(lambda request: httpx.Response(502))
    with pytest.raises(ParseRetryableError):
        await client.extractQuote([])


async def test_provider_maps_413_to_page_hint() -> None:
    client = _mock_client(lambda request: httpx.Response(413))
    with pytest.raises(ParseConfigError) as exc_info:
        await client.extractQuote([])
    assert "MAX_TOTAL_PAGES_PER_QUOTE" in exc_info.value.user_message


async def test_provider_invalid_json_is_retryable() -> None:
    client = _mock_client(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "抱歉，我无法解析"}}]}
        )
    )
    with pytest.raises(ParseRetryableError) as exc_info:
        await client.extractQuote([])
    assert "JSON" in exc_info.value.user_message


async def test_provider_parses_fenced_json_and_validates_schema() -> None:
    raw = json.dumps(load_fixture("unknown_coverage.json"), ensure_ascii=False)
    content = f"```json\n{raw}\n```"
    client = _mock_client(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )
    )
    result = await client.extractQuote([])
    assert result.insurer.name == "大地"


async def test_provider_thinking_param_injection() -> None:
    # 配置 vision_thinking 时随请求体下发，未配置时不发（走模型默认）
    raw = json.dumps(load_fixture("unknown_coverage.json"), ensure_ascii=False)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})

    client = OpenAICompatibleVisionClient(
        base_url="https://vision.example.com/v1",
        api_key="k",
        model="m",
        transport=httpx.MockTransport(handler),
        thinking="disabled",
    )
    await client.extractQuote([])
    assert captured["payload"]["thinking"] == {"type": "disabled"}

    captured.clear()
    default_client = _mock_client(handler)
    await default_client.extractQuote([])
    assert "thinking" not in captured["payload"]


async def test_provider_schema_violation_is_retryable() -> None:
    # planCount 与 plans 长度不一致 → Schema 校验失败（可重试）
    payload = load_fixture("unknown_coverage.json")
    payload["planCount"] = 3
    content = json.dumps(payload, ensure_ascii=False)
    client = _mock_client(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )
    )
    with pytest.raises(ParseRetryableError):
        await client.extractQuote([])


# ---- 页面准备（EXIF/缩放/PDF 渲染）----


def _jpeg_with_orientation(orientation: int, size: tuple[int, int]) -> bytes:
    image = Image.new("RGB", size, (200, 30, 30))
    buffer = io.BytesIO()
    from PIL.Image import Exif

    exif = Exif()
    exif[274] = orientation  # 0x0112 Orientation
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def test_page_prep_exif_rotation_and_scaling(tmp_path) -> None:
    settings = Settings(
        app_bind_host="127.0.0.1", upload_dir=str(tmp_path), max_image_long_edge=100
    )
    # 方向 6：需要顺时针旋转 90° 才能正立 —— 输出宽高互换
    data = _jpeg_with_orientation(6, (40, 80))
    images = _prepare_image(data, settings.max_image_long_edge, "文件 F1")
    assert images[0].size == (80, 40)
    # 长边超过上限：等比缩小到 100
    big = jpeg_bytes(size=(400, 200))
    images = _prepare_image(big, settings.max_image_long_edge, "文件 F1")
    assert max(images[0].size) == 100


def test_page_prep_pdf_renders_each_page(tmp_path) -> None:
    settings = Settings(
        app_bind_host="127.0.0.1", upload_dir=str(tmp_path), max_image_long_edge=2400
    )
    target = tmp_path / "x.pdf"
    target.write_bytes(pdf_bytes(2))
    pages = _prepare_pages_sync(
        settings,
        [
            ParseTaskFileInput(
                1, "F1", str(target.relative_to(tmp_path)), "application/pdf", 2
            )
        ],
    )
    assert [(p["fileKey"], p["page"]) for p in pages] == [("F1", 1), ("F1", 2)]
    assert all(p["content"].startswith(b"\x89PNG") for p in pages)


def test_page_prep_rejects_total_page_overflow(tmp_path) -> None:
    settings = Settings(
        app_bind_host="127.0.0.1",
        upload_dir=str(tmp_path),
        max_total_pages_per_quote=2,
    )
    data = pdf_bytes(3)
    target = tmp_path / "x.pdf"
    target.write_bytes(data)
    with pytest.raises(ParseConfigError) as exc_info:
        _prepare_pages_sync(
            settings,
            [
                ParseTaskFileInput(
                    1, "F1", str(target.relative_to(tmp_path)), "application/pdf", 3
                )
            ],
        )
    assert "MAX_TOTAL_PAGES_PER_QUOTE" in exc_info.value.user_message


def test_page_prep_corrupt_image_is_non_retryable() -> None:
    with pytest.raises(ParseInputError):
        _prepare_image(b"\xff\xd8\xff broken", 100, "文件 F1")


# ---- 装配 ----


def test_build_parse_pipeline_unconfigured_returns_fallback(db_session, monkeypatch) -> None:
    # 隔离本机 .env 与环境变量：该测试必须观察到"未配置"状态
    monkeypatch.delenv("VISION_BASE_URL", raising=False)
    monkeypatch.delenv("VISION_API_KEY", raising=False)
    monkeypatch.delenv("VISION_MODEL", raising=False)
    settings = Settings(app_bind_host="127.0.0.1", _env_file=None)
    assert not settings.vision_base_url
    pipeline = build_parse_pipeline(settings, None)
    assert pipeline.model == "not-configured"


def test_build_parse_pipeline_configured(tmp_path) -> None:
    settings = Settings(
        app_bind_host="127.0.0.1",
        vision_base_url="https://vision.example.com/v1",
        vision_api_key="k",
        vision_model="glm-4.5v",
    )
    pipeline = build_parse_pipeline(settings, None)
    assert isinstance(pipeline, VisionParsePipeline)
    assert pipeline.provider == "openai-compatible"
    assert pipeline.model == "glm-4.5v"
