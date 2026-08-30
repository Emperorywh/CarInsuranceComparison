"""OpenAI 兼容视觉 provider（SPEC §1.1；TASK-04 范围 1）。

只负责传输与结构化响应，不做业务映射：
- 端点：``{VISION_BASE_URL}/chat/completions``（已含路径则原样使用），
  覆盖智谱 GLM 视觉系列、阿里 DashScope 兼容端点及任何 OpenAI 兼容中转；
- 失败分类（SPEC §12）：鉴权/参数类 4xx（401/403/400/404/422/413）不重试；
  超时、网络错误、429、5xx、返回内容非 JSON 或 Schema 校验失败可重试；
  重试由 worker 按 attempt ≤ 3 统一控制，本层不自行重试；
- 隐私边界：错误信息只携带状态码与固定中文文案，绝不携带请求正文、
  原图 base64 或响应原文（可能含敏感信息）。
"""

from __future__ import annotations

import logging

import httpx

from app.services.parser.extraction_schema import parse_extraction
from app.services.parser.pipeline import ParseConfigError, ParseRetryableError
from app.services.parser.prompts import build_request_messages
from app.services.parser.vision_client import VisionInputPage

logger = logging.getLogger(__name__)

# 单次请求超时（秒）：连接 10s，读超时按 1–3 页 P95 ≤90s / 4–10 页 ≤180s（SPEC §13）
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 180.0


def _completions_url(base_url: str) -> str:
    """归一化 chat/completions 端点：允许配置到站点根、/v1 或完整路径。"""
    trimmed = base_url.strip().rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed
    return f"{trimmed}/chat/completions"


def _extract_json_text(content: str) -> str:
    """从模型回复中取出 JSON 文本：容忍 ```json 围栏与前后说明文字。"""
    text = content.strip()
    if text.startswith("```"):
        # 去掉首个围栏行与结尾围栏
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json object")
    return text[start : end + 1]


class OpenAICompatibleVisionClient:
    """OpenAI 兼容 chat/completions 视觉客户端。"""

    provider = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = _completions_url(base_url)
        self._api_key = api_key.strip()
        self.model = model.strip()
        # transport 供测试注入 MockTransport；生产恒为 None（默认连接池）
        self._transport = transport

    async def extractQuote(self, pages: list[VisionInputPage]):
        """发送全部页面并返回 Schema 校验通过的抽取结果。

        任何失败都以 ParseTaskFailure 子类抛出，由 worker 统一处理重试
        与终态；本方法不维护内部重试循环。
        """
        messages = build_request_messages(pages)
        payload = {
            "model": self.model,
            "messages": messages,
            # 结构化输出走提示词 + Schema 校验兜底；不传 response_format，
            # 兼容不支持该字段的 OpenAI 兼容端点（Schema 校验失败可重试）
            "temperature": 0.1,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
                transport=self._transport,
            ) as client:
                response = await client.post(self._url, json=payload, headers=headers)
        except TimeoutError:
            raise ParseRetryableError("视觉模型请求超时，已自动重试") from None
        except httpx.HTTPError:
            raise ParseRetryableError(
                "无法连接视觉模型服务，请检查 VISION_BASE_URL 配置与网络"
            ) from None

        if response.status_code >= 400:
            raise self._http_error(response.status_code)

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ParseRetryableError("视觉模型响应结构异常，已自动重试") from None
        if not isinstance(content, str) or not content.strip():
            raise ParseRetryableError("视觉模型返回内容为空，已自动重试")

        try:
            return parse_extraction(_extract_json_text(content))
        except ValueError:
            # JSON 本身不合法（_extract_json_text / json 解析失败）
            raise ParseRetryableError("模型返回内容不是合法 JSON，已自动重试") from None
        except Exception as exc:  # pydantic ValidationError：Schema 校验失败
            logger.info("模型输出 Schema 校验失败 type=%s", type(exc).__name__)
            raise ParseRetryableError(
                "模型输出不符合约定结构，已自动重试"
            ) from None

    def _http_error(self, status_code: int) -> ParseConfigError | ParseRetryableError:
        """HTTP 错误分类：鉴权/参数/载荷类 4xx 直接失败，其余可重试。"""
        if status_code in (401, 403):
            return ParseConfigError(
                f"视觉模型鉴权失败（HTTP {status_code}），请检查 VISION_API_KEY 配置"
            )
        if status_code == 413:
            # 载荷超限：供应商单请求能力不足，按边界要求提示调低页数或换供应商
            return ParseConfigError(
                "请求载荷超过视觉模型供应商上限，请调低 MAX_TOTAL_PAGES_PER_QUOTE "
                "或更换供应商后重试"
            )
        if status_code in (400, 404, 422):
            return ParseConfigError(
                f"视觉模型拒绝请求（HTTP {status_code}），请检查 VISION_BASE_URL "
                "与 VISION_MODEL 配置"
            )
        if status_code == 429:
            return ParseRetryableError("视觉模型限流（429），稍后自动重试")
        return ParseRetryableError(
            f"视觉模型服务异常（HTTP {status_code}），已自动重试"
        )
