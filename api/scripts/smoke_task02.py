"""TASK-02 全栈冒烟脚本：真实 uvicorn + 一次性 PostgreSQL 上的手动报价主路径。

覆盖（TASK-02 验证第 3 条的服务端链路）：
创建项目 → 新增手动报价（OTHER + 自由输入公司名）→ 7 Tab 等价的完整录入
（价格/险种/座位自动推导/未识别映射/服务/保障包/标注）→ 添加优惠（含
SERVICE 无折现值）→ 确认 → 项目详情分组卡片 → 删除报价。

前端交互（移动视口）由 Vitest 组件测试与生产构建覆盖（同 TASK-01 口径），
浏览器端到端走查统一并入 TASK-07 的 Playwright 门禁。

用法：uv run python scripts/smoke_task02.py
"""

from __future__ import annotations

import sys

# Windows 控制台默认 GBK：统一重配为 UTF-8，避免中文/符号打印失败
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import asyncio  # noqa: E402
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import asyncpg  # noqa: E402
from pg_server import EmbeddedPostgres  # noqa: E402

API_ROOT = Path(__file__).resolve().parents[1]
DB_NAME = "car_insurance_smoke02"
PORT = 8031
PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(f"{name}{' | ' + detail if detail else ''}")
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


def request(
    method: str, path: str, payload: dict | None = None
) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", data=body, method=method
    )
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read().decode("utf-8"))
        except Exception:
            return error.code, {}


def main() -> None:
    pg = EmbeddedPostgres()
    maintenance_url = pg.start()
    database_url = maintenance_url.rsplit("/", 1)[0] + "/" + DB_NAME

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
        "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.Popen(
        [str(API_ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
         "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(API_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
    )
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

        def quote_id(body: dict) -> int:
            return body["data"]["id"]

        # ---- 1. 创建项目 → 新增手动报价（OTHER 必须带公司名）----
        status, body = request("POST", "/api/projects", {
            "name": "2026 车辆续保", "vehicleName": "Model Y", "renewalYear": 2026,
        })
        check("创建项目", status == 201, body.get("message", ""))
        project_id = body["data"]["id"]

        status, body = request("POST", f"/api/projects/{project_id}/quotes", {
            "insurerCode": "OTHER", "insurerName": "利宝保险",
            "agentName": "小王", "source": "MANUAL",
        })
        check("创建手动报价（OTHER 自由输入公司名，创建即待确认）",
              status == 201 and body["data"]["status"] == "PENDING_CONFIRM",
              body.get("message", ""))
        qid = quote_id(body)

        # ---- 2. 价格：交强 1045、车船税 0、官方总价、车辆快照 ----
        status, body = request("PATCH", f"/api/quotes/{qid}", {
            "compulsoryPremium": "1045", "vehicleTax": "0",
            "otherFeesStatus": "NOT_INCLUDED", "officialTotal": "5785.14",
            "vehicleModel": "Model Y", "vehicleSeats": 5,
            "firstRegDate": "2022-05", "isNev": True,
        })
        check("价格分项与车辆快照保存", status == 200, body.get("message", ""))

        # ---- 3. 基础车险 + 座位总额自动推导 ----
        status, body = request("POST", f"/api/quotes/{qid}/coverages", {
            "code": "THIRD_PARTY_LIABILITY", "rawName": "三者险",
            "coverageAmount": "3000000", "premium": "1237.41",
        })
        check("新增三者险", status == 201, body.get("message", ""))
        status, body = request("POST", f"/api/quotes/{qid}/coverages", {
            "code": "PASSENGER_LIABILITY", "rawName": "乘客险",
            "perSeatAmount": "1000", "seatCount": 4, "premium": "100",
        })
        check("乘客险座位总额自动推导（0.1万/座×4 → 4000）",
              status == 201
              and body["data"]["coverages"][-1]["coverageAmount"] == 4000.0,
              body.get("message", ""))

        # ---- 4. 未识别项：先录入再映射为三者医保外 ----
        status, body = request("POST", f"/api/quotes/{qid}/coverages", {
            "rawName": "医保外附加权益", "premium": "36.50",
        })
        row_id = body["data"]["coverages"][-1]["id"]
        check("未识别项进入未识别区并阻断商业险计算",
              status == 201
              and body["data"]["coverages"][-1]["category"] == "UNRECOGNIZED"
              and body["data"]["computedCommercialPremium"] is None)
        status, body = request("PATCH", f"/api/quotes/{qid}/coverages/{row_id}", {
            "code": "TP_NON_MEDICAL", "coverageAmount": "500000",
        })
        check("未识别项映射为标准附加险",
              status == 200
              and body["data"]["computedCommercialPremium"] == 1373.91,
              body.get("message", ""))

        # ---- 5. 服务 + 保障包 + 标注 ----
        status, body = request("POST", f"/api/quotes/{qid}/services", {
            "serviceType": "ROAD_RESCUE", "status": "FREE", "count": 2, "cost": "0",
        })
        check("新增增值服务（明确 0 元 → FREE）", status == 201, body.get("message", ""))
        status, body = request("POST", f"/api/quotes/{qid}/packages", {
            "name": "车主尊享保障", "premium": "348",
            "coverages": [
                {"type": "DRIVER_ACCIDENT", "coverageAmount": "300000",
                 "multiplier": "2", "condition": "LEGAL_HOLIDAY"},
            ],
        })
        check("新增保障包（含内部保障）",
              status == 201 and len(body["data"]["packages"][0]["coverages"]) == 1,
              body.get("message", ""))
        status, body = request("POST", f"/api/quotes/{qid}/annotations", {
            "content": "节假日90万 100%赔付", "kind": "HANDWRITTEN",
        })
        check("新增销售说明", status == 201, body.get("message", ""))

        # ---- 6. 优惠：300 现金计入 + SERVICE 无折现（勾选也不减钱）----
        request("POST", f"/api/quotes/{qid}/discounts", {
            "discountType": "CASH", "description": "返现",
            "amount": "300", "cashEquivalent": "300", "includeInNet": True,
        })
        status, body = request("POST", f"/api/quotes/{qid}/discounts", {
            "discountType": "SERVICE", "description": "洗车5次",
            "amount": "200", "includeInNet": True,
        })
        check("优惠后净支出 = 5785.14 - 300（SERVICE 无折现不减钱）",
              status == 201 and body["data"]["netPayment"] == 5485.14,
              f"实际 netPayment={body['data'].get('netPayment')}")

        # ---- 7. 确认 → 项目摘要回填 + 分组卡片 ----
        status, body = request("POST", f"/api/quotes/{qid}/confirm", {})
        check("确认报价进入 CONFIRMED", status == 200 and body["data"]["status"] == "CONFIRMED",
              body.get("message", ""))
        status, body = request("GET", f"/api/projects/{project_id}")
        group = body["data"]["quoteGroups"][0]
        card = group["quotes"][0]
        check("项目详情分组卡片（公司+保险员、净支出、三者/医保外摘要）",
              status == 200
              and group["insurerName"] == "利宝保险"
              and card["netPayment"] == 5485.14
              and card["thirdPartyAmount"] == 3000000.0
              and card["tpNonMedicalAmount"] == 500000.0,
              body.get("message", ""))
        check("确认后回填项目车辆摘要",
              body["data"]["vehicleModel"] == "Model Y" and body["data"]["vehicleSeats"] == 5)

        # ---- 8. 删除报价 ----
        status, _ = request("DELETE", f"/api/quotes/{qid}")
        status_after, _ = request("GET", f"/api/quotes/{qid}")
        check("删除报价后 404", status == 200 and status_after == 404)

    finally:
        proc.kill()
        proc.wait()
        pg.stop()

    print(f"\n结果：{len(PASS)} 通过，{len(FAIL)} 失败")
    if FAIL:
        for item in FAIL:
            print("  失败项:", item)
        sys.exit(1)


if __name__ == "__main__":
    main()
