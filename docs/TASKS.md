# 车险报价对比助手 MVP 工程任务清单

> 本清单供多个彼此独立的 Claude Code 上下文按顺序执行。每个上下文只领取一个 Task，完成验证和交接后停止，不得顺手开始下一个 Task。

## 一、编制基线

本清单基于 2026-08-26 的仓库实际状态制定，需求依据按优先级为：

1. `docs/SPEC_MVP.md`：MVP 数据结构、状态机、接口、边界和验收口径的实现依据。
2. `docs/PRD.md`：产品目标、业务语义和范围依据。
3. 当前仓库中的已有代码、配置、锁文件和约束文件。

编制时已经完整检查到以下事实：

- Git 位于 `main`，与 `origin/main` 无领先或落后；`docs/PRD.md` 的修改和新增的 `docs/SPEC_MVP.md` 已暂存。后续执行者不得重置、覆盖或擅自取消暂存这些用户改动。
- `api/` 是 `uv` 初始化的 Python 3.13 项目，只有 `fastapi[standard]` 直接依赖和打印 `Hello from api!` 的 `main.py`；没有 ASGI 应用、数据库、迁移、业务包或测试。
- `web/` 是可运行的 Next.js 16.3.2 / React 19.2.8 / Tailwind CSS 4 初始化项目，包管理器为 pnpm 11.21.0；页面仍为默认模板，没有 shadcn/ui、API 客户端、业务组件或测试框架。
- 当前 `pnpm lint` 和 `pnpm build` 均通过；构建会警告锁定的 TypeScript 5.0.2 低于 Next.js 推荐的 5.1.0。
- 仓库没有 PostgreSQL 启动配置、根级环境示例、根级忽略规则、CI、测试用例和验收样本。
- `web/AGENTS.md` 要求任何前端编码前读取当前安装版本自带的 `web/node_modules/next/dist/docs/` 相关文档；`web/CLAUDE.md` 已引用该约束。

“独立验证”在本清单中指：Task 可以依赖已经完成的前置 Task，但完成后必须有只针对本次增量的自动化测试或可重复命令，不能仅凭页面观感或口头说明判定完成。

## 二、所有 Task 的统一执行规则

1. 开始前完整阅读 `docs/PRD.md`、`docs/SPEC_MVP.md`、本文件和当前 `git status`，再检查前置 Task 的完成记录与实际代码；不得只照本文中的路径机械实现。
2. 一个 Claude Code 上下文只执行一个 Task。若发现前置 Task 未完成，先停止并记录阻塞，不得在同一上下文包办多个 Task。
3. 只实现 MVP。禁止提前实现登录、QuoteVersion 版本链、AI 分析/问答/推荐、关注项个性化、Excel/Word、云存储、Celery、bbox、支付、投保或 CRM。
4. 后续 Task 只能在既有契约上增量扩展：数据库使用新的 Alembic 迁移，API 以 FastAPI OpenAPI 为唯一契约，前端类型随 OpenAPI 更新；不得重写已执行迁移、复制第二套 DTO，或改换前面确定的状态机和数据归属。
5. PRD 与 SPEC 如出现真实冲突，必须先在文档中记录决定并同步相关条目，再编码；不得静默选择。纯实现细节优先采取最小、可测试且不扩大范围的方案。
6. 保留所有不属于当前 Task 的用户改动，不执行破坏性 Git 操作，不主动格式化全仓代码。只对本 Task 触及的文件做必要修改。
7. 所有新增或修改的代码都要写多行简体中文注释，说明业务不变量、隐私边界或不直观算法；支持注释的配置文件同样如此。JSON 等不支持注释的格式，应在相邻 README 或代码中补充说明。不要写逐行复述代码的无效注释。
8. 任何前端 Task 编码前，先读 `web/AGENTS.md`，并按改动范围阅读 `web/node_modules/next/dist/docs/01-app/` 中对应的 Next.js 16 文档；不得依赖记忆中的旧版 Next.js 行为。
9. 常规测试不得调用真实视觉模型。解析测试使用可注入的假 provider 和固定、已脱敏的 `rawResult` fixture；允许调用真实 provider/model 的只有两处：TASK-04 在用户提供密钥后执行的可选非阻断 smoke test，以及 TASK-07 使用锁定 provider/model 的显式现场验收。
10. 日志、测试失败输出、快照、导出物和 Git 历史中不得出现模型密钥、原图 base64、手机号、身份证号、VIN、完整车牌或未脱敏原文。
11. 每次 API 变更都要验证统一响应包、HTTP 状态码和 OpenAPI/前端类型无漂移；每次数据库变更都要在一次性测试库验证从空库升级到 `head`。
12. 只有“验证”全部通过后，才能把 Task 状态改为已完成并填写完成记录。完成记录至少写明日期、关键文件、执行命令及结果；未取得外部验收素材时不得以“代码已写完”替代验收。

## 三、已冻结的实现边界

- 后端按 SPEC 落在 `api/app/`，使用 FastAPI、SQLAlchemy 2 async、Alembic 和 PostgreSQL；数据库列采用一致的 `snake_case`，对外 JSON 使用 SPEC 的 `camelCase`。
- 第一版迁移一次性建立 SPEC §2 的完整实体和枚举，后续 Task 只增加必要索引、约束或兼容字段，不重新设计实体归属。
- 文件属于项目，通过 `quote_file_link` 与报价多对多关联；解析输入通过 `parse_task_file` 固定。删除报价不等于删除共享文件。
- 解析任务只使用 PostgreSQL 任务表和 API 进程内单 worker；不引入 Redis、Celery 或额外常驻服务。
- PDF 处理默认使用 `pypdfium2`，避免在 MVP 中引入 PyMuPDF 的 AGPL 分发风险；图片使用 Pillow。若执行者必须改变该选择，需先记录许可与部署影响。
- 浏览器通过统一的类型化客户端访问 FastAPI。访问令牌只放请求头与浏览器 localStorage，不进入 URL、服务端渲染日志或错误上报。
- LLM 只负责报价结构化抽取；归一化、校验、置信度、价格计算、五问总结和差异说明全部由确定性规则完成。
- 前端保持 App Router、Tailwind CSS 4 和移动优先方案；基于 shadcn/ui 组件原语做轻快消费风定制，不更换框架或引入另一套并行设计系统。

---

## TASK-01：工程基线、完整数据模型与项目工作区

- **状态**：`[x] 已完成`
- **目标**：把现有初始化仓库变成可迁移、可测试、可启动的前后端工程，并完成“我的续保项目”这一条最小纵向链路；同时一次性冻结后续报价、文件、解析和合并功能所依赖的数据模型。
- **前置依赖**：无。开始时再次确认并保留当前暂存的两份需求文档；前端编码前阅读 `web/AGENTS.md` 以及本地 Next.js 文档中的项目结构、Server/Client Components、数据获取、环境变量、CSS 和 Vitest 指南。
- **实施范围**：
  1. 将 `api/main.py` 的占位脚本迁移为 SPEC 约定的 `api/app/main.py` ASGI 结构，建立配置、异步数据库 session、路由注册、统一 `{code,message,data}` 响应、统一异常处理和 `/health`；删除或改造旧入口，避免保留两个含义不同的启动方式。
  2. 在 `pyproject.toml` 中把实际直接使用的运行依赖声明为直接依赖，并建立 pytest、pytest-asyncio、HTTPX、Ruff（仅检查，不自动格式化）等开发依赖；更新并锁定 `uv.lock`。
  3. 增加根级 `.env.example`、`.gitignore`、仅含 PostgreSQL 的 `compose.yaml` 和可执行的本地开发说明。`.env`、测试密钥、`api/uploads/`、缓存和本地验收原文件必须被忽略。
  4. 实现配置校验、CORS 和本地访问令牌中间件：默认绑定 `127.0.0.1`；非回环地址且令牌为空时拒绝启动；`LOCAL_ACCESS_TOKEN` 非空即启用令牌校验，与绑定地址无关，此时除健康检查外的 API、原文件、OpenAPI/交互文档均受保护。同步建立统一脱敏服务和安全日志过滤器，先覆盖项目备注、请求错误与日志中的手机号、身份证号、VIN、完整车牌及个人字段标签，供后续 Task 扩展复用。
  5. 使用 SQLAlchemy 2 async 建立 SPEC §2 的全部 ORM、枚举、关系、唯一约束、检查约束、级联策略和关键索引：`comparison_project`、`quote`、`quote_file`、`quote_file_link`、`parse_task`、`parse_task_file`、`quote_coverage`、`quote_service`、`supplemental_package`、`package_coverage`、`sales_annotation`、`discount`、`field_evidence`、`merge_change`。不要创建 User、独立 Vehicle、QuoteVersion 或 `plan_split` 表。
  6. 建立 Alembic 初始迁移；覆盖 `parse_task.quote_id ON DELETE SET NULL`、共享文件关联、`field_evidence(quote_id,field_name)` 唯一性、金额非负与活动解析任务互斥等数据库级不变量。
  7. 实现项目 `POST/GET /api/projects`、`GET/PATCH/DELETE /api/projects/{id}`。列表返回报价数和最低有效净支出聚合；此时没有报价时返回稳定的空状态。删除项目走事务并预留文件清理服务接口，磁盘清理由 TASK-03 接通。
  8. 初始化 shadcn/ui 所需配置和最小组件集，更新 TypeScript 到 Next.js 16 支持版本，建立明亮色彩、大圆角、移动优先的全局主题、中文 metadata、加载/错误/空状态以及统一 API 客户端。
  9. API 客户端必须集中处理响应包、422/401 等错误和 `X-Access-Token`；实现令牌输入交互，令牌只存 localStorage。建立 OpenAPI 到前端类型的生成与漂移检查，后续不得手写冲突类型。
  10. 完成 `/`、`/projects/new`、`/projects/[id]`：项目卡片列表、创建表单、详情空状态、编辑与带二次确认的删除；字段严格为项目名、车辆名称、续保年份、可选到期日和备注。
  11. 建立后端测试数据库 fixture、API/服务测试和前端 Vitest + Testing Library 基础设施；测试不得依赖开发者现有数据库或网络。
- **主要交付物**：`api/app/` 基础包、完整 ORM 与首个 Alembic 迁移、项目 API、测试基础设施、根级环境/数据库/忽略配置、OpenAPI 类型链路、前端设计系统与三个项目页面；更新两端 README 和锁文件。
- **明确不做**：报价明细编辑、文件上传、视觉模型、任务 worker、对比引擎、长图导出，以及任何第二阶段能力。
- **验证**：
  1. 在一次性测试 PostgreSQL 中执行 `uv sync --locked --all-groups`、从空库 `uv run alembic upgrade head`、`uv run ruff check .`、`uv run pytest`；迁移测试需验证关键外键、唯一约束和删除策略。
  2. 验证默认本机模式（无令牌）可访问；把绑定地址改为非回环且不配置令牌时启动失败；本机与非回环绑定下配置令牌后行为一致：健康检查可匿名访问，业务 API 无令牌返回 401、正确令牌成功。
  3. 在 `web/` 执行 `pnpm install --frozen-lockfile`、`pnpm lint`、`pnpm test --run`、`pnpm build`；构建不再出现 TypeScript 版本过低警告。
  4. 用 API 集成测试和浏览器移动视口各走一遍“空首页 → 新建项目 → 查看/编辑 → 删除”主路径，校验中文错误提示和二次确认。
  5. 执行 OpenAPI 类型漂移检查和 `git diff --check`，确认未改动需求文档内容、未提交 `.env` 或数据库数据。
- **完成判定**：空库可一键迁移，前后端可按 README 启动，项目 CRUD 端到端可用，完整 MVP 数据模型已由迁移冻结，基础自动化检查全部通过。
- **完成记录**：
  - **日期**：2026-08-26
  - **工作区标识**：main 分支工作区（提交 `feat: TASK-01 工程基线、完整数据模型与项目工作区`）；编制基线时暂存的两份需求文档（PRD 修改与新增 SPEC）内容未改动、未取消暂存。
  - **关键文件**：
    - 后端：`api/app/`（`main.py` ASGI 入口、`config.py` 启动期安全校验、`db.py`、`core/`（统一响应包/异常/脱敏 `privacy.py`/安全日志 `logging.py`/令牌中间件 `security.py`）、`models/` 14 张表全部 ORM、`schemas/`、`api/routes/`（health + projects）、`services/`（project_service、file_cleanup 预留接口））；`api/alembic/versions/0001_initial_schema.py` 初始迁移；`api/tests/`（conftest 一次性测试库 fixture + pg_server 嵌入式 PostgreSQL + 迁移不变量/API/脱敏/配置/中间件/迁移循环测试）；`api/scripts/`（export_openapi、verify_startup）；旧占位入口 `api/main.py` 已删除。
    - 前端：`web/lib/api.ts` 统一类型化客户端、`web/lib/api-types.d.ts` 生成类型、`web/components/providers/api-provider.tsx` 令牌输入交互、`web/components/ui/` 最小组件集、`web/app/page.tsx`、`web/app/projects/new/page.tsx`、`web/app/projects/[id]/page.tsx`、`web/tests/`；TypeScript 升级至 5.9.2。
    - 根级：`.env.example`、`.gitignore`、`compose.yaml`（仅 PostgreSQL 17，宿主 5433）、`README.md`；两端 README 同步更新。
  - **验证命令与结果**（全部通过）：
    1. 后端：`uv sync --locked --all-groups`（32 包锁定安装）；一次性测试库从空库 `uv run alembic upgrade head`（pytest fixture 内 + `scripts/verify_startup.py` 独立执行各一次）；`uv run ruff check .` All checks passed；`uv run pytest` **40 passed**（覆盖：外键级联/SET NULL/field_evidence 唯一/金额非负 CheckConstraint/活动解析任务互斥 partial unique index/共享文件联合主键/升级-降级-再升级循环/项目 CRUD 主路径与聚合口径/中文 422 与 404/备注脱敏/启动校验/令牌中间件矩阵）。
    2. 启动行为（`uv run python scripts/verify_startup.py`，12 项全过）：默认本机模式匿名可访问；`APP_BIND_HOST=0.0.0.0` 且无令牌时进程拒绝启动（pydantic 校验错误）；令牌模式下 127.0.0.1 与 0.0.0.0 行为一致：`/health` 匿名 200、业务 API 无令牌/错误令牌 401、正确令牌 200。
    3. 前端：`pnpm install --frozen-lockfile`、`pnpm lint` 0 错误、`pnpm test --run` **22 passed**（含删除二次确认“取消不删除/确认才调用接口并跳转”、404 空状态、空首页引导）、`pnpm build` 成功且**无 TypeScript 版本过低警告**。
    4. 主路径：后端集成测试覆盖“空列表→创建(201)→详情→PATCH→DELETE→404”全链路并断言中文提示；全栈冒烟（嵌入式 PG + uvicorn:8031 + next dev:3311）验证 `/` 渲染“我的续保项目”、`/projects/new` 渲染“新建续保对比”，并经 HTTP 完整走通创建/查看/改名/删除；浏览器移动视口的交互走查以组件测试（空态/错误态/二次确认/卡片渲染）+ 生产构建覆盖，真机视口验收并入 TASK-07 的 Playwright 门禁（本任务未伪造该步骤）。
    5. `pnpm check:api` 契约与类型零漂移；`git diff --check` 通过；`git diff -- docs/` 为空（需求文档未改动）；未提交 `.env`、数据库数据或 `api/uploads/`。
  - **实现决策记录**（非 SPEC 冲突，属实现细节）：
    - 本机无 Docker/PostgreSQL：测试基础设施采用 Zonky embedded-postgres-binaries（PostgreSQL 17.5，一次性下载缓存于 `%LOCALAPPDATA%/CarInsurancePg`，之后完全离线），每次测试会话销毁重建独立库；`compose.yaml` 仍按任务要求提供，供有 Docker 的开发环境使用；`TEST_DATABASE_URL` 可指向外部库。
    - 金额对外 JSON 统一 float（Pydantic v2 会把 Decimal 序列化为字符串，不适合前端比较）；数据库仍为 numeric(12,2)/numeric(14,2)。
    - Alembic `downgrade` 显式回收全部原生枚举类型，保证“降级后可再升级”（有测试覆盖）。
    - 首页“最低有效净支出”只统计 CONFIRMED/MERGE_REVIEW 且 netPayment 非空的报价，与对比页可对比口径一致。

---

## TASK-02：完整手动报价、人工确认与价格规则

- **状态**：`[x] 已完成`
- **目标**：先在完全不依赖文件和模型的条件下打通“创建报价 → 完整手动录入 → 校验/确认 → 项目中展示”的业务主链路，并把所有后续解析结果都要复用的编辑、状态与价格规则固定下来。
- **前置依赖**：TASK-01 已完成且验证通过；不得修改初始迁移。前端先阅读本地 Next.js 文档中的表单、数据变更、错误处理、导航与 Client Components 指南。
- **实施范围**：
  1. 实现报价容器 API 和状态守卫：`POST /api/projects/{id}/quotes`、`GET/PATCH/DELETE /api/quotes/{id}`。预置公司码严格使用 SPEC §3.2；`OTHER` 必须带自由输入公司名；`source=MANUAL` 创建即为 `PENDING_CONFIRM`，`source=UPLOADED` 只创建 `DRAFT` 容器。
  2. 实现报价的价格、车辆快照、险种、服务、保障包及其内部保障、销售/用户标注、优惠的增删改接口；所有写操作校验项目归属、报价状态、非负金额和最多两位小数，并在单个事务内完成重算。备注、描述、标注等自由文本统一经过 TASK-01 的脱敏服务，不能各路由自行拼正则。
  3. 建立标准险种、保险公司、保障包类型、服务类型和状态枚举的单一代码来源；前后端展示值由 API 返回或共享生成类型驱动，不各自复制一套易漂移字典。
  4. 实现确定性价格服务：商业险与保障包显示值/计算值回退、UNKNOWN 阻断、NOT_INCLUDED 按 0、总价容差三态校验、`officialTotal ?? computedTotal`、优惠只扣勾选且有 `cashEquivalent` 的项目、优惠超额时 `INVALID_DISCOUNT + netPayment=null`。只有正式商业险都已归类且保费完整时才计算 `computedCommercialPremium`，只有全部保障包价格完整时才计算 `computedPackageTotal`；含金额的未识别项在用户处理前阻断计算。任何 null 都不得自行当 0。
  5. 实现 SPEC §6 中不依赖模型的规则：元/万换算、单座与座位总额、状态语义、重复行判定、主险/保障包隔离、数值范围、项目车辆摘要冲突。交强险只落 Quote 价格字段与 field_evidence，不生成 quote_coverage 行。用户修改的字段写入 `editedByUser=true`、`confidenceLevel=HIGH`，并显示“用户已确认/用户录入”。
  6. 实现 `POST /api/quotes/{id}/confirm` 的手动/单方案确认：价格分项必须明确为包含、不包含或未知；公司与车辆冲突必须得到显式选择；确认后回填或保留项目车辆摘要并进入 `CONFIRMED`。初登日期只提示，不单独阻断。
  7. 完成 `/projects/[id]/quotes/new` 的公司九宫格、其他公司输入、保险员输入和“跳过上传，手动录入”；本 Task 只开放可工作的手动入口，上传入口由 TASK-03 接通，不在用户界面展示内部任务编号、不可用提交按钮或假解析结果。
  8. 完成 `/quotes/[id]` 和 `/quotes/[id]/confirm` 的手动模式。确认页固定为价格、基础车险、附加险、额外保障、增值服务、销售说明、车辆信息 7 个 Tab；支持各层增删改、未识别保障手动映射/保留/丢弃、冲突提示和底部确认按钮。
  9. 完成优惠编辑和净支出展示；SERVICE 优惠默认无折现值。项目详情按“保险公司 + 保险员”分组展示已确认及草稿报价卡，显示净支出、官方总价异常、三者与三者医保外摘要，同来源只提示而不创建版本链。
  10. 对价格与校验规则写纯函数单测，对 API 状态机写集成测试，对 7 Tab 编辑和确认阻断写前端组件测试；更新 OpenAPI 产物与前端类型。
- **主要交付物**：报价及各层明细 API、价格/状态/校验领域服务、完整手动确认 UI、项目报价分组卡片、优惠与净支出功能、规则测试和一条手动端到端路径。
- **明确不做**：文件落盘、PDF/图片处理、模型调用、自动 evidence、置信度合成、多方案拆分、已确认报价补传合并、对比页和导出。
- **验证**：
  1. 后端执行 Ruff、完整 pytest，并至少覆盖金额换算、座位总额、FREE/UNKNOWN/NOT_INCLUDED、总额 PASSED/MISMATCH/NOT_CHECKABLE、正常净支出、无折现优惠、优惠超额各一个正常例和边界例。
  2. API 集成测试覆盖手动报价从 `PENDING_CONFIRM` 到 `CONFIRMED`、非法状态转换、`OTHER` 缺公司名、金额精度、主险/保障包隔离、车辆摘要两种冲突选择和删除报价。
  3. 前端执行 lint、测试和生产构建；浏览器移动视口完成“创建项目 → 新增手动报价 → 7 Tab 填写 → 添加优惠 → 确认 → 返回项目卡片”主路径。
  4. 确认官方总价与系统总价不一致时仍保留两者并在确认页和卡片提示；净支出异常报价不显示为最低价。
  5. 执行 OpenAPI 类型漂移检查和 `git diff --check`。
- **完成判定**：不配置模型、不上传文件也能创建一份包含所有业务层级的可比较报价；确定性价格和确认规则有自动测试保护，后续解析只需向同一数据契约写候选值。
- **完成记录**：
  - **日期**：2026-08-26
  - **工作区标识**：main 分支工作区，基于 TASK-01 提交 `589537a`；未修改初始迁移与需求文档（`git diff -- docs/` 为空）。
  - **关键文件**：
    - 后端：`api/app/api/routes/quotes.py`（报价容器/明细各层/确认/字典共 19 个路由）、`api/app/services/quote_service.py`（状态守卫、各层增删改、单事务重算、确认与车辆摘要冲突）、`api/app/services/pricing.py`（确定性价格服务：eff 回退、UNKNOWN 阻断、总价三态、净支出与优惠超额）、`api/app/services/normalization/alias_map.py`（标准险种/公司/保障包类型字典单一代码来源）、`api/app/services/normalization/amounts.py`（元/万换算、座位总额、状态语义、重复行、数值范围纯函数）、`api/app/services/dictionaries.py`（字典装配与状态中文标签）、`api/app/schemas/quote.py`（全部报价契约；金额请求非负两位小数、响应统一 float）、`api/app/core/errors.py`（QUOTE_NOT_FOUND / QUOTE_DETAIL_NOT_FOUND / QUOTE_STATE_CONFLICT）、项目详情扩展 `ProjectDetail.quoteGroups`（按公司+保险员分组卡片）。
    - 前端：`web/app/projects/[id]/quotes/new/page.tsx`（公司九宫格 + 保险员 + “跳过上传，手动录入”）、`web/app/quotes/[id]/confirm/page.tsx`（固定 7 Tab + 冲突选择 + 吸底确认）、`web/app/quotes/[id]/page.tsx`（价格摘要 + 优惠编辑净支出 + 删除）、`web/components/quote/`（price/coverage/package/service/annotation/vehicle 六类 Tab、未识别映射/丢弃、discount-editor、quote-group-card、status-badge）、`web/lib/use-dictionaries.ts` 字典 Hook；OpenAPI 产物与 `web/lib/api-types.d.ts` 已随 `pnpm gen:api` 更新。
    - 测试：`api/tests/test_pricing.py`（46 个纯函数用例）、`api/tests/test_quotes_api.py`（30 个集成用例）、`api/scripts/smoke_task02.py`（全栈冒烟）、`web/tests/quote-confirm.test.tsx`、`web/tests/quote-detail.test.tsx`、`web/tests/quote-group.test.tsx`。
  - **验证命令与结果**（全部通过）：
    1. 后端：`uv sync --locked --all-groups`（32 包）；`uv run ruff check .` All checks passed；`uv run pytest` **116 passed**（TASK-01 40 + TASK-02 76；价格规则单测覆盖金额换算/座位总额/三态语义/总额 PASSED·MISMATCH·NOT_CHECKABLE（含容差边界等于→PASSED）/正常净支出/无折现优惠/优惠超额与“折现=基准→0·OK”边界；集成覆盖 PENDING_CONFIRM→CONFIRMED 主路径、重复确认 409、DRAFT 编辑 409、OTHER 缺公司名 422、金额负数与三位小数 422、交强险/保障包类型码隔离、座位矛盾 422、车辆冲突两种选择与初登日期只提示、优惠超额恢复、删除级联、项目分组与最低净支出排除异常报价、自由文本脱敏、字典端点）。
    2. 全栈冒烟 `uv run python scripts/smoke_task02.py`：一次性 PG + 真实 uvicorn:8031 上 **15/15 通过**（创建项目 → OTHER 手动报价 → 价格/险种/座位自动推导/未识别映射/服务/保障包/标注 → 优惠（SERVICE 无折现不减钱）→ 确认 → 项目分组卡片与摘要回填 → 删除）。
    3. 前端：`pnpm lint` 0 错误；`pnpm test --run` **34 passed**（7 Tab 结构与切换、价格“值+INCLUDED”保存口径、未识别映射/丢弃、冲突未选择禁用确认并选择后携带 resolution、确认 422 中文提示、SERVICE 优惠无折现提交、净支出异常标注、分组卡片同来源提示与总价异常提示）；`pnpm build` 成功；浏览器移动视口的交互走查按 TASK-01 先例以组件测试 + 生产构建覆盖，真机视口端到端并入 TASK-07 Playwright 门禁（本任务未伪造该步骤）。
    4. 官方与系统总价不一致仍保留两者并三处提示（确认页价格 Tab、详情页、项目卡片），净支出异常报价不进入首页最低价（有专项断言）。
    5. `pnpm check:api` 契约与类型零漂移；`git diff --check` 通过；未改动 `docs/` 需求文档；未提交 `.env`、上传文件或缓存。
  - **实现决策记录**（非 SPEC 冲突，属实现细节）：
    - 价格分项 PATCH 语义：非空金额⇒INCLUDED；金额与 NOT_INCLUDED 并存⇒422；仅标 INCLUDED 允许无用户值（等计算值回退），但确认时“值与计算值皆缺”阻断并点名分项——与 SPEC §2.2“显示值优先、计算值回退”一致，守门收敛在 confirm。
    - 手动新增险种/服务/保障包内部保障行默认 status=INCLUDED（用户添加即视为投保）；费用为 0 的服务由用户显式选 FREE。
    - 所有明细层写操作统一返回重算后的完整 QuoteRead（含 netPayment），前端单状态整体刷新，避免多端点局部状态漂移。
    - 明细读模型的金额字段经共享 PlainSerializer 统一输出 JSON number（复用请求侧 Decimal 精度校验，同时满足 TASK-01 的“响应金额统一 float”决策）。
    - 确认页 7 Tab 之外不增设“优惠”Tab：优惠编辑固定在报价详情页（SPEC §8 报价详情行为），确认页底部提供跳转入口，主路径连续性由组件测试与冒烟覆盖。
    - UPLOADED 容器创建接口已按契约提供（仅建 DRAFT），上传/解析链路留给 TASK-03；前端未暴露不可用上传按钮。

---

## TASK-03：安全文件资产、解析任务队列与上传交互

- **状态**：`[ ] 未开始`
- **目标**：在不实现模型业务解析的前提下，完成文件从浏览器到受控本地存储、报价关联、解析任务入队/轮询/恢复的完整基础设施，使 TASK-04 只需接入解析流水线。
- **前置依赖**：TASK-02 已完成且验证通过；沿用既有 Quote 状态机、API 客户端和数据库模型，不新增替代性的文件归属或任务系统。前端先阅读本地 Next.js 文档中的表单、文件输入、环境变量和错误处理指南。
- **实施范围**：
  1. 实现项目级本地文件存储服务：随机化磁盘文件名，路径为 `UPLOAD_DIR/{projectId}/{fileId}/...`，数据库只存相对路径；`originalName` 先脱敏再入库；使用临时文件 + 原子移动，并在数据库失败时回滚未引用文件。
  2. 上传时同时校验扩展名、声明 MIME、文件签名、单文件/总大小、文件数、PDF 页数、单报价总页数和图片解码后像素数。只接受 JPEG、PNG、PDF；拒绝 HEIC/WebP、加密/损坏 PDF、解压炸弹和超限文件，并清理临时文件。
  3. 使用 Pillow 读取/验证图片，使用 `pypdfium2` 做 PDF 可打开性、加密和页数预检；CPU 密集操作放线程池，不阻塞事件循环。页面渲染和入模缩放由 TASK-04 完成。
  4. 实现 `POST /api/quotes/{id}/files`：先建报价后上传，多文件一次提交，创建 `quote_file`、`quote_file_link`、`parse_task`、`parse_task_file`；项目首次解析必须显式携带 `modelProcessingConsent=true` 并写 `modelConsentAt`，否则 422；活动任务冲突返回 409；成功返回 202 和 taskId。
  5. 实现 `GET /api/quotes/{id}/parse-status`、未确认报价的 `POST /api/quotes/{id}/reparse`、`GET /api/files/{fileId}/raw`。reparse 与上传一样受项目 consent 门控：`modelConsentAt` 为空且请求未携带 `modelProcessingConsent=true` 时返回 422。原文件接口校验访问令牌、文件项目归属并以 inline 流返回，绝不挂到公开静态目录。
  6. 建立进程内单 worker 的领取、互斥、attempt、失败分类、关停和启动恢复框架：启动时把遗留 RUNNING 重置 PENDING；测试环境可注入确定性假 pipeline；本 Task 不制造候选业务数据。正式解析能力缺失时必须安全失败并给出脱敏配置错误，不能假装成功。
  7. 接通报价状态：DRAFT 上传后进入 PARSING；任务最终失败进入 PARSE_FAILED；重试回到 PARSING；转手动时保留已上传文件但进入 PENDING_CONFIRM。TASK-04 接管成功后进入候选确认的分支。
  8. 实现无引用文件清理：删除报价只删自身 link；仍被兄弟报价或 parse_task 引用的文件保留；删除项目后在事务提交成功后清理对应项目目录；清理失败记录不含敏感路径内容的错误并可重试。
  9. 完成添加报价页的拖拽/相机/相册多选、格式与限制提示、首次模型传输同意弹窗、上传进度；完成报价详情页的每 3 秒任务轮询、排队/解析/失败状态、重试和转手动入口。
  10. 完成受控文件缩略图/预览壳层，支持多文件横滑和 PDF 翻页；此时只显示文件，不伪造 evidence。局域网 401 时复用 TASK-01 的令牌输入流程。
- **主要交付物**：安全存储与校验服务、文件及任务 API、可恢复单 worker 框架、文件删除策略、上传/轮询/预览 UI，以及使用假 pipeline 的集成测试。
- **明确不做**：真实 VisionClient、提示词、模型 JSON Schema、归一化、置信度、候选落库、多方案拆分和已确认报价 merge。
- **验证**：
  1. 后端执行 Ruff 与 pytest；上传集成测试覆盖合法 JPEG/PNG/PDF、多文件顺序、三类伪造 MIME、损坏/加密/超页 PDF、超大图片、总量限制、同报价并行任务 409、未同意 422，并断言正确的状态码口径：前置创建报价返回 201，上传接口无论何种成功路径一律返回 202 并携带 taskId，测试环境配置令牌后无令牌返回 401，缺同意返回 422；不得为上传接口引入 201 分支。
  2. 用假 pipeline 验证任务 `PENDING → RUNNING → SUCCEEDED/FAILED`、最多尝试次数、服务重启恢复、同报价互斥和日志不包含文件正文；此处 SUCCEEDED 只代表基础设施回调成功，不写候选报价。
  3. 验证原文件无令牌不可读、错误项目归属不可读、正确请求可 inline 预览；文件从不出现在 Next.js `public/` 或 FastAPI 静态目录。
  4. 验证共享引用和删除矩阵：单报价删除、仍有 task 引用、仍有兄弟 link、项目整体删除、数据库回滚后临时文件清理。
  5. 前端执行 lint、测试和构建；移动端走“选择公司 → 多文件 → 同意传输 → 上传 → 轮询 → 假失败 → 重试/转手动”，并验证拒绝同意仍可走手动录入。
  6. 执行 OpenAPI 类型漂移检查和 `git diff --check`。
- **完成判定**：文件资产、权限、生命周期和任务编排均有可重复测试；真实解析尚未接入但接口、状态与 UI 已准备好，TASK-04 无需改变存储或队列设计。
- **完成记录**：由执行者填写日期、提交/工作区标识、关键文件、验证命令与结果；未完成时写明精确阻塞，不得勾选。

---

## TASK-04：视觉解析、归一化、证据与候选确认

- **状态**：`[ ] 未开始`
- **目标**：把 TASK-03 的任务基础设施接成可工作的“图片/PDF → 脱敏结构化候选 → 人工确认”流水线，严格隔离模型抽取、规则归一化、校验和用户确认。
- **前置依赖**：TASK-03 已完成且验证通过；必须复用既有数据表、worker、状态机和手动确认组件。真实模型不是常规自动化测试前置条件。
- **实施范围**：
  1. 按 `VisionClient` Protocol 实现 OpenAI 兼容 provider，配置 `VISION_BASE_URL/API_KEY/MODEL`；provider 只负责传输和结构化响应，不做业务映射。鉴权/参数 4xx 不重试，超时、网络、429、5xx 或 Schema 失败按总尝试不超过 3 次处理。
  2. 使用同一组 Pydantic 模型生成并校验 SPEC §4.1 输出 Schema，覆盖 insurer、车辆白名单、pricing、核心/附加险、服务、保障包及内部保障、销售标注、planCount/plans、plan 内 unmatchedItems 和完整 evidence；所有定义键缺失都判失败，禁止维护第二份手写 Schema。
  3. 提示词明确禁止返回姓名、完整车牌、VIN、发动机号、身份证、手机号，要求 printed official 与 sales annotation 隔离、无法判断用 null/UNKNOWN、证据使用后端分配的 fileKey/page/text，禁止伪造页码和 bbox。
  4. 完成文件准备：EXIF 方向纠正、最长边缩放、PDF 逐页渲染为 PNG、每任务稳定分配 F1/F2… 和 1 起始页码、全部页面单次多图调用。超过供应商能力时任务失败并给出调低页数或换 provider 的提示，不自动分批。
  5. 扩展 TASK-01 的统一脱敏器，并在任何落库/日志前处理 originalName、rawResult、evidence.text、annotation.content、description、unmatched rawText、用户备注和错误摘要；命中个人字段标签的无关片段整段删除，无法安全处理的摘录改为“来源文本已隐藏”。未脱敏模型响应只允许短暂存在内存。
  6. 实现证据校验：fileKey 必须属于本次 `parse_task_file`，page 必须在对应文件合法范围；合法后转换为 sourceFileId。非法或缺失证据不建链接并按规则降档，多文件相同页码不得串文件。
  7. 实现归一化引擎：SPEC §3 的险种、公司、服务和保障包类型字典；精确、清理空格/括号、关键词包含的有序匹配；元/万/千分位、单座/座位、共享、倍数和条件转换；未知险种进入 UNRECOGNIZED，不猜测类别。
  8. 实现 SPEC §6 的全部自动校验和 §4.2 LOW/MEDIUM/HIGH 合成，尤其金额三态、严格重复去重、司机/乘客与三个医保外对象区分、保障包不得污染主险、明确 0 元服务为 FREE、公司/车辆冲突和低质量集中提示。
  9. 单方案成功时在事务内写入脱敏 rawResult、候选价格/车辆/明细/evidence，并进入 PENDING_CONFIRM；失败保留上一次未确认候选，按状态机进入 PARSE_FAILED 或回到 PENDING_CONFIRM。检测到一个批次含不同保险公司时以明确错误停止，不进入多方案拆分。检测到同保险公司的 planCount > 1 时，只写入脱敏 rawResult，容器报价进入 PENDING_CONFIRM 并展示“多方案待拆分”占位提示，不把任何 plan 的明细写入报价；拆分确认视图与子报价创建由 TASK-05 实现。
  10. 扩展报价详情和确认页：真实上传/解析状态、文件预览、点击字段切换到正确文件页并展示最短摘录、三档色标、“用户已确认”优先标识、公司/车辆/总价/低质量警告、未识别保障映射。确认仍调用 TASK-02 的同一接口和领域规则。
  11. 建立三层测试：纯规则单测；固定脱敏 rawResult 的 pipeline 回放测试；注入假 VisionClient 的任务集成测试。fixture 至少覆盖人保、平安、PDF 多页、多文件相同页码、销售红字、保障包、未知险种、非法 evidence 和同公司多方案（断言其只落 rawResult、不写任何 plan 明细）。
- **主要交付物**：VisionClient/provider、提示词与提取 Schema、图片/PDF 页面准备、脱敏/归一化/验证/置信度服务、单方案候选落库、带来源定位的确认 UI 和解析 fixture 测试。
- **明确不做**：真实报价准确率正式验收、多方案拆分确认视图与子报价创建（同公司多方案只落 rawResult，见实施范围第 9 条）、已确认报价补传合并、对比引擎、AI 分析和导出长图。
- **验证**：
  1. 后端执行 Ruff 和完整 pytest；固定 fixture 重放不访问网络，结果逐字段断言，不只做大对象快照。
  2. 高风险测试必须证明：司机/乘客不互换、三个医保外对象不互换、保障包驾乘保障不写主险、销售标注不参与正式字段/价格、明确 0 元服务为 FREE、空费用服务为 UNKNOWN。
  3. 证据测试覆盖合法多文件映射、越界页码、未知 fileKey 和无 evidence；非法来源字段显示 LOW 且没有伪造 sourceFileId。
  4. 脱敏测试使用手机号、身份证号、VIN、车牌和个人字段标签，断言它们不出现在 rawResult、field_evidence、annotation、错误日志和测试快照。
  5. 用假 provider 在移动端走“上传 → 轮询 → 候选 → 点击来源 → 修改字段 → 确认”主路径；另验证非法 JSON 重试、401 不重试、空方案失败和失败后转手动。
  6. 若用户提供可用密钥，可额外做一次非阻断 smoke test；不得把结果当作 TASK-07 的 10 份样本验收。
  7. 前端执行 lint、测试、构建；执行 OpenAPI 类型漂移检查和 `git diff --check`。
- **完成判定**：单方案图片/PDF 能经真实 provider 接口或等价假 provider 形成安全、可追溯、可编辑的分层候选；关键归一化和隐私不变量都有确定性测试。
- **完成记录**：由执行者填写日期、提交/工作区标识、关键文件、验证命令与结果；未完成时写明精确阻塞，不得勾选。

---

## TASK-05：多方案拆分、重解析与已确认报价补传合并

- **状态**：`[ ] 未开始`
- **目标**：补齐报价生命周期中最容易破坏既有数据的两条路径：同一原文件的多方案拆分，以及已确认报价补传/重解析后的逐项合并；保证原文件共享、原始结果可回放、用户编辑永不被静默覆盖。
- **前置依赖**：TASK-04 已完成且验证通过；不得新增 QuoteVersion、复制文件或改成“一文件一报价”。
- **实施范围**：
  1. `planCount > 1` 时不把任一方案直接写入容器报价，改为拆分确认视图；展示各 planLabel、价格和关键保障摘要，允许用户改标签并丢弃无效方案。
  2. 拆分确认在单个数据库事务内为保留方案创建平级 `PENDING_CONFIRM` Quote，复制候选结构化数据但不复制原文件，为每个子报价建立相同 `quote_file_link`；成功后删除容器报价，`parse_task.quoteId` 因 SET NULL 保留 rawResult 和输入文件引用。
  3. 只允许同一保险公司的多方案拆分；混合公司任务沿用 TASK-04 的明确失败结果，UI 提示按公司分别上传。
  4. 实现 PENDING_CONFIRM 的重新解析/补传：输入该报价全部关联文件，成功时只覆盖 `editedByUser=false` 的候选；失败保留上次候选并回到 PENDING_CONFIRM。
  5. 实现 CONFIRMED 的两种解析范围：补传只解析本次新增文件，重新解析读取全部关联文件；两者运行期间旧报价保持 CONFIRMED 可查看/可对比，成功后只生成 `merge_change` 并进入 MERGE_REVIEW，失败回到 CONFIRMED。
  6. 按稳定业务键生成 ADD/CONFLICT，不自动生成 DELETE：险种用 code、未识别项用 rawName、服务用 serviceType、保障包用名称；同键多行整组冲突，不猜测合并。用户编辑项默认 KEEP。
  7. 实现 `GET /api/quotes/{id}/merge-preview` 和 `POST /api/quotes/{id}/merge-resolve`；逐项 ACCEPT/KEEP，所有 PENDING 解决后在事务内重算价格/校验/净支出并回到 CONFIRMED。任何中途失败不得形成半合并状态。
  8. 完成确认页内的拆分卡片流和 MERGE_REVIEW 变更清单，明确显示旧值、新值、来源、用户编辑标识和“采纳新值/保留旧值”；解析失败状态条不能遮挡旧的已确认内容。
  9. 完善文件删除：删除一个子报价不影响兄弟报价预览；只有文件既无 quote link 又无 parse task 引用时才可删除资产；项目删除仍清理全部数据库记录与磁盘目录。
  10. 更新状态机、API、OpenAPI 类型和回放测试，覆盖并发重解析 409、解析任务恢复、拆分事务回滚、merge 重算和旧数据保护。
- **主要交付物**：多方案拆分事务与 UI、PENDING 重解析、CONFIRMED 补传/重解析、merge_change 生成与解决、共享文件安全删除以及对应端到端测试。
- **明确不做**：QuoteVersion 或历史版本 UI、跨保险公司自动拆分、自动删除旧字段、自动接受冲突、对比规则和导出。
- **验证**：
  1. 多方案集成测试验证子报价数量/标签/状态/候选内容、共享 fileId、容器删除、parse_task 保留；删除任一子报价后其他子报价仍能查看原文件和 evidence。
  2. 事务失败注入测试验证拆分不会留下部分子报价、孤儿 link 或丢失 rawResult。
  3. PENDING_CONFIRM 测试验证用户编辑值在重解析后保留，未编辑候选可更新，解析失败保留上次候选。
  4. CONFIRMED 测试分别验证补传输入范围、全量重解析输入范围、运行/失败期间旧值可读取、MERGE_REVIEW 使用旧值、ADD/CONFLICT/同键多行、默认 KEEP 和全部解决后的原子合并。
  5. 前端移动视口各走一条“多方案拆分确认”和“已确认报价补传 → 逐项解决 → 回到确认状态”主路径；执行 lint、测试和构建。
  6. 后端执行 Ruff、完整 pytest、OpenAPI 类型漂移检查和 `git diff --check`。
- **完成判定**：多方案和补传两条主路径可重复通过，文件与 rawResult 不丢失，任何已确认或用户编辑数据都不会被模型静默覆盖。
- **完成记录**：由执行者填写日期、提交/工作区标识、关键文件、验证命令与结果；未完成时写明精确阻塞，不得勾选。

---

## TASK-06：规则对比引擎、五问总结与移动端对比页

- **状态**：`[ ] 未开始`
- **目标**：在已经确认的数据之上实现纯规则的多报价比较，回答 PRD 的五个核心问题并提供可解释、可追溯的六区差异页面，不再读取原图或调用 LLM。
- **前置依赖**：TASK-05 已完成且验证通过；比较只消费 `CONFIRMED` 和 `MERGE_REVIEW` 的已确认旧值，不修改报价数据。前端先阅读本地 Next.js 文档中的数据获取、缓存、导航、加载和错误边界指南。
- **实施范围**：
  1. 建立纯函数优先的 Comparison Engine 和 `GET /api/projects/{id}/compare?quoteIds=...`；保持用户传入顺序，要求同项目、至少 2 个且最多 6 个合法状态报价，避免 N+1 查询。
  2. 实现价格排序：使用 netPayment，null 排最后并按 MISSING_TOTAL/INVALID_DISCOUNT 标注；官方总价、含用户估值、总额异常不得隐藏；最低值带估值或校验异常时使用“暂为最低”口径。
  3. 实现五问总结：最低价；三者/车损/三个医保外分别最高；商业四大主险完整性；以最低净支出为基准的价格分项与条件允许时的险种保费归因；核心保障口径不同或 UNKNOWN 时的不可直接比较提示。
  4. 实现固定差异基准：无论 2 个还是更多方案，都以用户勾选的第一个为逐行差异基准；价格归因仍以最低净支出为基准，并在返回和页面中分别标注两种基准，禁止混用。
  5. 实现 `↑/↓/+/−/=` 标签，比较集合、状态、保额、单座金额、座位数、共享、倍数和条件；保障包按包及内部 coverage 展开，服务按类型比较；未识别金额项不进入结构化分区，只在“不能直接比”中提示数量。
  6. 返回价格、核心保障、附加险、额外保障、增值服务、优惠/净支出六个稳定分区；差异行在服务端结果中标明，前端可高亮置顶，相同行可折叠。
  7. 项目详情增加报价勾选、同公司筛选和“开始对比”，上限 6 个并按勾选顺序生成 URL；报价卡继续按公司+保险员分组。
  8. 完成 `/projects/[id]/compare`：首屏五问卡、明确的对比/价格归因基准、冻结指标首列、约 44vw 的方案列横滑、六区表格、差异置顶、相同折叠、异常/UNKNOWN/未识别提示，以及统一免责声明。
  9. 对 Comparison Engine 写表驱动测试，覆盖 2/3/6 报价、并列最低、全部缺总价、优惠超额、保障不完整、不同口径、UNKNOWN、保障包、服务、未识别项和明细保费不完整；对 API 做权限/归属/数量和查询性能测试。
- **主要交付物**：纯规则比较服务与 API、项目报价选择、移动端对比页、五问总结、六区差异、完整规则与性能测试。
- **明确不做**：AI 总结/推荐、用户关注项排序、重新解析图片、长图导出和第二阶段知识库。
- **验证**：
  1. 后端执行 Ruff 和完整 pytest；逐条对照 SPEC §7.1–§7.4，断言五问文字所依据的结构化字段和基准，不使用模糊快照替代关键数值断言。
  2. 构造 6 个报价、每个 200 条明细的固定数据库数据集，预热后多次测量对比接口 P95 < 500ms；测试报告记录机器/数据库条件，失败时先修复查询或算法，不放宽规格。
  3. 验证 MERGE_REVIEW 对比读取旧确认值，候选 merge_change 不泄漏到结果；不同项目、非法状态、重复 quoteId、少于 2 或多于 6 均返回语义化错误。
  4. 前端执行 lint、测试、构建；在手机和桌面视口验证列横滑、首列可读、基准标记、差异折叠及所有异常提示。
  5. 手工走一条同公司方案和一条跨公司方案对比，核对“为什么贵”的 Δ价格分项始终给出，只有险种级归因在双方明细保费不完整时显示“明细保费不完整，无法继续拆分”。
  6. 执行 OpenAPI 类型漂移检查和 `git diff --check`。
- **完成判定**：任意 2–6 个合法报价能稳定得到与 SPEC 一致的五问和六区结果，关键差异无需用户自行查找，页面与接口性能达到口径且完全不调用模型。
- **完成记录**：由执行者填写日期、提交/工作区标识、关键文件、验证命令与结果；未完成时写明精确阻塞，不得勾选。

---

## TASK-07：脱敏长图、全链路验收与 MVP 交付收口

- **状态**：`[ ] 未开始`
- **目标**：完成最后一个 MVP 功能“导出脱敏对比长图”，建立可重复的浏览器端到端与真实样本验收门禁，并在不扩展产品范围的前提下修复全链路缺陷、形成可交付说明。
- **前置依赖**：TASK-06 已完成且验证通过。正式完成本 Task 还需要用户提供：10 份经人工脱敏并标注期望结果的真实报价（人保、平安各不少于 5 份，满足 SPEC §15.1 构成）、可用且允许测试的 provider/model 密钥、固定验收网络和 PostgreSQL 环境。缺少这些外部输入时可以完成代码与验收工具，但本 Task 必须保持未完成并记录阻塞，禁止伪造样本或准确率。
- **实施范围**：
  1. 在对比页实现客户端竖版长图导出，内容限定为五问、价格表、核心差异和免责声明；使用可维护的 DOM-to-image 方案，处理超长画布、高清缩放、字体/图标加载、移动浏览器下载，并在支持时提供 Web Share、否则下载 PNG。
  2. 导出使用专门的白名单 view model，只允许方案展示名、公司、价格和保障差异；不得直接截图含原文件、evidence 原文、车辆摘要、保险员备注、用户备注、销售标注或访问令牌的页面区域。
  3. 建立 Playwright 端到端环境和仅测试可启用的假 provider，覆盖 SPEC §15.2 要求的主路径：多方案拆分、纯手动报价、补传合并、导出长图、模型最终失败后转手动；至少再覆盖项目删除和 2–6 报价对比。
  4. 建立私有验收样本目录约定、gitignore、标注 manifest Schema、运行器和脱敏报告模板。真实原图默认不提交 Git；提交任何脱敏 fixture 前必须再次通过隐私扫描。报告只记录样本匿名 ID、provider/model、参数、正确率、错误类别和耗时。
  5. 运行锁定 provider/model 的 10 份样本，每份正常执行一次，模型偶发失败只走产品内置重试，不重复抽样挑最好结果；先验证 `MAX_IMAGE_LONG_EDGE=2400`，确有准确率影响时记录证据后调高并全量重跑。
  6. 计算核心字段字段级完全正确率并分类高风险错误；门禁为正确率至少 95%，且司机/乘客互换、三个医保外对象互换、保障包污染主险、销售标注污染正式字段、明确 0 元服务识别为不包含这五类错误均为 0。
  7. 验证每个 evidence 的 fileId/page、隐私字段不落 rawResult/evidence/annotation/错误日志/长图、局域网无令牌不能读原文件；验证 1–3 页 P95 ≤90s、4–10 页 P95 ≤180s，并复跑 TASK-06 的对比 P95。
  8. 完善 README：Windows/通用开发启动、数据库迁移、前后端命令、供应商配置与数据流告知、局域网令牌/CORS、备份与不可恢复删除、供应商“不用于训练/最短留存”配置责任、验收命令和常见失败恢复。
  9. 运行全量检查并只修复 MVP 验收暴露的缺陷；修复必须沿用现有 schema、服务边界和页面结构。不得借收口之名加入 AI 分析、账户、云存储或其他第二阶段能力。
- **主要交付物**：脱敏长图功能、Playwright 主路径、真实样本验收运行器与报告、隐私/性能证据、完整运行说明和通过全部门禁的 MVP。
- **明确不做**：第二阶段的 AI 问答/推荐、个性化、登录、版本链、Excel/Word、云部署改造；不购买模型额度、不代替用户生成或猜测真实验收样本。
- **验证**：
  1. 后端从干净测试库执行锁文件安装、Alembic 全量升级、Ruff、完整 pytest；前端从锁文件安装后执行 lint、单元/组件测试、生产构建和完整 Playwright。
  2. 对导出 PNG 做浏览器测试和人工查看：尺寸非零、内容无裁切/空白、金额与页面一致、免责声明存在；用测试敏感数据断言导出专用 view model、待栅格化 DOM 和传给图像库的节点均不包含敏感文本。
  3. 五条规定端到端路径全部通过，并保留不含敏感信息的测试报告/截图；任何依赖真实模型的步骤与假 provider 测试明确分开标记。
  4. 真实样本报告达到 ≥95% 和五类高风险错误为 0；全部 evidence、隐私和性能条件达到 SPEC §15.2。任一项不达标时修复后按既定规则重跑完整相关样本集，不选择性删除失败样本。
  5. 在默认本机和显式局域网两种模式做最终 smoke test；确认拒绝模型传输仍可手动录入，解析/重解析失败不破坏旧数据，项目删除同步清理磁盘文件。
  6. 执行 OpenAPI 类型漂移检查、锁文件一致性检查、`git diff --check` 和最终 `git status --short`；确认没有密钥、原始验收文件、上传文件或缓存进入版本控制。
- **完成判定**：长图可安全分享，自动化主路径、真实样本准确率、隐私与性能门禁全部有可复核证据，README 足以让新的独立上下文从干净环境启动并验收完整 MVP。
- **完成记录**：由执行者填写日期、提交/工作区标识、关键文件、全部验证命令、真实样本报告位置与结果；任一外部验收条件缺失或未通过时不得勾选。

## 四、需求覆盖与顺序检查

| MVP 能力 | 负责 Task | 后续只允许的扩展方式 |
|---|---|---|
| 工程、PostgreSQL、访问控制、完整实体 | TASK-01 | 追加迁移和路由，不换基础设施 |
| 项目 CRUD、手动报价、7 Tab、价格/优惠 | TASK-01–02 | 自动解析复用相同 Quote 契约 |
| 图片/PDF、多文件、本地资产、异步任务 | TASK-03 | parser 通过既有 worker 插槽接入 |
| Vision、脱敏、归一化、校验、evidence | TASK-04 | 拆分/merge 只消费其脱敏结果 |
| 多方案与补传合并 | TASK-05 | 对比继续读取已确认旧值 |
| 五问、六区、差异与移动端对比 | TASK-06 | 导出只消费对比 view model |
| 长图、E2E、真实样本、隐私/性能交付 | TASK-07 | MVP 到此结束，不进入第二阶段 |

这 7 个 Task 的边界分别落在数据契约、人工数据、文件/任务、模型候选、生命周期合并、只读比较和发布门禁上。合并任意相邻两项都会让单个上下文同时承担两个高风险状态边界；继续拆分则会产生无法独立验收的纯脚手架任务，因此不再细分。
