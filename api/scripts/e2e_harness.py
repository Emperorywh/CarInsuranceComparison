"""Playwright 端到端测试环境编排（TASK-07）。

子命令：
  up   ：准备一次性测试数据库（外部 PostgreSQL 优先，嵌入式兜底）→
         从空库迁移到 head → 写入假视觉模型的 fixture 目录与初始抽取
         结果 → 前台启动 API（阻塞，由 Playwright 作为 webServer 托管）。
  down ：读取运行状态文件，尽力清理：强删一次性库、停嵌入式实例、
         删除运行目录。清理失败不影响下次 up（up 总是先重建一切）。

隐私与边界：
- 一次性库名固定 ``car_e2e``（外部服务器）或随机名（嵌入式），绝不触碰
  业务库或服务器上的其他数据库；库中只有合成测试数据；
- 假视觉模型必须经 ``VISION_FIXTURE_DIR`` 显式启用，且仅由本脚本注入；
- 数据库账号只从环境变量 / 仓库根 `.env` 的 ``E2E_DATABASE_URL`` 读取，
  该文件不入版本控制，密钥绝不写进任何日志；
- 上传目录与 fixture 目录统一放在 ``api/.e2e-run/``（已 gitignore）。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent
RUN_DIR = API_ROOT / ".e2e-run"
STATE_PATH = RUN_DIR / "state.json"

# Playwright 从 web/ 目录启动本脚本：显式注入 api/ 到 sys.path，
# 保证 alembic env.py 与 uvicorn 的 `app.main` 导入不依赖进程工作目录
sys.path.insert(0, str(API_ROOT))

E2E_DB_NAME = "car_e2e"
# E2E 专用端口：不与开发者本机可能占用的 8000 冲突；
# 前端构建时以 NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8310 指向此处
DEFAULT_API_PORT = 8310

# 允许 E2E 前端（Playwright 启动的 next start）跨域访问 API
E2E_ALLOWED_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:3310,http://127.0.0.1:3310"
)


def _load_root_env() -> dict[str, str]:
    """极简 .env 解析：只为读取 E2E_DATABASE_URL（pydantic 会忽略未知键）。"""
    values: dict[str, str] = {}
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return values
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _resolve_e2e_database_url() -> str | None:
    """外部 PostgreSQL 连接串：环境变量优先，其次根 .env；均缺失返回 None。"""
    url = os.environ.get("E2E_DATABASE_URL", "").strip()
    if url:
        return url
    return _load_root_env().get("E2E_DATABASE_URL", "").strip() or None


def _split_db_url(url: str) -> dict:
    """把 postgresql:// 或 postgresql+asyncpg:// 连接串拆成连接参数。"""
    normalized = url.replace("postgresql+asyncpg://", "postgresql://")
    normalized = normalized.replace("postgresql+psycopg://", "postgresql://")
    parts = urlsplit(normalized)
    netloc = f"{parts.username or 'postgres'}"
    if parts.password:
        netloc += f":{parts.password}"
    netloc += f"@{parts.hostname or '127.0.0.1'}:{parts.port or 5432}"
    return {
        "host": parts.hostname or "127.0.0.1",
        "port": parts.port or 5432,
        "user": parts.username or "postgres",
        "password": parts.password or "",
        "database": (parts.path or "/").lstrip("/") or "postgres",
        "url": normalized,
        # 应用与 Alembic 统一用 asyncpg 驱动；显式重建 netloc 避免泄露 URL 细节差异
        "app_url": f"postgresql+asyncpg://{netloc}/{(parts.path or '/').lstrip('/') or 'postgres'}",
    }


def _app_database_url(params: dict, database: str) -> str:
    """拼出应用使用的 asyncpg 连接串（指向指定数据库）。"""
    return params["app_url"].rsplit("/", 1)[0] + f"/{database}"


async def _recreate_database(params: dict, database: str) -> None:
    """连维护库销毁并重建一次性库；WITH (FORCE) 断开残留连接（PG 13+）。"""
    import asyncpg

    conn = await asyncpg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database=params["database"] if params["database"] != database else "postgres",
        timeout=10,
    )
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{database}"')
    finally:
        await conn.close()


def _prepare_external_database(base_url: str) -> dict:
    """外部模式：确认可达后销毁重建 car_e2e；不可达时抛异常由上层兜底。"""
    params = _split_db_url(base_url)
    asyncio.run(_recreate_database(params, E2E_DB_NAME))
    return {"mode": "external", "database_url": _app_database_url(params, E2E_DB_NAME)}


def _prepare_embedded_database() -> dict:
    """离线兜底：嵌入式 PostgreSQL（复用 pytest 的 Zonky 发行版缓存）。"""
    sys.path.insert(0, str(API_ROOT / "tests"))
    from pg_server import EmbeddedPostgres  # noqa: E402

    server = EmbeddedPostgres()
    base_url = server.start()
    params = _split_db_url(base_url)
    database = f"{E2E_DB_NAME}_{random.randint(1000, 9999)}"
    asyncio.run(_recreate_database(params, database))
    return {
        "mode": "embedded",
        "database_url": _app_database_url(params, database),
        # 记录发行版与数据目录，供 down 用 pg_ctl 停实例（跨平台、无需 PID）
        "pg_bin": str(Path(sys.modules["pg_server"].ensure_dist()) / "bin"),
        "pg_data": str(server._data_dir or ""),  # noqa: SLF001 - 同仓库测试设施
        "pg_port": params["port"],
    }


def _run_migrations(database_url: str) -> None:
    """从空库执行 Alembic 迁移到 head（与 pytest 同一套迁移入口）。"""
    os.environ["DATABASE_URL"] = database_url
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(cfg, "head")


def _port_in_use(port: int) -> bool:
    import socket
    from contextlib import closing

    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _kill_pid(pid: int) -> None:
    """跨平台尽力终止进程树（残留 API 进程会占用端口并阻塞下一次运行）。"""
    if not pid:
        return
    import subprocess

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            timeout=30,
        )
    else:
        import contextlib
        import signal

        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)


def _kill_previous_run() -> None:
    """上次运行可能被硬杀留下孤儿 API 进程：按状态文件记录先清掉。"""
    if not STATE_PATH.exists():
        return
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    pid = state.get("api_pid")
    if isinstance(pid, int) and pid != os.getpid():
        _kill_pid(pid)
        print(f"[e2e-harness] 已终止上次运行的残留 API 进程 pid={pid}")


def cmd_up() -> None:
    """准备全部环境并前台启动 API（不返回）。"""
    api_port = int(os.environ.get("E2E_API_PORT", str(DEFAULT_API_PORT)))
    _kill_previous_run()
    if _port_in_use(api_port):
        raise SystemExit(
            f"端口 {api_port} 已被占用：请先停掉占用该端口的 API 进程再运行 E2E"
        )

    # 运行目录整体重建：上一次运行的库/文件残留一律作废
    if RUN_DIR.exists():
        shutil.rmtree(RUN_DIR, ignore_errors=True)
    fixture_dir = RUN_DIR / "vision-fixture"
    uploads_dir = RUN_DIR / "uploads"
    fixture_dir.mkdir(parents=True)
    uploads_dir.mkdir(parents=True)

    # 数据库：外部优先，5 秒内连不上自动降级嵌入式（保持离线可运行）
    external_url = _resolve_e2e_database_url()
    db_info: dict = {}
    if external_url:
        try:
            db_info = _prepare_external_database(external_url)
            print(f"[e2e-harness] 使用外部 PostgreSQL（一次性库 {E2E_DB_NAME}）")
        except Exception as cause:  # noqa: BLE001 - 降级路径需要捕获一切连接错误
            print(f"[e2e-harness] 外部数据库不可达（{cause}），降级为嵌入式 PostgreSQL")
            external_url = None
    if not external_url:
        db_info = _prepare_embedded_database()
        print("[e2e-harness] 使用嵌入式 PostgreSQL（离线兜底模式）")

    _run_migrations(db_info["database_url"])

    # 初始 fixture：默认返回一份合法单方案抽取结果；各用例按需原子改写
    default_fixture = API_ROOT / "tests" / "fixtures" / "raw_results" / "picc_full.json"
    shutil.copyfile(default_fixture, fixture_dir / "current.json")

    state = {
        **db_info,
        "fixture_dir": str(fixture_dir),
        "uploads_dir": str(uploads_dir),
        "api_port": api_port,
        "api_pid": os.getpid(),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    # 进程内启动 API：env 在导入 app 前设置完成（get_settings 读取即固定）
    os.environ["DATABASE_URL"] = db_info["database_url"]
    os.environ["UPLOAD_DIR"] = str(uploads_dir)
    os.environ["VISION_FIXTURE_DIR"] = str(fixture_dir)
    # 显式清空正式模型配置，确保 E2E 恒走 fixture 假模型
    os.environ["VISION_BASE_URL"] = ""
    os.environ["VISION_API_KEY"] = ""
    os.environ["ALLOWED_ORIGINS"] = E2E_ALLOWED_ORIGINS
    os.environ["APP_BIND_HOST"] = "127.0.0.1"

    print(f"[e2e-harness] API 启动于 http://127.0.0.1:{api_port}（fixture 假视觉模型）")
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=api_port,
        log_level="warning",
        access_log=False,
    )


def cmd_down() -> None:
    """尽力清理一次性资源；失败仅打印警告（下次 up 会强制重建）。"""
    if not STATE_PATH.exists():
        return
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return

    # 先终止上次运行的 API（若仍存活，例如 Playwright 未能杀干净进程树）
    pid = state.get("api_pid")
    if isinstance(pid, int) and pid != os.getpid():
        _kill_pid(pid)

    if state.get("mode") == "external":
        params = _split_db_url(state["database_url"])
        try:
            asyncio.run(_drop_database(params, E2E_DB_NAME))
            print(f"[e2e-harness] 已删除一次性库 {E2E_DB_NAME}")
        except Exception as cause:  # noqa: BLE001 - 清理是尽力而为
            print(f"[e2e-harness] 清理一次性库失败（下次 up 会重建）：{cause}")
    else:
        # 嵌入式：pg_ctl 停实例后删除数据目录
        pg_ctl = Path(state.get("pg_bin", "")) / (
            "pg_ctl.exe" if os.name == "nt" else "pg_ctl"
        )
        data_dir = state.get("pg_data", "")
        if pg_ctl.exists() and data_dir:
            import subprocess

            subprocess.run(
                [str(pg_ctl), "-D", data_dir, "stop", "-m", "immediate"],
                check=False,
                capture_output=True,
                timeout=60,
            )
        print("[e2e-harness] 已停止嵌入式 PostgreSQL")

    shutil.rmtree(RUN_DIR, ignore_errors=True)


async def _drop_database(params: dict, database: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database=params["database"] if params["database"] != database else "postgres",
        timeout=10,
    )
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    finally:
        await conn.close()


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "up":
        cmd_up()
    elif command == "down":
        started = time.monotonic()
        cmd_down()
        print(f"[e2e-harness] 清理完成，耗时 {time.monotonic() - started:.1f}s")
    else:
        raise SystemExit("用法: e2e_harness.py up|down")


if __name__ == "__main__":
    main()
