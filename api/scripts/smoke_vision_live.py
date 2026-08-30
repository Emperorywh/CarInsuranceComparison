"""可选的真实视觉模型 smoke test（TASK-04 验证 6，非阻断）。

定位与边界：
- 仅在用户提供可用密钥（VISION_BASE_URL/VISION_API_KEY/VISION_MODEL）
  时才能运行；未配置时打印提示并以 0 退出，不阻塞任何验收；
- 本脚本的通过与否**不作为** TASK-07 的 10 份真实样本验收口径，只用于
  开发期快速验证 provider 连通性、Schema 兼容性与端到端候选落库；
- 上传文件为脚本自造的纯色图片（无任何真实个人信息），模型返回内容
  只按脱敏后的任务状态与字段计数打印，绝不打印原文或 rawResult 正文。

用法：uv run python scripts/smoke_vision_live.py
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import httpx
from pg_server import EmbeddedPostgres  # type: ignore[import-not-found]
from PIL import Image

API_ROOT = Path(__file__).resolve().parents[1]
DB_NAME = "car_insurance_smoke_live"


def jpeg_bytes(color) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (600, 800), color).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


async def flow(database_url: str, upload_dir: str) -> int:
    from app.config import Settings, get_settings
    from app.main import create_app

    os.environ["DATABASE_URL"] = database_url
    os.environ["UPLOAD_DIR"] = upload_dir
    get_settings.cache_clear()

    settings = Settings(
        app_bind_host="127.0.0.1",
        local_access_token="",
        database_url=database_url,
        upload_dir=upload_dir,
    )
    if not (
        settings.vision_base_url.strip()
        and settings.vision_api_key.strip()
        and settings.vision_model.strip()
    ):
        print("未配置 VISION_BASE_URL / VISION_API_KEY / VISION_MODEL，跳过 live smoke（非阻断）。")
        return 0
    print(
        f"使用 provider=openai-compatible model={settings.vision_model} "
        f"base={settings.vision_base_url}"
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://live") as client:
            response = await client.post(
                "/api/projects",
                json={"name": "live smoke", "vehicleName": "测试车辆", "renewalYear": 2026},
            )
            project_id = response.json()["data"]["id"]
            response = await client.post(
                f"/api/projects/{project_id}/quotes",
                json={"insurerCode": "PICC", "source": "UPLOADED"},
            )
            quote_id = response.json()["data"]["id"]
            response = await client.post(
                f"/api/quotes/{quote_id}/files",
                files=[("files", ("测试图.jpg", jpeg_bytes((200, 210, 240)), "image/jpeg"))],
                data={"modelProcessingConsent": "true"},
            )
            print("上传:", response.status_code, response.json()["message"])
            if response.status_code != 202:
                return 1

            deadline = time.monotonic() + 300
            status_data: dict = {}
            while time.monotonic() < deadline:
                response = await client.get(f"/api/quotes/{quote_id}/parse-status")
                status_data = response.json()["data"]
                if status_data["status"] in ("SUCCEEDED", "FAILED"):
                    break
                await asyncio.sleep(3)

            print(
                "任务终态:",
                status_data.get("status"),
                "attempt=",
                status_data.get("attempt"),
                "耗时分段 startedAt=",
                status_data.get("startedAt"),
                "finishedAt=",
                status_data.get("finishedAt"),
            )
            print("脱敏错误摘要:", status_data.get("error"))
            response = await client.get(f"/api/quotes/{quote_id}")
            quote = response.json()["data"]
            print("报价状态:", quote["status"])
            print(
                "候选计数: 险种",
                len(quote["coverages"]),
                "服务",
                len(quote["services"]),
                "保障包",
                len(quote["packages"]),
                "标注",
                len(quote["annotations"]),
                "证据",
                len(quote["evidences"]),
            )
            print("质量警告:", quote.get("qualityWarnings"))
            ok = status_data.get("status") == "SUCCEEDED"
            print("结果:", "通过（provider 连通且候选落库成功）" if ok else "未通过（见上方脱敏错误摘要）")
            return 0 if ok else 1


def main() -> int:
    import subprocess

    import asyncpg

    pg = EmbeddedPostgres()
    maintenance_url = pg.start()
    database_url = maintenance_url.rsplit("/", 1)[0] + "/" + DB_NAME
    upload_dir = tempfile.mkdtemp(prefix="smoke-live-uploads-")

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
        return asyncio.run(flow(database_url, upload_dir))
    finally:
        pg.stop()


if __name__ == "__main__":
    raise SystemExit(main())
