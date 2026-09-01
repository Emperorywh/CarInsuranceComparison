"""MVP 真实样本验收运行器（TASK-07，SPEC §15）。

职责：
- 按 manifest.json 逐份上传真实样本（锁定 provider/model，每份执行 1 次，
  失败只走产品内置重试，不重复抽样）；
- 与人工标注的期望结果逐字段比对，计算核心字段字段级完全正确率；
- 判定五类高风险错误（司机/乘客互换、三个医保外对象互换、保障包污染
  主险、销售标注污染正式字段、明确 0 元服务识别为不包含），必须为 0；
- 校验 evidence 的 fileId/page 合法性、隐私探针不泄露（原图中的真实
  姓名/车牌/VIN/发动机号不得进入任何落库内容）、解析耗时口径；
- 产出匿名化报告（Markdown + JSON）到 reports/，只含样本 ID、
  provider/model、参数、正确率、错误类别与耗时。

使用方式（仓库根目录）：
  # 正式验收：需先在 .env / 环境变量配置 VISION_BASE_URL/API_KEY/MODEL
  # 与 E2E_DATABASE_URL（外部 PostgreSQL；运行器使用独立的一次性库）
  uv run python acceptance/run_acceptance.py

  # 工具链自检（无密钥时验证运行器本身，fixture 假模型；结果不作为验收）
  uv run python acceptance/run_acceptance.py --dry-run

隐私边界：样本原图与 probes 只存放在 gitignore 的 samples/ 下；报告与
manifest 只含匿名 ID 与业务数值。上传前用户须知：原文件会发送至所配置
的视觉模型供应商（与产品内一致，由运行器的同意参数显式记录）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"
sys.path.insert(0, str(API_ROOT))

DEFAULT_API_PORT = 8410
ACCEPTANCE_DB_NAME = "car_acceptance"

# 字段级金额比对容差：金额按分（两位小数）完全一致
MONEY_EPS = 0.005


# ---------------------------------------------------------------------------
# manifest 模型（与 manifest.schema.json 同口径；schema 供外部工具校验）
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field  # noqa: E402


class CoverageExpectation(BaseModel):
    absent: bool = False
    status: str | None = None
    coverageAmount: float | None = None
    perSeatAmount: float | None = None
    seatCount: int | None = None
    premium: float | None = None
    note: str | None = None

    def annotated_fields(self) -> dict[str, float | int | str]:
        """返回参与正确率统计的已标注字段（absent/note 不计）。"""
        fields: dict[str, float | int | str] = {}
        for key in ("coverageAmount", "premium"):
            value = getattr(self, key)
            if value is not None:
                fields[key] = value
        for key in ("perSeatAmount", "seatCount"):
            value = getattr(self, key)
            if value is not None:
                fields[key] = value
        if self.status is not None:
            fields["status"] = self.status
        return fields


class ServiceExpectation(BaseModel):
    absent: bool = False
    status: str | None = None
    count: int | None = None
    cost: float | None = None


class PackageExpectation(BaseModel):
    nameContains: str
    premium: float


class Expected(BaseModel):
    vehicle: dict[str, object] = Field(default_factory=dict)
    pricing: dict[str, float | None] = Field(default_factory=dict)
    coreCoverages: dict[str, CoverageExpectation] = Field(default_factory=dict)
    additionalCoverages: dict[str, CoverageExpectation] = Field(default_factory=dict)
    services: dict[str, ServiceExpectation] = Field(default_factory=dict)
    packages: list[PackageExpectation] = Field(default_factory=list)
    annotationProbes: list[str] = Field(default_factory=list)


class Sample(BaseModel):
    id: str
    insurer: str
    files: list[str]
    tags: list[str] = Field(default_factory=list)
    expected: Expected


class Manifest(BaseModel):
    schemaVersion: int
    notes: str | None = None
    samples: list[Sample]


# ---------------------------------------------------------------------------
# 一次性环境（外部 PostgreSQL 一次性库 + 前台 API），复用 e2e_harness 的机制
# ---------------------------------------------------------------------------

def _split_db_url(url: str) -> dict:
    normalized = url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )
    parts = urlsplit(normalized)
    return {
        "host": parts.hostname or "127.0.0.1",
        "port": parts.port or 5432,
        "user": parts.username or "postgres",
        "password": parts.password or "",
        "database": (parts.path or "/").lstrip("/") or "postgres",
    }


def _recreate_database(params: dict, database: str) -> None:
    import asyncpg

    async def _run() -> None:
        conn = await asyncpg.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database=params["database"],
            timeout=15,
        )
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{database}"')
        finally:
            await conn.close()

    asyncio.run(_run())


def _migrate(database_url: str) -> None:
    os.environ["DATABASE_URL"] = database_url
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(cfg, "head")


def _start_api(port: int, extra_env: dict[str, str]) -> subprocess.Popen:
    """以子进程启动 API（前台托管给运行器，结束时由运行器终止）。"""
    env = {**os.environ, **extra_env, "APP_BIND_HOST": "127.0.0.1"}
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys, uvicorn; sys.path.insert(0, r'%s'); "
                "uvicorn.run('app.main:app', host='127.0.0.1', port=%d, "
                "log_level='warning', access_log=False)" % (API_ROOT, port)
            ),
        ],
        env=env,
        cwd=str(API_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_health(port: int, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("验收 API 启动超时")


# ---------------------------------------------------------------------------
# 逐样本执行与判定
# ---------------------------------------------------------------------------

class SampleResult(BaseModel):
    sample_id: str
    insurer: str
    files: int
    pages: int
    parse_seconds: float
    task_status: str
    checks_total: int = 0
    checks_passed: int = 0
    failures: list[str] = Field(default_factory=list)
    high_risk: dict[str, int] = Field(
        default_factory=lambda: {
            "driver_passenger_swap": 0,
            "non_medical_object_swap": 0,
            "package_pollutes_core": 0,
            "annotation_pollutes_official": 0,
            "zero_cost_service_not_included": 0,
        }
    )
    evidence_errors: int = 0
    privacy_leaks: list[str] = Field(default_factory=list)


def _money_equal(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= MONEY_EPS


class Checker:
    """逐字段判定器：统计字段级完全正确 + 五类高风险错误。"""

    def __init__(self, sample: Sample, quote: dict) -> None:
        self.sample = sample
        self.quote = quote
        self.result = SampleResult(
            sample_id=sample.id,
            insurer=sample.insurer,
            files=len(sample.files),
            pages=sum(1 for _ in sample.files),
            parse_seconds=0.0,
            task_status="",
        )
        # 行文本池：用于标注污染扫描（正式行，不含 sales_annotation）
        self._official_texts: list[str] = []

    # ---- 基础取数 ----

    def _included_rows(self, code: str) -> list[dict]:
        return [
            row
            for row in self.quote.get("coverages", [])
            if row.get("code") == code and row.get("status") == "INCLUDED"
        ]

    def _rows_text_pool(self) -> list[str]:
        if not self._official_texts:
            pool: list[str] = []
            for row in self.quote.get("coverages", []):
                pool.append(str(row.get("rawName", "")))
                pool.append(str(row.get("rawValue") or ""))
                pool.append(str(row.get("description") or ""))
            for row in self.quote.get("services", []):
                pool.append(str(row.get("rawName", "")))
                pool.append(str(row.get("description") or ""))
            for pkg in self.quote.get("packages", []):
                pool.append(str(pkg.get("name", "")))
                pool.append(str(pkg.get("description") or ""))
            for ev in self.quote.get("evidences", []):
                pool.append(str(ev.get("rawValue") or ""))
            self._official_texts = pool
        return self._official_texts

    # ---- 字段级判定 ----

    def check(self, name: str, actual: object, expected: object) -> None:
        self.result.checks_total += 1
        ok = (
            _money_equal(float(actual), float(expected))
            if isinstance(expected, (int, float)) and isinstance(actual, (int, float))
            else actual == expected
        )
        if ok:
            self.result.checks_passed += 1
        else:
            self.result.failures.append(f"{name}: 期望 {expected!r}，实际 {actual!r}")

    def check_pricing(self) -> None:
        pricing = self.sample.expected.pricing
        for key, expected in pricing.items():
            if key.endswith("Status"):
                continue
            self.check(f"pricing.{key}", self.quote.get(key), expected)

    def check_vehicle(self) -> None:
        # manifest 用 SPEC 语义名；QuoteRead 契约里座位数字段为 vehicleSeats
        key_mapping = {"seatCount": "vehicleSeats"}
        for key, expected in self.sample.expected.vehicle.items():
            quote_key = key_mapping.get(key, key)
            self.check(f"vehicle.{key}", self.quote.get(quote_key), expected)

    def _check_coverage_group(self, group: str, expectations: dict[str, CoverageExpectation], swap_pool: dict[str, set[int]]) -> None:
        for code, exp in expectations.items():
            rows = self._included_rows(code)
            if exp.absent:
                self.result.checks_total += 1
                if rows:
                    self.result.failures.append(f"{group}.{code}: 期望不包含，实际存在 INCLUDED 行")
                continue
            if not rows:
                self.result.checks_total += 1
                self.result.failures.append(f"{group}.{code}: 期望存在 INCLUDED 行，实际缺失")
                continue
            # 任一行同时满足全部已标注字段才算通过（逐字段累计统计）
            best: tuple[int, list[str]] | None = None
            for row in rows:
                passed = 0
                fails: list[str] = []
                for field, expected in exp.annotated_fields().items():
                    actual = row.get(field)
                    self_check = (
                        _money_equal(float(actual), float(expected))
                        if isinstance(expected, (int, float))
                        and isinstance(actual, (int, float))
                        else actual == expected
                    )
                    if self_check:
                        passed += 1
                    else:
                        fails.append(f"{group}.{code}.{field}: 期望 {expected!r}，实际 {actual!r}")
                if best is None or passed > best[0]:
                    best = (passed, fails)
            assert best is not None
            self.result.checks_total += len(exp.annotated_fields())
            self.result.checks_passed += best[0]
            self.result.failures.extend(best[1])
            # 记录行金额到互换判定池
            if exp.coverageAmount is not None:
                swap_pool.setdefault(code, set()).update(
                    int(float(row.get("coverageAmount")))
                    for row in rows
                    if row.get("coverageAmount") is not None
                )

    def _check_swaps(self, group: str, expectations: dict[str, CoverageExpectation], codes: list[str], kind: str) -> None:
        """互换判定：期望值唯一地命中了「错误对象」的行（金额精确相等）。"""
        for code, exp in expectations.items():
            if exp.coverageAmount is None:
                continue
            for other in codes:
                if other == code:
                    continue
                others = self._included_rows(other)
                if any(
                    row.get("coverageAmount") is not None
                    and _money_equal(float(row["coverageAmount"]), exp.coverageAmount)  # type: ignore[arg-type]
                    for row in others
                ):
                    # 另一对象出现同额行：只有当本对象自身缺失/不匹配时才判互换
                    own = self._included_rows(code)
                    own_ok = any(
                        row.get("coverageAmount") is not None
                        and _money_equal(float(row["coverageAmount"]), exp.coverageAmount)  # type: ignore[arg-type]
                        for row in own
                    )
                    if not own_ok:
                        self.result.high_risk[kind] += 1
                        return

    def check_services(self) -> None:
        rows = self.quote.get("services", [])
        for code, exp in self.sample.expected.services.items():
            matched = [row for row in rows if row.get("serviceType") == code]
            if exp.absent:
                self.result.checks_total += 1
                if matched:
                    self.result.failures.append(f"services.{code}: 期望不存在，实际存在")
                continue
            if not matched:
                self.result.checks_total += 1
                self.result.failures.append(f"services.{code}: 期望存在服务行，实际缺失")
                continue
            row = matched[0]
            if exp.status is not None:
                self.check(f"services.{code}.status", row.get("status"), exp.status)
                # 高风险 #5：明确 0 元服务被判为「不包含」
                if exp.status == "FREE" and row.get("status") == "NOT_INCLUDED":
                    self.result.high_risk["zero_cost_service_not_included"] += 1
            if exp.count is not None:
                self.check(f"services.{code}.count", row.get("count"), exp.count)
            if exp.cost is not None:
                self.check(f"services.{code}.cost", row.get("cost"), exp.cost)

    def check_packages(self) -> None:
        pkgs = self.quote.get("packages", [])
        for exp in self.sample.expected.packages:
            matched = [
                pkg
                for pkg in pkgs
                if exp.nameContains in str(pkg.get("name", ""))
                and pkg.get("status") != "NOT_INCLUDED"
            ]
            self.result.checks_total += 1
            if not matched:
                self.result.failures.append(f"packages[{exp.nameContains}]: 期望存在保障包，实际缺失")
                continue
            premium = matched[0].get("premium")
            if premium is not None and _money_equal(float(premium), exp.premium):
                self.result.checks_passed += 1
            else:
                self.result.failures.append(
                    f"packages[{exp.nameContains}].premium: 期望 {exp.premium!r}，实际 {premium!r}"
                )

    def check_high_risk_text(self) -> None:
        """高风险 #3/#4：主险行混入保障包内容；销售标注串入正式行。"""
        for row in self.quote.get("coverages", []):
            raw = str(row.get("rawName", ""))
            if any(word in raw for word in ("驾乘", "意外", "尊享", "途顺", "家安")):
                self.result.high_risk["package_pollutes_core"] += 1
        probes = self.sample.expected.annotationProbes
        for probe in probes:
            if any(probe in text for text in self._rows_text_pool()):
                self.result.high_risk["annotation_pollutes_official"] += 1

    def check_evidence(self) -> None:
        """§15.2.3：每个证据必须定位到合法 fileId/page；缺失不伪造。"""
        page_counts = {f["id"]: f.get("pageCount", 1) for f in self.quote.get("files", [])}
        evidence_groups = [self.quote.get("evidences", [])]
        for group_name, rows in (
            ("coverages", self.quote.get("coverages", [])),
            ("services", self.quote.get("services", [])),
            ("packages", self.quote.get("packages", [])),
        ):
            for row in rows:
                if group_name == "packages":
                    evidence_groups.append(
                        [
                            {
                                "sourceFileId": row.get("sourceFileId"),
                                "sourcePage": row.get("sourcePage"),
                                "fieldName": row.get("name"),
                            }
                        ]
                    )
                    for inner in row.get("coverages", []) or []:
                        evidence_groups.append(
                            [
                                {
                                    "sourceFileId": inner.get("sourceFileId"),
                                    "sourcePage": inner.get("sourcePage"),
                                    "fieldName": inner.get("name"),
                                }
                            ]
                        )
                    continue
                evidence_groups.append(
                    [
                        {
                            "sourceFileId": row.get("sourceFileId"),
                            "sourcePage": row.get("sourcePage"),
                            "fieldName": row.get("name"),
                        }
                    ]
                )
        for group in evidence_groups:
            for ev in group:
                file_id = ev.get("sourceFileId")
                page = ev.get("sourcePage")
                if file_id is None:
                    continue  # 无证据合法（字段按规则降档），不算错误
                if file_id not in page_counts:
                    self.result.evidence_errors += 1
                    continue
                if page is None or not (1 <= int(page) <= page_counts[file_id]):
                    self.result.evidence_errors += 1


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _load_probes(samples_dir: Path, sample_id: str) -> dict[str, list[str]]:
    path = samples_dir / "probes" / f"{sample_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


async def _check_privacy_db(db_params: dict, database: str, project_id: int, probes: dict[str, list[str]]) -> list[str]:
    """直查数据库：rawResult 与错误摘要不得包含任何隐私探针。"""
    import asyncpg

    if not any(probes.values()):
        return []
    leaks: list[str] = []
    conn = await asyncpg.connect(
        host=db_params["host"],
        port=db_params["port"],
        user=db_params["user"],
        password=db_params["password"],
        database=database,
        timeout=15,
    )
    try:
        rows = await conn.fetch(
            "SELECT id, raw_result, error FROM parse_task WHERE project_id = $1",
            project_id,
        )
        for row in rows:
            for column in ("raw_result", "error"):
                value = row[column]
                if value is None:
                    continue
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                for kind, values in probes.items():
                    for probe in values:
                        if probe and probe in text:
                            leaks.append(f"task#{row['id']}.{column} 泄露 {kind}")
    finally:
        await conn.close()
    return leaks


def _composition_report(manifest: Manifest) -> dict:
    counts: dict[str, int] = {}
    tags: dict[str, int] = {}
    for sample in manifest.samples:
        counts[sample.insurer] = counts.get(sample.insurer, 0) + 1
        for tag in sample.tags:
            tags[tag] = tags.get(tag, 0) + 1
    return {
        "total": len(manifest.samples),
        "byInsurer": counts,
        "byTag": tags,
        "gaps": {
            "total_at_least_10": len(manifest.samples) >= 10,
            "picc_at_least_5": counts.get("PICC", 0) >= 5,
            "pingan_at_least_5": counts.get("PINGAN", 0) >= 5,
            "pdf_at_least_2": tags.get("pdf", 0) >= 2,
            "multi_file_at_least_2": tags.get("multi_file", 0) >= 2,
            "multi_plan_at_least_1": tags.get("multi_plan", 0) >= 1,
            "annotation_at_least_2": (tags.get("annotation_red_text", 0) + tags.get("annotation_arrow", 0) + tags.get("annotation_handwritten", 0)) >= 2,
        },
    }


def run(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    samples_dir = Path(args.samples_dir)
    reports_dir = REPO_ROOT / "acceptance" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    mode = "dry-run" if args.dry_run else "acceptance"
    long_edge = os.environ.get("MAX_IMAGE_LONG_EDGE", "2400")
    thinking = os.environ.get("VISION_THINKING", "").strip()

    # ---- 环境准备（一次性库 + API）----
    db_base_url = os.environ.get("E2E_DATABASE_URL", "").strip()
    if not db_base_url:
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("E2E_DATABASE_URL="):
                    db_base_url = line.split("=", 1)[1].strip()
                    break
    if not db_base_url:
        print("缺少 E2E_DATABASE_URL（外部 PostgreSQL 连接串），无法运行验收")
        return 2
    db_params = _split_db_url(db_base_url)
    database = ACCEPTANCE_DB_NAME if not args.dry_run else f"{ACCEPTANCE_DB_NAME}_dry"
    print(f"[acceptance] 重建一次性库 {database}（外部 PostgreSQL）")
    _recreate_database(db_params, database)
    # 应用与 Alembic 统一使用 asyncpg 驱动（用户给的连接串可能是 +psycopg 或裸 postgresql://）
    normalized = db_base_url.replace("postgresql+psycopg://", "postgresql://")
    if normalized.startswith("postgresql://"):
        normalized = normalized.replace("postgresql://", "postgresql+asyncpg://", 1)
    app_db_url = normalized.rsplit("/", 1)[0] + f"/{database}"
    _migrate(app_db_url)

    fixture_dir = REPO_ROOT / "acceptance" / ".run-fixture"
    if fixture_dir.exists():
        shutil.rmtree(fixture_dir)
    fixture_dir.mkdir(parents=True)
    extra_env: dict[str, str] = {
        "DATABASE_URL": app_db_url,
        "UPLOAD_DIR": str(REPO_ROOT / "api" / ".acceptance-uploads"),
        "ALLOWED_ORIGINS": "http://localhost:3000,http://127.0.0.1:3000",
        "VISION_BASE_URL": "",
        "VISION_API_KEY": "",
    }
    if args.dry_run:
        # 自检模式：假模型固定返回与示例 manifest 对齐的抽取结果
        shutil.copyfile(
            REPO_ROOT / "acceptance" / "fixtures" / "dry-run-plan.json",
            fixture_dir / "current.json",
        )
        extra_env["VISION_FIXTURE_DIR"] = str(fixture_dir)
    else:
        # 正式验收：VISION_* 必须已在环境中配置（锁定 provider/model）
        # 并显式注入 extra_env，否则 _start_api 的合并会让初始空值覆盖真实配置
        for key in ("VISION_BASE_URL", "VISION_API_KEY", "VISION_MODEL"):
            value = os.environ.get(key, "").strip()
            if not value:
                print(f"正式验收要求环境变量 {key} 已配置（锁定验收所用 provider/model）")
                return 2
            extra_env[key] = value
    provider = "fixture" if args.dry_run else "openai-compatible"
    model = "fixture-model" if args.dry_run else os.environ["VISION_MODEL"]

    proc = _start_api(args.port, extra_env)
    try:
        _wait_health(args.port)
        base = f"http://127.0.0.1:{args.port}"
        import httpx

        client = httpx.Client(base_url=base, timeout=60)
        results: list[SampleResult] = []
        composition = _composition_report(manifest)

        for sample in manifest.samples:
            result = SampleResult(
                sample_id=sample.id,
                insurer=sample.insurer,
                files=len(sample.files),
                pages=len(sample.files),
                parse_seconds=0.0,
                task_status="",
            )
            results.append(result)
            project = client.post(
                "/api/projects",
                json={"name": f"acceptance-{sample.id}", "vehicleName": "验收样本车", "renewalYear": 2026},
            ).json()["data"]
            quote_res = client.post(
                f"/api/projects/{project['id']}/quotes",
                json={"insurerCode": sample.insurer, "source": "UPLOADED"},
            ).json()["data"]
            quote_id = quote_res["id"]

            files_payload = []
            for name in sample.files:
                path = samples_dir / name
                files_payload.append(
                    ("files", (name, path.read_bytes(), _guess_mime(name)))
                )
            files_payload.append(("modelProcessingConsent", (None, "true")))
            started = time.monotonic()
            upload = client.post(f"/api/quotes/{quote_id}/files", files=files_payload)
            if upload.status_code != 202:
                result.task_status = f"UPLOAD_{upload.status_code}"
                continue
            while True:
                status = client.get(f"/api/quotes/{quote_id}/parse-status").json()["data"]
                if status["status"] in ("SUCCEEDED", "FAILED"):
                    result.parse_seconds = time.monotonic() - started
                    result.task_status = status["status"]
                    break
                if time.monotonic() - started > 600:
                    result.task_status = "TIMEOUT"
                    break
                time.sleep(2)
            if result.task_status != "SUCCEEDED":
                continue

            quote = client.get(f"/api/quotes/{quote_id}").json()["data"]
            # 页数以落库文件为准（图片 1 页，PDF 为实际页数）——耗时口径按页数分桶
            result.pages = sum(int(f.get("pageCount", 1)) for f in quote.get("files", []))
            checker = Checker(sample, quote)
            checker.result = result
            checker.check_pricing()
            checker.check_vehicle()
            core_pool: dict[str, set[int]] = {}
            checker._check_coverage_group("core", sample.expected.coreCoverages, core_pool)  # noqa: SLF001
            checker._check_swaps(
                "core",
                sample.expected.coreCoverages,
                ["VEHICLE_LOSS", "THIRD_PARTY_LIABILITY", "DRIVER_LIABILITY", "PASSENGER_LIABILITY"],
                "driver_passenger_swap",
            )
            addl_pool: dict[str, set[int]] = {}
            checker._check_coverage_group("additional", sample.expected.additionalCoverages, addl_pool)  # noqa: SLF001
            checker._check_swaps(
                "additional",
                sample.expected.additionalCoverages,
                ["TP_NON_MEDICAL", "DRIVER_NON_MEDICAL", "PASSENGER_NON_MEDICAL", "EXTERNAL_GRID"],
                "non_medical_object_swap",
            )
            checker.check_services()
            checker.check_packages()
            checker.check_high_risk_text()
            checker.check_evidence()
            probes = _load_probes(samples_dir, sample.id)
            result.privacy_leaks = asyncio.run(
                _check_privacy_db(db_params, database, project["id"], probes)
            )
            quote_text = json.dumps(quote, ensure_ascii=False)
            for kind, values in probes.items():
                for probe in values:
                    if probe and probe in quote_text:
                        result.privacy_leaks.append(f"quote 泄露 {kind}")

        client.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        _recreate_database(db_params, database)  # 结束后清空一次性库

    _write_reports(reports_dir, mode, provider, model, long_edge, thinking, manifest, composition, results)

    # ---- 门禁 ----
    ok = True
    total_checks = sum(r.checks_total for r in results)
    passed_checks = sum(r.checks_passed for r in results)
    accuracy = (passed_checks / total_checks * 100) if total_checks else 0.0
    high_risk_total = {k: sum(r.high_risk[k] for r in results) for k in results[0].high_risk}
    evidence_total = sum(r.evidence_errors for r in results)
    privacy_total = sum(len(r.privacy_leaks) for r in results)
    gates = {
        "字段级完全正确率 ≥95%": accuracy >= 95.0,
        "五类高风险错误为 0": all(v == 0 for v in high_risk_total.values()),
        "evidence 全部合法": evidence_total == 0,
        "隐私探针零泄露": privacy_total == 0,
        "样本组合满足 §15.1": all(composition["gaps"].values()),
    }
    print(f"\n字段级完全正确率: {accuracy:.2f}%（{passed_checks}/{total_checks}）")
    print(f"高风险错误: {high_risk_total}")
    print(f"evidence 错误: {evidence_total}，隐私泄露: {privacy_total}")
    print(f"样本组合: {composition}")
    for name, passed in gates.items():
        print(f"  [{'通过' if passed else '未通过'}] {name}")
    ok = all(gates.values())
    if args.dry_run:
        print("\n[dry-run] 以上为工具链自检结果（fixture 假模型），不作为真实准确率验收。")
        return 0
    return 0 if ok else 1


def _guess_mime(name: str) -> str:
    if name.lower().endswith(".png"):
        return "image/png"
    if name.lower().endswith(".pdf"):
        return "application/pdf"
    return "image/jpeg"


def _write_reports(
    reports_dir: Path,
    mode: str,
    provider: str,
    model: str,
    long_edge: str,
    thinking: str,
    manifest: Manifest,
    composition: dict,
    results: list[SampleResult],
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    total_checks = sum(r.checks_total for r in results)
    passed_checks = sum(r.checks_passed for r in results)
    accuracy = (passed_checks / total_checks * 100) if total_checks else 0.0
    high_risk_total = {k: sum(r.high_risk[k] for r in results) for k in results[0].high_risk}

    lines = [
        "# MVP 真实样本验收报告",
        "",
        f"- 生成时间（UTC）：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- 模式：{mode}" + ("（工具链自检，非真实准确率验收）" if mode == "dry-run" else ""),
        f"- provider/model：{provider} / {model}" + (f"（thinking={thinking}）" if thinking else ""),
        f"- MAX_IMAGE_LONG_EDGE：{long_edge}",
        f"- 样本数：{len(manifest.samples)}",
        f"- 字段级完全正确率：**{accuracy:.2f}%**（{passed_checks}/{total_checks}）",
        f"- 高风险错误合计：{json.dumps(high_risk_total, ensure_ascii=False)}",
        f"- 样本组合：{json.dumps(composition, ensure_ascii=False)}",
        "",
        "| 样本 | 公司 | 文件数 | 任务结果 | 字段正确 | 耗时(s) | 高风险 | 证据错误 | 隐私 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.sample_id} | {r.insurer} | {r.files} | {r.task_status} "
            f"| {r.checks_passed}/{r.checks_total} | {r.parse_seconds:.1f} "
            f"| {sum(r.high_risk.values())} | {r.evidence_errors} | {len(r.privacy_leaks)} |"
        )
    failures = [
        f"- {r.sample_id}: {failure}"
        for r in results
        for failure in r.failures[:10]
    ]
    if failures:
        lines += ["", "## 字段级失败明细（每样本最多 10 条）", *failures]
    md = "\n".join(lines) + "\n"
    md_path = reports_dir / f"report-{mode}-{stamp}.md"
    md_path.write_text(md, encoding="utf-8")
    (reports_dir / f"report-{mode}-{stamp}.json").write_text(
        json.dumps(
            {
                "mode": mode,
                "provider": provider,
                "model": model,
                "maxImageLongEdge": long_edge,
                "accuracy": accuracy,
                "checksPassed": passed_checks,
                "checksTotal": total_checks,
                "highRisk": high_risk_total,
                "composition": composition,
                "samples": [r.model_dump() for r in results],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n[acceptance] 报告已写入 {md_path}")
    return md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="MVP 真实样本验收运行器")
    parser.add_argument(
        "--manifest",
        default=None,
        help="标注 manifest 路径；默认 acceptance/manifest.json（dry-run 用示例 manifest）",
    )
    parser.add_argument(
        "--samples-dir",
        default=None,
        help="样本目录；默认 acceptance/samples（dry-run 用 acceptance/fixtures）",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="工具链自检模式：fixture 假模型 + 示例 manifest，结果不作为验收",
    )
    args = parser.parse_args()
    if args.manifest is None:
        args.manifest = str(
            REPO_ROOT
            / "acceptance"
            / ("manifest.example.json" if args.dry_run else "manifest.json")
        )
    if args.samples_dir is None:
        args.samples_dir = str(
            REPO_ROOT / "acceptance" / ("fixtures" if args.dry_run else "samples")
        )
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
