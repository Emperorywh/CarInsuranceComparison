# api — 车险报价对比助手后端

FastAPI + SQLAlchemy 2 (async) + Alembic + PostgreSQL。目录约定见 `docs/SPEC_MVP.md` §11。

```text
app/
  main.py            # ASGI 入口：CORS、访问令牌中间件、统一异常处理、路由注册、
                     #   解析任务启动恢复与进程内单 worker 启停
  config.py          # pydantic-settings 环境配置（含启动期安全校验、上传限制）
  db.py              # async engine / session 工厂
  core/              # 统一响应包、错误码、脱敏服务、安全日志
  models/            # ORM 实体（SPEC §2 全部 15 张表）
  schemas/           # Pydantic 请求/响应模型（对外 JSON 一律 camelCase）
  api/routes/        # 路由（projects / quotes / files / dictionaries）
  services/          # 领域服务（报价与各层明细、确定性价格规则、字典装配、
                     #   normalization 标准码表与数值规则、文件存储与上传预检
                     #   storage/、解析任务编排 parse_service、解析 worker parser/、
                     #   文件清理接口）
alembic/             # 数据库迁移（首个迁移冻结 MVP 全量数据模型）
tests/               # pytest：迁移不变量、API 集成、价格规则单测、脱敏与配置行为、
                     #   上传矩阵/解析任务/原文件安全/删除矩阵
scripts/             # OpenAPI 导出、启动验证、TASK-02/03 全栈冒烟
```

## 启动

```bash
uv sync                       # 安装依赖（含开发组）
uv run alembic upgrade head   # 数据库迁移（需 .env 中 DATABASE_URL 可用）
uv run uvicorn app.main:app --reload --port 8877
```

交互文档：http://127.0.0.1:8877/docs （配置 LOCAL_ACCESS_TOKEN 后需携带 `X-Access-Token`）。

## 文件上传与解析任务（TASK-03）

- 上传只接受 JPEG / PNG / PDF；扩展名、声明 MIME、文件签名三者一致，且受
  单文件/总大小、文件数、PDF 页数、单报价总页数、图片像素上限约束（`.env`）。
- 原文件按 `{UPLOAD_DIR}/{projectId}/{fileId}/{随机名}` 落盘，数据库只存相对
  路径与脱敏后的展示名；原文件仅能经 `GET /api/files/{id}/raw?projectId=`
  受控读取（校验访问令牌与项目归属，inline 流），绝不挂静态目录。
- 状态码口径：创建报价容器 201；上传/重解析成功一律 202 + taskId；同一报价
  已有活动解析任务 409；项目首次解析缺 `modelProcessingConsent=true` 返回 422。
- 进程内单 worker 串行消费任务（无 Redis/Celery）：启动时把遗留 RUNNING 重置
  PENDING；attempt 最大 3；未配置 `VISION_*` 时任务安全失败（脱敏错误提示），
  报价进入 PARSE_FAILED，可重试解析或转手动录入。
- 删除报价只清理“无任何引用”的文件资产；仍被兄弟报价或解析任务引用的文件
  保留。删除项目在事务提交后清理整个项目上传目录（幂等可重试）。

## 视觉解析配置（TASK-04）

`.env` 中配置 OpenAI 兼容端点即可启用真实解析（三项任一为空则解析任务
安全失败并提示，绝不妨碍手动录入路径）：

```text
VISION_BASE_URL=...        # 兼容端点（智谱 GLM / DashScope 兼容模式 / OpenAI 中转）
VISION_API_KEY=...
VISION_MODEL=glm-4.5v
VISION_THINKING=disabled   # 思考模式开关；非智谱端点若报 400 未知参数请置空
MAX_TOTAL_PAGES_PER_QUOTE=12   # 单次多图调用上限；超过供应商能力时任务失败并提示调低
```

解析流水线：页面准备（EXIF 纠正/长边缩放/PDF 逐页渲染 PNG）→ 单次多图调用 →
§4.1 Schema 校验 → 白名单脱敏（rawResult 整树 + 全部自由文本）→ 证据校验
（fileKey/page 非法即降 LOW 且不建链）→ 归一化 → 校验/置信度 → 候选落库。
同公司多方案只落 rawResult 并展示“多方案待拆分”占位；混合公司批次直接失败。

开发验证（均不消耗模型额度，除非显式运行 live smoke）：

```bash
uv run python scripts/smoke_task04.py        # 全栈冒烟（假 provider，18 项）
uv run python scripts/smoke_vision_live.py   # 可选：真实密钥连通性 smoke（非阻断）
```

## 测试与检查

```bash
uv run pytest          # 自动启动一次性 PostgreSQL（pgserver），从空库升级到 head 后执行
uv run ruff check .    # 仅检查，不自动改写
```

测试不依赖开发者现有数据库，也不访问网络；如需指向外部测试库，可设置 `TEST_DATABASE_URL`。

## 安全约定（务必阅读）

- 默认只监听 `127.0.0.1`；`APP_BIND_HOST` 改为非回环地址且未配置 `LOCAL_ACCESS_TOKEN` 时，应用直接拒绝启动。
- `LOCAL_ACCESS_TOKEN` 非空即启用令牌校验（与绑定地址无关）：除 `/health` 外全部 API、原文件与交互文档均要求 `X-Access-Token`。
- 用户自由文本（备注、错误信息）在落库/写日志前统一经 `app.core.privacy` 脱敏，禁止各路由自行拼正则。
