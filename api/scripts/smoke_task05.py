"""TASK-05 全栈冒烟：多方案拆分、待确认重解析与已确认补传合并主链路。

与 smoke_task04 相同的口径：真实 API + 真实 lifespan worker + 一次性
PostgreSQL，视觉模型以“固定脱敏 fixture / 脚本化异常的假 provider”替换，
其余环节全部为生产代码路径。

覆盖主路径（TASKS.md 验证 1、3、4 的端到端版本）：
1. 多方案拆分：PINGAN 容器上传 → SUCCEEDED+planCount=2 → 拆分预览 →
   确认拆分（改标签+丢弃）→ 子报价平级 PENDING_CONFIRM + 共享原文件 →
   删除一个子报价不影响兄弟预览原文件
2. 待确认重解析：失败回 PENDING_CONFIRM 保留候选（用户编辑值不丢）；
   成功只覆盖未编辑候选
3. 已确认补传合并：补传只解析新增文件 → MERGE_REVIEW + 旧值可读 →
   merge-preview 展示旧值/新值/用户编辑标识 → 逐项 ACCEPT → 原子合并
   回 CONFIRMED + 价格重算
4. 补传解析失败：报价保持 CONFIRMED、旧数据可对比
5. 同一报价活动任务互斥 409（并发重解析）

用法：uv run python scripts/smoke_task05.py
"""

from __future__ import annotations

import asyncio
import copy
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
DB_NAME = "car_insurance_smoke05"
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


# ---- 假 provider：脚本化返回 fixture/异常（空脚本时安全失败）----


class ScriptedVisionClient:
    provider = "smoke-fixture"
    model = "fixture-raw-result"
    # 类属性脚本队列：lifespan 创建的实例与冒烟流程共享同一队列；
    # 每次模型调用弹出一项（dict=固定脱敏抽取结果；Exception=按失败分类
    # 抛出），队列排空后安全失败，绝不假装成功
    script: list = []

    async def extractQuote(self, pages):
        from app.services.parser.extraction_schema import parse_extraction
        from app.services.parser.pipeline import ParseConfigError

        if not ScriptedVisionClient.script:
            raise ParseConfigError("冒烟假 provider 脚本已排空（预期外的模型调用）")
        item = ScriptedVisionClient.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return parse_extraction(item)


def build_smoke_pipeline(settings, session_factory):
    from app.services.parser.pipeline import VisionParsePipeline

    return VisionParsePipeline(settings, session_factory, ScriptedVisionClient())


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def changed_payload() -> dict:
    """相对 picc_full 的定点变化版：商业险/三者保额变化 + 划痕新增。"""
    payload = copy.deepcopy(load_fixture("picc_full.json"))
    plan = payload["plans"][0]
    plan["pricing"]["commercialPremium"]["value"] = 4500.0
    plan["pricing"]["officialTotal"]["value"] = 5945.0
    for row in plan["coreCoverages"]:
        if "第三者" in row["rawName"]:
            row["coverageAmount"] = 5000000.0
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
    return payload


async def wait_terminal(client: httpx.AsyncClient, quote_id: int, timeout: float = 30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(f"/api/quotes/{quote_id}/parse-status")
        data = response.json()["data"]
        if data["status"] in ("SUCCEEDED", "FAILED"):
            return data
        await asyncio.sleep(0.3)
    raise TimeoutError(f"报价 {quote_id} 解析任务未在时限内到达终态")


async def smoke_flow(database_url: str, upload_dir: str) -> None:
    import os

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
    app_main.build_parse_pipeline = build_smoke_pipeline
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        from app.services.file_cleanup import (
            LocalFileCleanupService,
            set_file_cleanup_service,
        )

        set_file_cleanup_service(LocalFileCleanupService(settings))

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://smoke") as client:
            from app.services.parser.pipeline import ParseConfigError

            # 类属性即脚本句柄（lifespan 实例读取同一队列）
            scripted = ScriptedVisionClient

            # ---- 1. 项目 + PINGAN 容器 + 多方案解析 ----
            response = await client.post(
                "/api/projects",
                json={"name": "2026 车辆续保", "vehicleName": "Model Y", "renewalYear": 2026},
            )
            check("创建项目 201", response.status_code == 201, response.text[:80])
            project_id = response.json()["data"]["id"]
            response = await client.post(
                f"/api/projects/{project_id}/quotes",
                json={"insurerCode": "PINGAN", "source": "UPLOADED"},
            )
            container_id = response.json()["data"]["id"]

            scripted.script = [load_fixture("multi_plan_same_insurer.json")]
            response = await client.post(
                f"/api/quotes/{container_id}/files",
                files=[
                    ("files", ("方案单.pdf", pdf_bytes(1), "application/pdf")),
                    ("files", ("拍照.jpg", jpeg_bytes(), "image/jpeg")),
                ],
                data={"modelProcessingConsent": "true"},
            )
            check("多方案容器上传 202", response.status_code == 202)
            status_data = await wait_terminal(client, container_id)
            check(
                "同公司多方案 SUCCEEDED + planCount=2",
                status_data["status"] == "SUCCEEDED" and status_data["planCount"] == 2,
                str(status_data),
            )

            # ---- 2. 拆分预览 + 确认拆分（改标签 + 丢弃无效方案）----
            response = await client.get(f"/api/quotes/{container_id}/plan-split")
            check(
                "拆分预览 200 + 两方案标签",
                response.status_code == 200
                and [p["planLabel"] for p in response.json()["data"]["plans"]] == ["方案A", "方案B"],
                response.text[:120],
            )
            response = await client.post(
                f"/api/quotes/{container_id}/plan-split",
                json={"plans": [{"index": 0, "planLabel": "低配"}, {"index": 1}]},
            )
            check(
                "确认拆分 201（丢弃原始标签方案 B 改用模型标签）",
                response.status_code == 201 and len(response.json()["data"]["quotes"]) == 2,
                response.text[:120],
            )
            children = response.json()["data"]["quotes"]
            check(
                "子报价平级 PENDING_CONFIRM + 用户改写标签生效",
                all(q["status"] == "PENDING_CONFIRM" for q in children)
                and children[0]["planLabel"] == "低配"
                and children[1]["planLabel"] == "方案B",
                str([q["planLabel"] for q in children]),
            )
            check(
                "容器报价已删除（404）",
                (await client.get(f"/api/quotes/{container_id}")).status_code == 404,
            )

            first_detail = (await client.get(f"/api/quotes/{children[0]['id']}")).json()["data"]
            second_detail = (await client.get(f"/api/quotes/{children[1]['id']}")).json()["data"]
            first_files = {f["id"] for f in first_detail["files"]}
            second_files = {f["id"] for f in second_detail["files"]}
            check(
                "子报价共享同一批原文件（fileId 集合一致）",
                first_files == second_files and len(first_files) == 2,
                str(first_files),
            )
            raw = await client.get(
                f"/api/files/{sorted(first_files)[0]}/raw",
                params={"projectId": project_id},
            )
            check("子报价可 inline 读取共享原文件", raw.status_code == 200)

            # 删除一个子报价 → 兄弟仍能读原文件（无引用规则保护共享资产）
            response = await client.delete(f"/api/quotes/{children[0]['id']}")
            check("删除子报价 200", response.status_code == 200)
            raw_after = await client.get(
                f"/api/files/{sorted(second_files)[0]}/raw",
                params={"projectId": project_id},
            )
            check(
                "删除后兄弟报价仍可读原文件",
                raw_after.status_code == 200,
            )

            # ---- 3. 待确认重解析：失败保留候选 + 用户编辑保护 ----
            survivor_id = children[1]["id"]
            # 子报价方案B商业险候选 3200；先补一次完整方案解析（单方案 fixture）
            scripted.script = [load_fixture("picc_full.json")]
            response = await client.post(f"/api/quotes/{survivor_id}/reparse", data={})
            check("待确认重解析 202", response.status_code == 202, response.text[:80])
            status_data = await wait_terminal(client, survivor_id)
            detail = (await client.get(f"/api/quotes/{survivor_id}")).json()["data"]
            check(
                "重解析成功：PENDING_CONFIRM + 未编辑候选被覆盖",
                status_data["status"] == "SUCCEEDED"
                and detail["status"] == "PENDING_CONFIRM"
                and detail["commercialPremium"] == 4093.91,
                f"status={status_data['status']} commercial={detail['commercialPremium']}",
            )
            # 用户编辑 → 再重解析（失败）：回 PENDING_CONFIRM 且编辑保留
            response = await client.patch(
                f"/api/quotes/{survivor_id}", json={"commercialPremium": "5000.00"}
            )
            check("用户编辑商业险 200", response.status_code == 200)

            scripted.script = [ParseConfigError("视觉模型鉴权失败（HTTP 401），请检查 VISION_API_KEY 配置")]
            await client.post(f"/api/quotes/{survivor_id}/reparse", data={})
            status_data = await wait_terminal(client, survivor_id)
            detail = (await client.get(f"/api/quotes/{survivor_id}")).json()["data"]
            check(
                "待确认重解析失败：回 PENDING_CONFIRM 保留用户编辑值",
                detail["status"] == "PENDING_CONFIRM" and detail["commercialPremium"] == 5000.0,
                f"status={detail['status']} commercial={detail['commercialPremium']}",
            )

            # 活动任务互斥：上传新文件后立即 reparse → 409
            scripted.script = []
            await client.post(
                f"/api/quotes/{survivor_id}/files",
                files=[("files", ("补充.jpg", jpeg_bytes(), "image/jpeg"))],
            )
            conflict = await client.post(f"/api/quotes/{survivor_id}/reparse", data={})
            check(
                "并发重解析 409（活动任务互斥）",
                conflict.status_code == 409,
                conflict.text[:80],
            )
            await wait_terminal(client, survivor_id)  # 等该任务终态，不产生并发

            # ---- 4. 已确认补传合并主路径 ----
            # 先让这次“转手动后待确认”的报价确认掉（价格分项完整）
            response = await client.post(
                f"/api/quotes/{survivor_id}/confirm",
                json={"insurerConflictResolution": "USE_MODEL"},
            )
            check(
                "待确认报价确认 → CONFIRMED",
                response.status_code == 200
                and response.json()["data"]["status"] == "CONFIRMED",
                response.text[:120],
            )
            confirmed_detail = response.json()["data"]
            old_commercial = confirmed_detail["commercialPremium"]

            # 补传 1 个新文件（任务输入=仅新增文件）+ 变化版解析
            scripted.script = [changed_payload()]
            upload = await client.post(
                f"/api/quotes/{survivor_id}/files",
                files=[("files", ("新报价.pdf", pdf_bytes(2), "application/pdf"))],
            )
            check(
                "CONFIRMED 补传 202 且报价保持 CONFIRMED",
                upload.status_code == 202
                and (await client.get(f"/api/quotes/{survivor_id}")).json()["data"]["status"]
                == "CONFIRMED",
                upload.text[:80],
            )
            status_data = await wait_terminal(client, survivor_id)
            detail = (await client.get(f"/api/quotes/{survivor_id}")).json()["data"]
            check(
                "补传解析成功：MERGE_REVIEW + 旧值未被改写",
                status_data["status"] == "SUCCEEDED"
                and detail["status"] == "MERGE_REVIEW"
                and detail["commercialPremium"] == old_commercial,
                f"status={detail['status']} commercial={detail['commercialPremium']}",
            )

            preview = await client.get(f"/api/quotes/{survivor_id}/merge-preview")
            changes = preview.json()["data"]["changes"]
            check(
                "merge-preview 变更清单含标量冲突与新增行",
                preview.status_code == 200
                and any(
                    c["entityType"] == "scalar"
                    and c["entityKey"] == "commercialPremium"
                    and c["kind"] == "CONFLICT"
                    for c in changes
                )
                and any(c["kind"] == "ADD" for c in changes),
                str([(c["entityType"], c["entityKey"], c["kind"]) for c in changes]),
            )
            commercial_change = next(
                c for c in changes if c["entityType"] == "scalar" and c["entityKey"] == "commercialPremium"
            )
            check(
                "用户编辑项默认 KEEP（旧值 5000 不被静默覆盖）",
                commercial_change["userEdited"] is True
                and commercial_change["defaultResolution"] == "KEEP"
                and commercial_change["oldValue"]["value"] == 5000.0
                and commercial_change["newValue"]["value"] == 4500.0,
                str(commercial_change["oldValue"]),
            )

            # 部分解决 → 422；全部解决 → 原子合并回 CONFIRMED
            partial = await client.post(
                f"/api/quotes/{survivor_id}/merge-resolve",
                json={"resolutions": [{"changeId": changes[0]["id"], "resolution": "KEEP"}]},
            )
            resolved = await client.post(
                f"/api/quotes/{survivor_id}/merge-resolve",
                json={
                    "resolutions": [
                        {"changeId": c["id"], "resolution": c["defaultResolution"]}
                        for c in changes
                    ]
                },
            )
            merged = resolved.json()["data"]
            scratch_rows = [c for c in merged["coverages"] if c["code"] == "SCRATCH"]
            check(
                "部分解决 422（未全部裁决不得合并）",
                partial.status_code == 422,
                partial.text[:80],
            )
            check(
                "全部按默认裁决解决：回 CONFIRMED + 划痕行合入 + 用户编辑值保留",
                resolved.status_code == 200
                and merged["status"] == "CONFIRMED"
                and len(scratch_rows) == 1
                and merged["commercialPremium"] == 5000.0,
                f"status={merged['status']} commercial={merged['commercialPremium']}",
            )

            # ---- 5. 补传解析失败：保持 CONFIRMED、旧数据可读 ----
            scripted.script = [
                ParseConfigError("视觉模型鉴权失败（HTTP 401），请检查 VISION_API_KEY 配置")
            ]
            await client.post(
                f"/api/quotes/{survivor_id}/files",
                files=[("files", ("再来一张.jpg", jpeg_bytes(), "image/jpeg"))],
            )
            status_data = await wait_terminal(client, survivor_id)
            detail = (await client.get(f"/api/quotes/{survivor_id}")).json()["data"]
            check(
                "补传解析失败：报价保持 CONFIRMED、已确认数据可读",
                status_data["status"] == "FAILED"
                and detail["status"] == "CONFIRMED"
                and detail["commercialPremium"] == 5000.0,
                f"status={detail['status']}",
            )


def main() -> int:
    import os
    import subprocess

    import asyncpg

    pg = EmbeddedPostgres()
    maintenance_url = pg.start()
    database_url = maintenance_url.rsplit("/", 1)[0] + "/" + DB_NAME
    upload_dir = tempfile.mkdtemp(prefix="smoke05-uploads-")

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
