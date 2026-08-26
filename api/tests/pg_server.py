"""测试用嵌入式 PostgreSQL 供应（无 Docker 环境）。

- 二进制来自 Zonky embedded-postgres-binaries（PostgreSQL 官方源码构建，
  Windows/macOS/Linux 全平台可用），一次性下载并缓存在用户目录；
- 之后所有测试运行完全离线，不依赖开发者现有数据库；
- 已有外部 PostgreSQL 时可设置 TEST_DATABASE_URL 直接指向，跳过本模块。

缓存位置：环境变量 CAR_INSURANCE_PG_HOME，默认
Windows %LOCALAPPDATA%/CarInsurancePg，其他平台 ~/.cache/car-insurance-pg。
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path

# 固定的二进制版本：与 compose.yaml 中的 postgres:17 对齐
ZONKY_VERSION = "17.5.0"

# Zonky 通过 Maven Central 分发；jar 内含 postgres-{os}-{arch}.txz
_MAVEN_BASE = (
    "https://repo1.maven.org/maven2/io/zonky/test/postgres/"
    "embedded-postgres-binaries-{platform}/{version}/"
    "embedded-postgres-binaries-{platform}-{version}.jar"
)


def _platform_name() -> str:
    """Zonky 的平台标识（os-arch）。"""
    machine = platform.machine().lower()
    system = platform.system().lower()
    if system == "windows":
        return "windows-amd64"
    if system == "darwin":
        return "darwin-arm64" if machine in ("arm64", "aarch64") else "darwin-x86_64"
    return "linux-amd64" if machine in ("x86_64", "amd64") else "linux-arm64"


def _cache_root() -> Path:
    env = os.environ.get("CAR_INSURANCE_PG_HOME")
    if env:
        return Path(env)
    if platform.system().lower() == "windows":
        return Path.home() / "AppData" / "Local" / "CarInsurancePg"
    return Path.home() / ".cache" / "car-insurance-pg"


def _download(url: str, dest: Path) -> None:
    print(f"[pg_server] 下载 {url}（一次性，之后离线复用）...", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=180) as response, dest.open("wb") as fh:
        shutil.copyfileobj(response, fh)


def ensure_dist() -> Path:
    """确保本地存在 PostgreSQL 发行版，返回其根目录（含 bin/ lib/ share/）。"""
    root = _cache_root() / f"postgres-{ZONKY_VERSION}"
    marker = root / ".complete"
    if marker.exists():
        return root

    platform_name = _platform_name()
    jar_url = _MAVEN_BASE.format(platform=platform_name, version=ZONKY_VERSION)

    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        jar_path = tmp_path / "dist.jar"
        _download(jar_url, jar_path)
        # jar 本质是 zip，内含 txz 压缩包
        with zipfile.ZipFile(jar_path) as jar:
            member = next(name for name in jar.namelist() if name.endswith(".txz"))
            with jar.open(member) as src, (tmp_path / "dist.txz").open("wb") as dst:
                shutil.copyfileobj(src, dst)
        # tarfile 原生支持 xz；发行版内容直接解压到 root
        with tarfile.open(tmp_path / "dist.txz", "r:xz") as tar:
            tar.extractall(root, filter="data")

    marker.write_text("ok", encoding="utf-8")
    return root


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _exe(dist: Path, name: str) -> str:
    suffix = ".exe" if platform.system().lower() == "windows" else ""
    return str(dist / "bin" / f"{name}{suffix}")


class EmbeddedPostgres:
    """临时 PostgreSQL 实例：随机端口、临时数据目录、trust 认证。"""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._data_dir: Path | None = None
        self.port: int = 0

    def start(self) -> str:
        """启动实例并返回指向维护库 postgres 的连接 URL。"""
        dist = ensure_dist()
        self.port = _free_port()
        # 数据目录放临时目录，实例停止后随目录销毁，不留任何测试数据
        self._data_dir = Path(tempfile.mkdtemp(prefix="car-insurance-test-pg-"))
        subprocess.run(
            [
                _exe(dist, "initdb"),
                "-D", str(self._data_dir),
                "-U", "postgres",
                "-A", "trust",
                "-E", "UTF8",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self._proc = subprocess.Popen(
            [
                _exe(dist, "postgres"),
                "-D", str(self._data_dir),
                "-p", str(self.port),
                # 只监听本机回环；测试库可能含隐私字段，绝不暴露
                "-c", "listen_addresses=127.0.0.1",
                "-c", "max_connections=50",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_ready(dist)
        return f"postgresql+asyncpg://postgres@127.0.0.1:{self.port}/postgres"

    def _wait_ready(self, dist: Path, timeout_seconds: float = 60.0) -> None:
        # Zonky 精简发行版不含 pg_isready，直接用 asyncpg 做真实协议探活
        import asyncio

        import asyncpg

        async def _probe() -> None:
            conn = await asyncpg.connect(
                host="127.0.0.1", port=self.port, user="postgres", database="postgres"
            )
            await conn.execute("SELECT 1")
            await conn.close()

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                asyncio.run(_probe())
                return
            except Exception:
                time.sleep(0.5)
        self.stop()
        raise RuntimeError("嵌入式 PostgreSQL 启动超时")

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._data_dir is not None:
            shutil.rmtree(self._data_dir, ignore_errors=True)
            self._data_dir = None
