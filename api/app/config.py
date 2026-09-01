"""应用配置：统一从环境变量 / .env 读取，并在创建应用前完成安全校验。

隐私边界（SPEC §9）：
- APP_BIND_HOST 默认 127.0.0.1，仅本机访问；
- 改为非回环地址且未配置 LOCAL_ACCESS_TOKEN 时，Settings.validate_security
  直接抛出异常使应用拒绝启动，避免把包含个人信息的原文件无保护地暴露到局域网；
- LOCAL_ACCESS_TOKEN 非空即启用令牌校验，与绑定地址无关。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根目录 = api/app/config.py 向上三级
REPO_ROOT = Path(__file__).resolve().parents[2]

# 回环绑定地址集合：这些地址下允许不配置访问令牌
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # 同时支持仓库根目录与 api/ 下的 .env；显式环境变量优先级更高
        env_file=(REPO_ROOT / ".env", Path(__file__).resolve().parents[1] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 数据库 ----
    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/car_insurance"

    # ---- 文件存储 ----
    upload_dir: str = "./api/uploads"

    # ---- 视觉模型供应商（解析阶段 TASK-03/04 使用）----
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = "glm-4.5v"
    # 思考模式开关（随请求体 thinking 参数下发）：默认 "disabled"——结构化抽取
    # 不需要长推理，长思考链会显著增加耗时与 JSON 格式失稳风险。
    # 仅智谱系模型支持该参数；其他 OpenAI 兼容端点若报 400 未知参数，请置空
    # （空=不下发，走模型默认；"enabled" 显式开启）
    vision_thinking: str = "disabled"
    # 仅测试可启用（TASK-07）：设置后解析走固定 fixture 假模型，不访问网络。
    # 正式部署绝不配置；E2E 运行器通过环境变量注入，优先级高于 VISION_*
    vision_fixture_dir: str = ""

    # ---- 上传限制 ----
    max_file_size_mb: int = 20
    max_total_upload_mb: int = 60
    max_files_per_quote: int = 12
    max_pdf_pages: int = 10
    max_total_pages_per_quote: int = 12
    max_image_pixels: int = 40_000_000
    max_image_long_edge: int = 2400

    # ---- 绑定与访问控制 ----
    app_bind_host: str = "127.0.0.1"
    local_access_token: str = ""
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ---- 业务容差 ----
    total_check_tolerance: float = 0.50

    @field_validator("database_url")
    @classmethod
    def _check_database_driver(cls, value: str) -> str:
        # MVP 明确使用 PostgreSQL；提前拦截误配的 sqlite/mysql 连接串
        if not value.startswith(("postgresql+asyncpg://", "postgresql://")):
            raise ValueError("DATABASE_URL 必须是 PostgreSQL 连接串（推荐 postgresql+asyncpg://）")
        return value

    @model_validator(mode="after")
    def validate_security(self) -> Settings:
        # 安全不变量：绑定到非回环地址却没有访问令牌 -> 拒绝启动。
        # 判断只看主机名部分（如 uvicorn 常见的 0.0.0.0 / 局域网 IP）。
        host = self.app_bind_host.strip().lower()
        if host not in _LOOPBACK_HOSTS and not self.local_access_token.strip():
            raise ValueError(
                "APP_BIND_HOST 绑定到非回环地址时必须配置 LOCAL_ACCESS_TOKEN，"
                "否则包含个人信息的原文件与 API 将无保护暴露到局域网。"
            )
        return self

    @property
    def token_required(self) -> bool:
        """LOCAL_ACCESS_TOKEN 非空即启用令牌校验（与绑定地址无关）。"""
        return bool(self.local_access_token.strip())

    @property
    def cors_origins(self) -> list[str]:
        # 逗号分隔配置，去空白；CORS 只是浏览器放宽策略，本身不作为访问控制
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def upload_path(self) -> Path:
        """上传目录绝对路径：相对路径一律相对仓库根目录解析。"""
        path = Path(self.upload_dir)
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()


@lru_cache
def get_settings() -> Settings:
    """进程内单例配置；测试通过清除缓存或直接构造 Settings 注入不同环境。"""
    return Settings()
