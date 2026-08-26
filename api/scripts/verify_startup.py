"""TASK-01 后端运行时验证脚本。

覆盖验证矩阵：
1. 默认本机模式（127.0.0.1，无令牌）：健康检查与业务 API 均可匿名访问；
2. 非回环绑定 + 无令牌：uvicorn 启动失败（进程退出）；
3. 令牌模式（本机 127.0.0.1 与非回环 0.0.0.0 行为一致）：
   健康检查匿名可访问；业务 API 无令牌 401、错误令牌 401、正确令牌 200。

用法：uv run python scripts/verify_startup.py
（自动启动一次性嵌入式 PostgreSQL 并从空库执行 alembic upgrade head）
"""

from __future__ import annotations

import asyncio
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
DB_NAME = "car_insurance_verify"
PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(f"{name}{' | ' + detail if detail else ''}")
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


def http_get(url: str, token: str | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(url)
    if token is not None:
        request.add_header("X-Access-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read().decode("utf-8"))
        except Exception:
            return error.code, {}


def start_uvicorn(env: dict[str, str], port: int) -> subprocess.Popen:
    """按给定环境启动 uvicorn；应用导入失败（安全校验）会让进程很快退出。"""
    return subprocess.Popen(
        [str(API_ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "app.main:app",
         "--host", env["APP_BIND_HOST"], "--port", str(port)],
        cwd=str(API_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def wait_http(port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            return True
        except Exception:
            time.sleep(0.4)
    return False


def base_env(database_url: str, bind_host: str, token: str = "") -> dict[str, str]:
    return {
        **{k: v for k, v in os.environ.items()
           if not k.startswith(("DATABASE_", "APP_", "LOCAL_"))},
        "DATABASE_URL": database_url,
        "APP_BIND_HOST": bind_host,
        "LOCAL_ACCESS_TOKEN": token,
        "PYTHONUNBUFFERED": "1",
    }


def run_mode(name: str, env: dict[str, str], port: int) -> subprocess.Popen | None:
    """启动一种模式并等待其就绪；启动失败（进程退出）返回 None 并输出日志尾部。"""
    proc = start_uvicorn(env, port)
    if wait_http(port):
        print(f"[{name}] uvicorn 就绪 port={port}")
        return proc
    if proc.poll() is not None:
        output = proc.stdout.read() if proc.stdout else ""
        tail = "\n".join(output.splitlines()[-6:])
        print(f"[{name}] 进程退出（rc={proc.returncode}）：\n{tail}")
    else:
        proc.kill()
    return None


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
    print(f"[verify] 一次性测试库已创建: {database_url}")

    # ---- 验证项：空库 alembic upgrade head（独立于 pytest 再执行一次）----
    result = subprocess.run(
        [str(API_ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "alembic", "upgrade", "head"],
        cwd=str(API_ROOT),
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )
    check("空库 alembic upgrade head", result.returncode == 0, result.stderr.strip()[-120:])

    try:
        # ---- 模式 1：默认本机（无令牌）----
        proc = run_mode("local", base_env(database_url, "127.0.0.1"), 8021)
        if proc:
            status, body = http_get("http://127.0.0.1:8021/health")
            check("本机模式 /health 匿名 200",
                  status == 200 and body.get("data", {}).get("status") == "ok")
            status, body = http_get("http://127.0.0.1:8021/api/projects")
            check("本机模式业务 API 匿名 200", status == 200 and body.get("data") == [])
            proc.kill()
            proc.wait()

        # ---- 模式 2：非回环 + 无令牌 → 拒绝启动 ----
        proc = run_mode("insecure", base_env(database_url, "0.0.0.0"), 8022)
        check("非回环+无令牌拒绝启动", proc is None)
        if proc:
            proc.kill()
            proc.wait()

        # ---- 模式 3：令牌模式（本机与非回环行为一致）----
        for label, host, port in (
            ("token@127.0.0.1", "127.0.0.1", 8023),
            ("token@0.0.0.0", "0.0.0.0", 8024),
        ):
            proc = run_mode(label, base_env(database_url, host, token="verify-token"), port)
            if not proc:
                check(f"{label} 启动", False)
                continue
            status, _ = http_get(f"http://127.0.0.1:{port}/health")
            check(f"{label} /health 匿名 200", status == 200)
            status, body = http_get(f"http://127.0.0.1:{port}/api/projects")
            check(f"{label} 无令牌 401", status == 401 and body.get("code") == "UNAUTHORIZED")
            status, _ = http_get(f"http://127.0.0.1:{port}/api/projects", token="wrong")
            check(f"{label} 错误令牌 401", status == 401)
            status, body = http_get(f"http://127.0.0.1:{port}/api/projects", token="verify-token")
            check(f"{label} 正确令牌 200", status == 200 and body.get("data") == [])
            proc.kill()
            proc.wait()
    finally:
        pg.stop()

    print(f"\n结果：{len(PASS)} 通过，{len(FAIL)} 失败")
    if FAIL:
        for item in FAIL:
            print("  失败项:", item)
        sys.exit(1)


if __name__ == "__main__":
    main()
