"""TASK-04 全栈冒烟：真实 API + 真实 worker + 一次性 PostgreSQL 上的解析主链路。

与 smoke_task03 的差异：视觉模型供应商调用以“固定脱敏 fixture 的假
provider”替换（TASKS.md 统一规则 9：常规测试不得调用真实视觉模型），
其余环节（上传预检、落盘、worker 领取、页面准备、Schema 校验、脱敏、
归一化、候选落库、状态迁移、确认）全部为生产代码路径。

覆盖主路径：
1. 人保全量 fixture：上传 202 → 轮询 SUCCEEDED → 候选落库
   （PENDING_CONFIRM + 险种/服务/保障包/证据 + planCount=1 + 质量警告）
2. 确认接口带公司冲突解决 → CONFIRMED
3. 同公司多方案 fixture：只落 rawResult、报价回 PENDING_CONFIRM、
   parse-status 暴露 planCount=2（多方案待拆分占位）
4. 混合公司 fixture：任务 FAILED + 脱敏中文错误 + 报价 PARSE_FAILED
   → 转手动录入保留文件进入 PENDING_CONFIRM

用法：uv run python scripts/smoke_task04.py
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import time
from pathlib import Path

# Windows 控制台默认 GBK：统一重配为 UTF-8，避免中文/符号打印失败
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import httpx
from pg_server import EmbeddedPostgres  # type: ignore[import-not-found]
from PIL import Image
from pypdf import PdfWriter

API_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = API_ROOT / "tests" / "fixtures" / "raw_results"
DB_NAME = "car_insurance_smoke04"
PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(f"{name}{' | ' + detail if detail else ''}")
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


def jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 240), (30, 144, 255)).save(buffer, format="JPEG")
    return buffer.getvalue()


def pdf_bytes(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def jpeg_with_exif_orientation() -> bytes:
    """带 EXIF 方向标记的手机照片模拟（验证页面准备的 EXIF 纠正）。"""
    from PIL.Image import Exif

    buffer = io.BytesIO()
    exif = Exif()
    exif[274] = 6  # Orientation: rotate 90°
    Image.new("RGB", (400, 200), (255, 140, 0)).save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


# ---- 假 provider：按当前 fixture 文件返回抽取结果（可序列切换）----


class FixtureVisionClient:
    provider = "smoke-fixture"
    model = "fixture-raw-result"
    fixture_name = "picc_full.json"
    pages_seen: list = []

    async def extractQuote(self, pages):
        FixtureVisionClient.pages_seen = pages
        payload = json.loads(
            (FIXTURE_DIR / self.fixture_name).read_text(encoding="utf-8")
        )
        from app.services.parser.extraction_schema import parse_extraction

        return parse_extraction(payload)


def build_smoke_pipeline(settings, session_factory):
    """替换 build_parse_pipeline：装配走生产 VisionParsePipeline 的假客户端。"""
    from app.services.parser.pipeline import VisionParsePipeline

    return VisionParsePipeline(settings, session_factory, FixtureVisionClient())


async def wait_terminal(client: httpx.AsyncClient, quote_id: int, timeout: float = 30):
    """轮询 parse-status 直到任务终态（生产前端同款轮询的紧凑版）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(f"/api/quotes/{quote_id}/parse-status")
        data = response.json()["data"]
        if data["status"] in ("SUCCEEDED", "FAILED"):
            return data
        await asyncio.sleep(0.3)
    raise TimeoutError(f"报价 {quote_id} 解析任务未在时限内到达终态")


async def smoke_flow(database_url: str, upload_dir: str) -> None:
    """在已就绪的数据库上跑完整冒烟链路（含 lifespan 启动的真实 worker）。"""
    import os

    # lifespan 内的全局 engine 读取进程配置（get_settings 缓存）：
    # 必须在导入 app 模块前把一次性库写入环境并清空配置缓存
    os.environ["DATABASE_URL"] = database_url
    os.environ["UPLOAD_DIR"] = upload_dir
    import app.main as app_main
    from app.config import Settings, get_settings
    from app.main import create_app

    get_settings.cache_clear()

    settings = Settings(
        app_bind_host="127.0.0.1",
        local_access_token="",
        database_url=database_url,
        upload_dir=upload_dir,
        max_image_long_edge=800,
    )
    # 注入假 provider 的流水线装配（仅替换模型传输，其余全为生产路径）
    app_main.build_parse_pipeline = build_smoke_pipeline
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        # lifespan 已把真实 worker 跑起来（同生产），补注册本进程的清理服务
        from app.services.file_cleanup import (
            LocalFileCleanupService,
            set_file_cleanup_service,
        )

        set_file_cleanup_service(LocalFileCleanupService(settings))

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://smoke") as client:
            # ---- 1. 项目 + UPLOADED 容器 ----
            response = await client.post(
                "/api/projects",
                json={
                    "name": "2026 车辆续保",
                    "vehicleName": "Model Y",
                    "renewalYear": 2026,
                },
            )
            check("创建项目 201", response.status_code == 201, response.text[:80])
            project_id = response.json()["data"]["id"]

            response = await client.post(
                f"/api/projects/{project_id}/quotes",
                json={"insurerCode": "PICC", "source": "UPLOADED"},
            )
            check("UPLOADED 容器 201", response.status_code == 201)
            quote_id = response.json()["data"]["id"]

            # ---- 2. 未同意 422 → 同意上传 202 ----
            response = await client.post(
                f"/api/quotes/{quote_id}/files",
                files=[("files", ("a.jpg", jpeg_bytes(), "image/jpeg"))],
                data={"modelProcessingConsent": "false"},
            )
            check(
                "首次上传未同意 422",
                response.status_code == 422
                and response.json()["code"] == "MODEL_CONSENT_REQUIRED",
            )

            response = await client.post(
                f"/api/quotes/{quote_id}/files",
                files=[
                    # fixture 证据引用 F1=2 页 PDF、F2=手机拍照图片
                    ("files", ("条款.pdf", pdf_bytes(2), "application/pdf")),
                    ("files", ("手机拍照.jpg", jpeg_with_exif_orientation(), "image/jpeg")),
                ],
                data={"modelProcessingConsent": "true"},
            )
            check(
                "同意后多文件上传 202 + taskId",
                response.status_code == 202 and "taskId" in response.json()["data"],
                response.text[:80],
            )

            # ---- 3. 轮询到 SUCCEEDED，候选落库 ----
            status_data = await wait_terminal(client, quote_id)
            check(
                "任务 SUCCEEDED 且 planCount=1",
                status_data["status"] == "SUCCEEDED" and status_data["planCount"] == 1,
                str(status_data),
            )
            response = await client.get(f"/api/quotes/{quote_id}")
            quote = response.json()["data"]
            check(
                "报价进入 PENDING_CONFIRM",
                quote["status"] == "PENDING_CONFIRM",
            )
            check(
                "价格候选落库（商业险/官方总价/总额校验 PASSED）",
                quote["commercialPremium"] == 4093.91
                and quote["officialTotal"] == 5486.91
                and quote["totalCheckStatus"] == "PASSED",
                f"net={quote['netPayment']}",
            )
            codes = {row["code"] for row in quote["coverages"] if row["code"]}
            check(
                "险种候选归一（三者/司机/乘客/医保外齐全，无交强行）",
                {"THIRD_PARTY_LIABILITY", "DRIVER_LIABILITY", "PASSENGER_LIABILITY", "TP_NON_MEDICAL"}
                <= codes
                and "COMPULSORY" not in codes,
                str(sorted(codes)),
            )
            check(
                "标量证据带来源（officialTotal 指向 F1 第 2 页）",
                any(
                    e["fieldName"] == "officialTotal"
                    and e["sourceFileId"]
                    and e["sourcePage"] == 2
                    for e in quote["evidences"]
                ),
            )
            check(
                "质量警告字段返回（list 结构）",
                isinstance(quote.get("qualityWarnings"), list),
                str(quote.get("qualityWarnings")),
            )
            services = {s["serviceType"]: s["status"] for s in quote["services"]}
            check(
                "明确 0 元服务为 FREE、缺费用服务为 UNKNOWN",
                services.get("ROAD_RESCUE") == "FREE"
                and services.get("INSPECTION") == "UNKNOWN",
                str(services),
            )
            check(
                "销售标注隔离（红字只进 annotations，不影响净支出）",
                len(quote["annotations"]) == 1
                and quote["annotations"][0]["kind"] == "RED_TEXT"
                and quote["netPayment"] == 5486.91,
            )
            pages = FixtureVisionClient.pages_seen
            sizes = [Image.open(io.BytesIO(p["content"])).size for p in pages]
            check(
                "单次多图调用（3 页、fileKey/page 分配、PNG 入模、EXIF 已纠正）",
                len(pages) == 3
                and [(p["fileKey"], p["page"]) for p in pages] == [("F1", 1), ("F1", 2), ("F2", 1)]
                and all(p["content"].startswith(b"\x89PNG") for p in pages)
                # 400x200 + 方向 6 → 纠正为 200x400（EXIF 纠正生效的直接证据）
                and sizes[2] == (200, 400),
                str(sizes),
            )

            # ---- 4. 确认（带公司冲突 KEEP_USER）→ CONFIRMED ----
            response = await client.post(
                f"/api/quotes/{quote_id}/confirm",
                json={
                    "vehicleConflictResolution": None,
                    "insurerConflictResolution": "KEEP_USER",
                },
            )
            check(
                "确认成功进入 CONFIRMED",
                response.status_code == 200
                and response.json()["data"]["status"] == "CONFIRMED",
                response.text[:120],
            )

            # ---- 5. 同公司多方案：只落 rawResult + 占位 ----
            response = await client.post(
                f"/api/projects/{project_id}/quotes",
                json={"insurerCode": "PINGAN", "source": "UPLOADED"},
            )
            multi_quote_id = response.json()["data"]["id"]
            FixtureVisionClient.fixture_name = "multi_plan_same_insurer.json"
            await client.post(
                f"/api/quotes/{multi_quote_id}/files",
                files=[("files", ("多方案.jpg", jpeg_bytes(), "image/jpeg"))],
            )
            status_data = await wait_terminal(client, multi_quote_id)
            response = await client.get(f"/api/quotes/{multi_quote_id}")
            multi_quote = response.json()["data"]
            check(
                "同公司多方案：SUCCEEDED + 报价回 PENDING_CONFIRM + planCount=2",
                status_data["status"] == "SUCCEEDED"
                and status_data["planCount"] == 2
                and multi_quote["status"] == "PENDING_CONFIRM"
                and multi_quote["coverages"] == [],
                str(status_data),
            )

            # ---- 6. 混合公司：明确失败 → PARSE_FAILED → 转手动 ----
            response = await client.post(
                f"/api/projects/{project_id}/quotes",
                json={"insurerCode": "CPIC", "source": "UPLOADED"},
            )
            mixed_quote_id = response.json()["data"]["id"]
            FixtureVisionClient.fixture_name = "mixed_insurers.json"
            await client.post(
                f"/api/quotes/{mixed_quote_id}/files",
                files=[("files", ("混合.jpg", jpeg_bytes(), "image/jpeg"))],
            )
            status_data = await wait_terminal(client, mixed_quote_id)
            response = await client.get(f"/api/quotes/{mixed_quote_id}")
            mixed_quote = response.json()["data"]
            check(
                "混合公司批次：任务 FAILED + 脱敏中文错误",
                status_data["status"] == "FAILED"
                and "不同保险公司" in (status_data["error"] or ""),
                str(status_data["error"]),
            )
            check(
                "混合公司批次：报价 PARSE_FAILED 且无候选",
                mixed_quote["status"] == "PARSE_FAILED"
                and mixed_quote["coverages"] == [],
            )
            response = await client.post(f"/api/quotes/{mixed_quote_id}/convert-manual")
            check(
                "转手动录入保留文件并进入 PENDING_CONFIRM",
                response.status_code == 200
                and response.json()["data"]["status"] == "PENDING_CONFIRM"
                and len(response.json()["data"]["files"]) == 1,
            )


def main() -> int:
    import os
    import subprocess

    import asyncpg

    # PG 启动是同步阻塞流程，必须在 asyncio.run 之外执行（pg_server 约束）
    pg = EmbeddedPostgres()
    maintenance_url = pg.start()
    database_url = maintenance_url.rsplit("/", 1)[0] + "/" + DB_NAME
    upload_dir = tempfile.mkdtemp(prefix="smoke04-uploads-")

    async def _create_db() -> None:
        conn = await asyncpg.connect(
            host="127.0.0.1", port=pg.port, user="postgres", database="postgres"
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{DB_NAME}"')
        await conn.close()

    asyncio.run(_create_db())
    subprocess.run(
        [
            str(API_ROOT / ".venv" / "Scripts" / "python.exe"),
            "-m",
            "alembic",
            "upgrade",
            "head",
        ],
        cwd=str(API_ROOT),
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=True,
    )

    try:
        asyncio.run(smoke_flow(database_url, upload_dir))
    finally:
        pg.stop()

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for item in FAIL:
        print("FAIL:", item)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
