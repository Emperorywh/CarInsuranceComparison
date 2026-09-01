# 车险报价对比助手

把不同保险公司、不同格式的车险报价单转换成统一结构，按「价格、保障、附加险、增值服务、额外保障包」横向对比。

- 产品与业务边界：[`docs/PRD.md`](docs/PRD.md)
- MVP 实现口径（数据模型 / 状态机 / 接口 / 验收）：[`docs/SPEC_MVP.md`](docs/SPEC_MVP.md)
- 工程任务清单：[`docs/TASKS.md`](docs/TASKS.md)
- 验收样本与报告：[`acceptance/README.md`](acceptance/README.md)

## 技术栈

| 端 | 技术 |
|---|---|
| 后端 `api/` | Python 3.13 · FastAPI · SQLAlchemy 2 (async) · Alembic · PostgreSQL · uv |
| 前端 `web/` | Next.js 16 (App Router) · React 19 · Tailwind CSS 4 · shadcn/ui · pnpm |

## 本地开发快速启动（Windows / macOS / Linux 通用）

前置要求：[uv](https://docs.astral.sh/uv/) ≥ 0.11、Node.js ≥ 22、pnpm ≥ 11，以及任一 PostgreSQL 实例（15–17 均可；无 Docker 时见下方说明）。

```bash
# 1. 准备环境变量
copy .env.example .env      # Windows；macOS/Linux 用 cp

# 2. 启动数据库（有 Docker Desktop 时）
docker compose up -d        # 宿主机 5433 端口，避开本机默认 5432

# 3. 初始化后端依赖并执行迁移（在 api/ 目录）
cd api
uv sync                     # 按 uv.lock 精确安装
uv run alembic upgrade head # 从空库迁移到最新

# 4. 启动后端（仅监听 127.0.0.1:8877）
uv run uvicorn app.main:app --reload --port 8877

# 5. 启动前端（新终端，在 web/ 目录）
pnpm install --frozen-lockfile
pnpm dev                    # http://localhost:3000
```

没有 Docker 时：本仓库测试自带嵌入式 PostgreSQL（首次运行 `uv run pytest`
自动下载缓存到用户目录，之后完全离线）。开发用持久库也可以直接安装本机
PostgreSQL，把 `.env` 的 `DATABASE_URL` 指向它，再执行迁移。

## 数据库迁移

```bash
cd api
uv run alembic upgrade head      # 迁移到最新（从空库一键可迁移）
uv run alembic downgrade -1      # 回退一个版本
uv run alembic revision -m "..." # 生成新迁移（开发时）
```

迁移只会增量建表/加列，不会改写历史；首次部署从空库 `upgrade head`
即完成全部初始化。

## 常用命令

后端（在 `api/`）：

```bash
uv run pytest                # 全量测试（自动创建一次性测试库，离线可跑）
uv run ruff check .          # 静态检查（仅检查，不自动改写）
uv run python scripts/verify_startup.py   # 启动安全矩阵冒烟（12 项）
```

前端（在 `web/`）：

```bash
pnpm lint                    # ESLint
pnpm test --run              # Vitest 单测/组件测试
pnpm build                   # 生产构建
pnpm e2e                     # Playwright 端到端主路径门禁（自动构建前端）
pnpm gen:api                 # 从后端 OpenAPI 重新生成前端类型
pnpm check:api               # 校验前端类型与后端 OpenAPI 无漂移
```

## 视觉模型供应商配置与数据流告知

- 解析走 OpenAI 兼容 `chat/completions` 多图接口：在 `.env` 配置
  `VISION_BASE_URL` / `VISION_API_KEY` / `VISION_MODEL`（智谱 GLM 视觉、
  阿里 DashScope 兼容端点或任何 OpenAI 兼容中转均可）。
- **数据流**：上传的报价单原文件会发送至上述供应商用于推理，不会发送
  到第三方对象存储；原文件本身只保存在本机 `api/uploads/`。首次解析前
  产品会弹窗告知并取得同意（项目级记录一次），拒绝同意仍可完整手动录入。
- **供应商配置责任**：如供应商提供「数据不用于训练」「最短留存」「零
  留存」选项，部署者有责任在供应商控制台/参数中启用并确认政策；本工具
  的本地措施不能替代供应商侧配置。
- `VISION_FIXTURE_DIR` 是仅测试可启用的假模型开关（端到端测试注入），
  正式部署**绝不配置**，否则解析结果为固定测试数据。

## 局域网模式：访问令牌与 CORS

- 默认仅监听 `127.0.0.1`，本机使用无需任何令牌。
- 手机/局域网访问：`.env` 设 `APP_BIND_HOST=0.0.0.0`（或本机局域网 IP）
  并**必须**设置 `LOCAL_ACCESS_TOKEN=<自定义口令>`，否则后端拒绝启动；
  同时把手机实际访问的来源加入 `ALLOWED_ORIGINS`（如
  `http://192.168.1.5:3000`），`NEXT_PUBLIC_API_BASE_URL` 改为电脑局域网
  IP 后重新构建/启动前端。浏览器首次访问或收到 401 时会弹出令牌输入框，
  令牌只存浏览器 localStorage，不进入 URL 与日志。

## 备份与不可恢复删除

- 项目数据 = PostgreSQL 记录 + `api/uploads/` 磁盘文件，两者必须一起
  备份（建议：停服后用 `pg_dump` + 直接拷贝 uploads 目录）。
- **删除项目不可恢复**：级联删除项目下全部报价、解析记录与文件关联，
  并在事务提交后清理磁盘目录；界面有二次确认。删除单个报价只清理无任何
  引用的文件资产（拆分共享的原文件会保留）。
- 解析失败不会破坏旧数据：待确认报价失败保留上次候选；已确认报价失败
  保持 CONFIRMED 且继续可对比。

## 验收（真实样本）

```bash
# 工具链自检（无需模型密钥；fixture 假模型，结果不作为验收）
cd api && uv run python ../acceptance/run_acceptance.py --dry-run

# 正式验收：锁定 provider/model，逐份上传真实样本并逐字段评分
# 前置：.env 配置 VISION_* 与 E2E_DATABASE_URL（外部 PostgreSQL）
uv run python ../acceptance/run_acceptance.py

# 提交验收产物前
uv run python ../acceptance/privacy_scan.py
```

门禁口径（SPEC §15.2）：字段级完全正确率 ≥95%；司机/乘客互换、三个
医保外对象互换、保障包污染主险、销售标注污染正式字段、明确 0 元服务
识别为不包含这五类高风险错误为 0；evidence 全部合法；隐私探针零泄露。
详见 [`acceptance/README.md`](acceptance/README.md)。

## 常见失败与恢复

| 现象 | 处理 |
|---|---|
| 后端启动报「必须配置 LOCAL_ACCESS_TOKEN」 | 绑定了非回环地址：补配令牌，或把 `APP_BIND_HOST` 改回 `127.0.0.1` |
| 前端一直提示「无法连接后端服务」 | 后端未启动或 `NEXT_PUBLIC_API_BASE_URL` 指错；改 `.env` 后需重启 `pnpm dev` |
| 解析任务 FAILED，提示检查 VISION_* 配置 | `.env` 缺少/错填 `VISION_BASE_URL/API_KEY/MODEL`；修正后点「重试解析」，或「转手动录入」 |
| 解析任务 FAILED，提示调低页数或更换供应商 | 单次任务页数超过供应商单请求上限：调低 `MAX_TOTAL_PAGES_PER_QUOTE` 或更换供应商（MVP 不自动分批） |
| 数据库连不上（compose 模式） | 确认 `DATABASE_URL` 端口是 **5433**（compose 映射），容器 `docker compose ps` 是否健康 |
| `pnpm check:api` 漂移报错 | 后端接口变更后未重新生成类型：先 `pnpm gen:api` 再检查 |
| E2E 报「端口 8310 已被占用」 | 上次运行残留：`cd api && uv run python scripts/e2e_harness.py down`，或手动结束占用进程 |
| 验收运行器报缺少 E2E_DATABASE_URL | 在仓库根 `.env` 增加 `E2E_DATABASE_URL=postgresql://…`（运行器需要外部 PostgreSQL 建一次性库） |

## 安全与隐私约定

- 原始报价文件保存在 `api/uploads/`（gitignore），包含个人信息，绝不进入版本控制，也绝不通过静态目录对外暴露；仅能经受控接口 `GET /api/files/{id}/raw`（校验访问令牌与项目归属）读取。
- 后端默认仅监听 `127.0.0.1`；改为局域网地址必须配置 `LOCAL_ACCESS_TOKEN`，否则拒绝启动。
- 模型密钥只放在 `.env`；日志与测试输出不得出现密钥、原图 base64、手机号、身份证号、VIN、完整车牌。
- 上传只接受 JPEG/PNG/PDF，并校验扩展名、声明 MIME 与文件签名一致；超限或伪造一律拒绝。
- 首次上传解析前，产品会明确告知“原文件将发送至所配置的视觉模型”并取得同意（项目级记录一次）；拒绝同意仍可手动录入。
- 长图导出是白名单渲染：只包含方案展示名、公司、价格、保障差异与免责声明；保险员、车辆摘要、证据原文、备注等不进入导出图。
- 删除项目会级联删除其报价与文件记录并清理磁盘目录，不可恢复；删除报价仅清理无任何引用的文件资产（共享文件保留）。

## 目录结构

```text
api/          FastAPI 后端（app/ 应用包、alembic/ 迁移、tests/ 测试、scripts/ 冒烟与 E2E 编排）
web/          Next.js 前端（app/ 路由、components/ 组件、lib/ API 客户端、e2e/ Playwright 主路径）
acceptance/   真实样本验收（标注 manifest、运行器、隐私扫描、报告）
docs/         PRD / SPEC / 任务清单
```
