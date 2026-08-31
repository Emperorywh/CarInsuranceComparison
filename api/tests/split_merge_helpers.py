"""TASK-05 测试共享助手：假 VisionClient、会话工厂与报价链路搭建。

与 test_parse_pipeline 的注入方式一致：真实上传链路（file_client）+
真实 VisionParsePipeline + 假供应商脚本，全部确定性、不访问网络。
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from app.config import Settings
from app.services.parser import pipeline as pipeline_module
from app.services.parser.extraction_schema import ExtractionResult, parse_extraction
from app.services.parser.pipeline import VisionParsePipeline
from app.services.parser.worker import run_one_task
from tests.files_helpers import jpeg_bytes

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "raw_results"


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


def load_fixture(name: str) -> dict:
    """读取固定脱敏 fixture（dict 形态，便于用例内定点修改）。"""
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def extraction_from(name: str) -> ExtractionResult:
    return parse_extraction(load_fixture(name))


def extraction_from_payload(payload: dict) -> ExtractionResult:
    return parse_extraction(json.loads(json.dumps(payload, ensure_ascii=False)))


def session_factory(db_session):
    """在共享连接上打开独立会话（savepoint 语义）。

    正式 VisionParsePipeline 在 execute 内部使用 `async with db.begin()`
    显式开事务，必须给它绑定同一 Connection 的新会话。
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


async def run_parse(
    db_session,
    file_upload_settings: Settings,
    script: list,
) -> None:
    """注入假供应商流水线并执行一个完整任务周期（终态返回）。"""
    client = FakeVisionClient(script)
    pipeline = VisionParsePipeline(file_upload_settings, session_factory(db_session), client)
    pipeline_module.set_parse_pipeline(pipeline)
    await run_one_task(session_factory(db_session), pipeline)


async def setup_project_and_quote(
    file_client,
    *,
    insurer_code: str = "PICC",
) -> tuple[int, int]:
    """创建项目与 UPLOADED 报价容器，返回 (projectId, quoteId)。"""
    response = await file_client.post(
        "/api/projects",
        json={"name": "合并测试项目", "vehicleName": "Model Y", "renewalYear": 2026},
    )
    project_id = response.json()["data"]["id"]
    response = await file_client.post(
        f"/api/projects/{project_id}/quotes",
        json={"insurerCode": insurer_code, "source": "UPLOADED"},
    )
    quote_id = response.json()["data"]["id"]
    return project_id, quote_id


async def upload_files(file_client, quote_id: int, count: int = 2) -> int:
    """上传 count 个 JPEG 并返回 taskId（202 链路）。"""
    response = await file_client.post(
        f"/api/quotes/{quote_id}/files",
        files=[
            ("files", (f"报价单{i}.jpg", jpeg_bytes(), "image/jpeg"))
            for i in range(count)
        ],
        data={"modelProcessingConsent": "true"},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]["taskId"]
