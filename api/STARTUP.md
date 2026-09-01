# API 启动指南

后端技术栈：FastAPI + SQLAlchemy 2 (async) + Alembic + PostgreSQL，依赖用 [uv](https://docs.astral.sh/uv/) 管理，Python ≥ 3.13。

## 前置条件

| 工具 | 用途 | 检查命令 |
| --- | --- | --- |
| uv ≥ 0.5 | Python 依赖与虚拟环境管理 | `uv --version` |
| Docker | 运行本地 PostgreSQL（仅此一个容器） | `docker --version` |

安装 uv（Windows PowerShell）：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## 启动步骤

以下命令均假定当前目录为 `api/`（第 1 步除外）。

### 1. 启动 PostgreSQL（仓库根目录执行）

```bash
docker compose up -d
```

- 容器名 `car-insurance-postgres`，宿主机端口 **5433**（避开本机已装的 5432）。
- 凭据：`postgres / postgres`，库名 `car_insurance`，与默认 `DATABASE_URL` 一致，无需额外配置。
- 数据存放在命名卷中，`docker compose down` 停止但保留数据；`down -v` 才会清库。

### 2. 安装依赖

```bash
uv sync
```

### 3. 数据库迁移

```bash
uv run alembic upgrade head
```

要求 PostgreSQL 已在 5433 端口可用（即第 1 步完成）。

### 4. 启动开发服务器

```bash
uv run uvicorn app.main:app --reload
```

- 地址：<http://127.0.0.1:8000>
- 交互文档：<http://127.0.0.1:8000/docs>
- 健康检查：`GET /health`（唯一免令牌的接口）
- 代码改动自动重载（`--reload`）。

## 配置（.env，可选）

配置从**仓库根目录 `.env`** 或 `api/.env` 读取，显式环境变量优先。不创建 `.env` 时全部使用默认值，本机开发即可跑通。常用项：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/car_insurance` | 必须是 PostgreSQL 连接串，否则拒绝启动 |
| `UPLOAD_DIR` | `./api/uploads`（相对仓库根） | 上传文件落盘目录 |
| `LOCAL_ACCESS_TOKEN` | 空 | 非空即启用令牌校验：除 `/health` 外全部接口、原文件与 `/docs` 都要求请求头 `X-Access-Token` |
| `APP_BIND_HOST` | `127.0.0.1` | **绑定非回环地址且未配置令牌时，应用直接拒绝启动**（安全不变量） |
| `ALLOWED_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | CORS 白名单，逗号分隔。前端若跑在其他端口（如 3311），需把对应 origin 加进来，否则浏览器请求会被拦 |
| `VISION_BASE_URL` / `VISION_API_KEY` / `VISION_MODEL` | 空 / 空 / `glm-4.5v` | OpenAI 兼容视觉端点；不配置时解析任务安全失败（脱敏提示），不影响手动录入 |
| `MAX_TOTAL_PAGES_PER_QUOTE` | `12` | 单次多图调用上限 |

## 常见问题

- **连接数据库失败**：确认 `docker ps` 里 `car-insurance-postgres` 是 Up 且 healthy；端口占用时改 compose 映射并同步改 `DATABASE_URL`。
- **8000 端口被占用**：`uv run uvicorn app.main:app --reload --port 8001`，并同步把新端口加进 `ALLOWED_ORIGINS`。
- **前端联调被 CORS 拦截**：见上表 `ALLOWED_ORIGINS`。
- **解析任务一直失败**：多为 `VISION_*` 未配置，属预期的安全失败；配置后可对失败报价重试解析。

## 相关脚本

```bash
uv run python scripts/verify_startup.py   # 启动验证
uv run python scripts/smoke_task04.py     # 全栈冒烟（假 provider，不耗模型额度）
uv run pytest                             # 测试（自动起一次性 PostgreSQL，不占用 5433）
uv run ruff check .                       # 代码检查
```
