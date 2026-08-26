# api — 车险报价对比助手后端

FastAPI + SQLAlchemy 2 (async) + Alembic + PostgreSQL。目录约定见 `docs/SPEC_MVP.md` §11。

```text
app/
  main.py            # ASGI 入口：CORS、访问令牌中间件、统一异常处理、路由注册
  config.py          # pydantic-settings 环境配置（含启动期安全校验）
  db.py              # async engine / session 工厂
  core/              # 统一响应包、错误码、脱敏服务、安全日志
  models/            # ORM 实体（SPEC §2 全部 15 张表）
  schemas/           # Pydantic 请求/响应模型（对外 JSON 一律 camelCase）
  api/routes/        # 路由（projects 等）
  services/          # 领域服务（文件清理接口、后续解析/对比引擎的落点）
alembic/             # 数据库迁移（首个迁移冻结 MVP 全量数据模型）
tests/               # pytest：迁移不变量、API 集成、脱敏与配置行为
scripts/             # OpenAPI 导出等工具脚本
```

## 启动

```bash
uv sync                       # 安装依赖（含开发组）
uv run alembic upgrade head   # 数据库迁移（需 .env 中 DATABASE_URL 可用）
uv run uvicorn app.main:app --reload
```

交互文档：http://127.0.0.1:8000/docs （配置 LOCAL_ACCESS_TOKEN 后需携带 `X-Access-Token`）。

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
