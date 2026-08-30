"""TASK-03 全栈冒烟脚本：真实 uvicorn + 一次性 PostgreSQL 上的文件解析链路。

覆盖（TASK-03 验证 1/2/3/5 的服务端链路）：
创建项目 → UPLOADED 容器（201）→ 未同意 422 → 同意后多文件上传（202 + taskId）
→ 原文件 inline 预览 → 进程内 worker 领取任务（未配置视觉模型 → 安全失败，
绝不假装成功）→ 报价进入 PARSE_FAILED → 重试解析（202）→ 转手动录入
（保留文件，进入 PENDING_CONFIRM）→ 删除项目同步清理磁盘目录。

上传接口无论何种成功路径一律 202 并携带 taskId，无 201 分支；
前端交互（移动视口）由 Vitest 组件测试与生产构建覆盖（同 TASK-01 口径）。

用法：uv run python scripts/smoke_task03.py
"""

from __future__ import annotations

import io
import sys

# Windows 控制台默认 GBK：统一重配为 UTF-8，避免中文/符号打印失败
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import asyncio  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import asyncpg  # noqa: E402
import httpx  # noqa: E402
from pg_server import EmbeddedPostgres  # noqa: E402
from PIL import Image  # noqa: E402
from pypdf import PdfWriter  # noqa: E402

API_ROOT = Path(__file__).resolve().parents[1]
DB_NAME = "car_insurance_smoke03"
PORT = 8032
PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(f"{name}{' | ' + detail if detail else ''}")
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


def request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = (
        json_dumps(payload) if payload is not None else None
    )
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", data=body, method=method
    )
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json_loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            return error.code, json_loads(error.read())
        except Exception:
            return error.code, {}


def json_dumps(payload: dict) -> bytes:
    import json

    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def json_loads(raw: bytes) -> dict:
    import json

    return json.loads(raw.decode("utf-8"))


def jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (120, 90), (30, 144, 255)).save(buffer, format="JPEG")
    return buffer.getvalue()


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (120, 90), (255, 99, 71)).save(buffer, format="PNG")
    return buffer.getvalue()


def pdf_bytes(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def main() -> None:
    pg = EmbeddedPostgres()
    maintenance_url = pg.start()
    database_url = maintenance_url.rsplit("/", 1) [0] + "/" + DB_NAME
    upload_dir = tempfile.mkdtemp(prefix="smoke03-uploads-")

    async def _create_db() -> None:
        conn = await asyncpg.connect(
            host="127.0.0.1", port=pg.port, user="postgres", database="postgres"
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{DB_NAME}"')
        await conn.close()

    asyncio.run(_create_db())
    subprocess.run(
        [str(API_ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=True,
    )

    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_", "APP_", "LOCAL_"))},
        "DATABASE_URL": database_url,
        "APP_BIND_HOST": "127.0.0.1",
        "LOCAL_ACCESS_TOKEN": "",
        "UPLOAD_DIR": upload_dir,
        "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.Popen(
        [str(API_ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
         "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(API_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=20)
    try:
        deadline = time.monotonic() + 20
        ready = False
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2)
                ready = True
                break
            except Exception:
                time.sleep(0.4)
        if not ready:
            output = proc.stdout.read() if proc.stdout else ""
            print("uvicorn 未能就绪：\n" + "\n".join(output.splitlines()[-10:]))
            sys.exit(1)

        # ---- 1. 项目与 UPLOADED 容器（201 口径）----
        status, body = request("POST", "/api/projects", {
            "name": "2026 车辆续保", "vehicleName": "Model Y", "renewalYear": 2026,
        })
        check("创建项目", status == 201, body.get("message", ""))
        project_id = body["data"]["id"]

        status, body = request("POST", f"/api/projects/{project_id}/quotes", {
            "insurerCode": "PICC", "source": "UPLOADED",
        })
        check("前置创建 UPLOADED 报价容器返回 201", status == 201, body.get("message", ""))
        quote_id = body["data"]["id"]

        # ---- 2. 未同意 -> 422；拒绝后手动路径可用 ----
        files = [
            ("files", ("车损报价.jpg", jpeg_bytes(), "image/jpeg")),
            ("files", ("条款.pdf", pdf_bytes(2), "application/pdf")),
        ]
        response = client.post(
            f"/api/quotes/{quote_id}/files",
            files=files,
            data={"modelProcessingConsent": "false"},
        )
        check("首次上传未同意返回 422（MODEL_CONSENT_REQUIRED）",
              response.status_code == 422
              and response.json()["code"] == "MODEL_CONSENT_REQUIRED",
              response.text[:120])

        status, body = request("POST", f"/api/projects/{project_id}/quotes", {
            "insurerCode": "PINGAN", "agentName": "小王", "source": "MANUAL",
        })
        check("拒绝同意后手动录入路径可用", status == 201 and body["data"]["status"] == "PENDING_CONFIRM")

        # ---- 3. 同意后多文件上传：一律 202 + taskId（无 201 分支）----
        response = client.post(
            f"/api/quotes/{quote_id}/files",
            files=files,
            data={"modelProcessingConsent": "true"},
        )
        ok = response.status_code == 202
        data = response.json()["data"] if ok else {}
        check("同意后多文件上传返回 202 并携带 taskId", ok and data.get("taskId", 0) > 0,
              response.text[:160])
        check("响应携带按提交顺序的文件清单（JPEG/PNG/PDF 已入库）",
              [f["fileName"] for f in data.get("files", [])] == ["车损报价.jpg", "条款.pdf"],
              str(data)[:160])
        raw_url = data["files"][0]["rawUrl"] if data.get("files") else ""

        # ---- 4. 原文件受控读取（inline）----
        response = client.get(raw_url)
        check("原文件 inline 预览（content-type 与内容一致）",
              response.status_code == 200
              and response.headers["content-type"].startswith("image/jpeg")
              and response.content[:3] == b"\xff\xd8\xff",
              f"status={response.status_code}")
        response = client.get(f"/api/files/{data['files'][0]['id'] + 9999}/raw?projectId={project_id}")
        check("不存在的文件按 404 处理（不泄露存在性）", response.status_code == 404)

        # ---- 5. 轮询解析状态：worker 领取后因未配置视觉模型安全失败 ----
        final_status = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status, body = request("GET", f"/api/quotes/{quote_id}/parse-status")
            if status == 200 and body["data"]["status"] in ("SUCCEEDED", "FAILED"):
                final_status = body["data"]
                break
            time.sleep(1)
        check("进程内 worker 领取任务并执行（任务进入终态）",
              final_status is not None, str(final_status)[:160])
        check("未配置视觉模型时任务安全失败（FAILED + attempt=1，不重试）",
              final_status is not None and final_status["status"] == "FAILED"
              and final_status["attempt"] == 1,
              str(final_status)[:200])
        check("错误摘要脱敏且可操作（指向 VISION_* 配置或转手动）",
              final_status is not None and "VISION_" in (final_status.get("error") or ""))

        status, body = request("GET", f"/api/quotes/{quote_id}")
        check("任务终态失败后报价进入 PARSE_FAILED",
              status == 200 and body["data"]["status"] == "PARSE_FAILED")

        # ---- 6. 重试解析（202）→ 再次失败（安全失败）----
        response = client.post(f"/api/quotes/{quote_id}/reparse", data={})
        check("失败报价重试解析返回 202", response.status_code == 202, response.text[:120])
        final_status = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status, body = request("GET", f"/api/quotes/{quote_id}/parse-status")
            if status == 200 and body["data"]["status"] in ("SUCCEEDED", "FAILED"):
                final_status = body["data"]
                break
            time.sleep(1)
        check("重试任务再次安全失败", final_status is not None and final_status["status"] == "FAILED")

        # ---- 7. 转手动录入：保留文件，进入 PENDING_CONFIRM ----
        response = client.post(f"/api/quotes/{quote_id}/convert-manual")
        data = response.json().get("data") or {}
        check("转手动后报价进入 PENDING_CONFIRM 且文件保留",
              response.status_code == 200 and data.get("status") == "PENDING_CONFIRM"
              and len(data.get("files") or []) == 2,
              response.text[:160])

        # ---- 8. 删除项目同步清理磁盘目录 ----
        upload_root = Path(upload_dir)
        project_dir = upload_root / str(project_id)
        check("上传目录按 {projectId}/{fileId} 布局落盘",
              project_dir.exists() and len(list(project_dir.iterdir())) == 2)
        status, body = request("DELETE", f"/api/projects/{project_id}")
        # 清理在后台线程执行，给一个宽限窗口
        deadline = time.monotonic() + 10
        cleaned = False
        while time.monotonic() < deadline:
            if not project_dir.exists():
                cleaned = True
                break
            time.sleep(0.5)
        check("项目删除后磁盘目录被清理（幂等可重试）",
              status == 200 and cleaned, str(project_dir))

        print(f"\n结果：{len(PASS)} 通过，{len(FAIL)} 失败")
        if FAIL:
            for item in FAIL:
                print("  失败项:", item)
            sys.exit(1)
    finally:
        client.close()
        proc.kill()
        pg.stop()


if __name__ == "__main__":
    main()
