# 车险报价对比助手

把不同保险公司、不同格式的车险报价单转换成统一结构，按「价格、保障、附加险、增值服务、额外保障包」横向对比。

- 产品与业务边界：[`docs/PRD.md`](docs/PRD.md)
- MVP 实现口径（数据模型 / 状态机 / 接口 / 验收）：[`docs/SPEC_MVP.md`](docs/SPEC_MVP.md)
- 工程任务清单：[`docs/TASKS.md`](docs/TASKS.md)

## 技术栈

| 端 | 技术 |
|---|---|
| 后端 `api/` | Python 3.13 · FastAPI · SQLAlchemy 2 (async) · Alembic · PostgreSQL · uv |
| 前端 `web/` | Next.js 16 (App Router) · React 19 · Tailwind CSS 4 · shadcn/ui · pnpm |

## 本地开发快速启动

前置要求：[uv](https://docs.astral.sh/uv/) ≥ 0.11、Node.js ≥ 22、pnpm ≥ 11，以及任一 PostgreSQL 17 实例（无 Docker 时见下方说明）。

```bash
# 1. 准备环境变量
copy .env.example .env      # Windows；macOS/Linux 用 cp

# 2. 启动数据库（有 Docker Desktop 时）
docker compose up -d

# 3. 初始化后端依赖并执行迁移（在 api/ 目录）
cd api
uv sync
uv run alembic upgrade head

# 4. 启动后端（默认仅监听 127.0.0.1:8000）
uv run uvicorn app.main:app --reload

# 5. 启动前端（新终端，在 web/ 目录）
pnpm install
pnpm dev                     # http://localhost:3000
```

没有 Docker 时：本仓库自带 pgsql 测试基础设施（`pgserver`），运行 `uv run pytest` 会自动启动一次性 PostgreSQL 实例，不需要外部数据库。若需要长期开发库，也可安装本机 PostgreSQL 并将 `DATABASE_URL` 指向它。

## 常用命令

后端（在 `api/`）：

```bash
uv run pytest                # 全量测试（自动创建一次性测试库）
uv run ruff check .          # 静态检查（仅检查，不自动改写）
uv run alembic upgrade head  # 迁移到最新
uv run alembic downgrade -1  # 回退一个版本
```

前端（在 `web/`）：

```bash
pnpm lint                    # ESLint
pnpm test --run              # Vitest 单测
pnpm build                   # 生产构建
pnpm gen:api                 # 从后端 OpenAPI 重新生成前端类型
pnpm check:api               # 校验前端类型与后端 OpenAPI 无漂移
```

## 安全与隐私约定

- 原始报价文件保存在 `api/uploads/`（gitignore），包含个人信息，绝不进入版本控制。
- 后端默认仅监听 `127.0.0.1`；改为局域网地址必须配置 `LOCAL_ACCESS_TOKEN`，否则拒绝启动。
- 模型密钥只放在 `.env`；日志与测试输出不得出现密钥、原图 base64、手机号、身份证号、VIN、完整车牌。
- 删除项目会级联删除其报价与文件记录，不可恢复。

## 目录结构

```text
api/          FastAPI 后端（app/ 应用包、alembic/ 迁移、tests/ 测试）
web/          Next.js 前端（app/ 路由、components/ 组件、lib/ API 客户端）
docs/         PRD / SPEC / 任务清单
```
