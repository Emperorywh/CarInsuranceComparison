"""视觉模型供应商统一接口（SPEC §1.1；TASK-04 范围 1）。

职责边界：
- VisionClient 只负责“传输 + 结构化响应”：把页面交给供应商、取回 JSON、
  校验通过 §4.1 Schema 后原样返回；禁止在本层做任何业务归一化；
- 页面的 fileKey / page 由后端在任务领取时分配（parse_task_file 输入
  顺序），并同时写入提示词，用于把证据稳定映射回原文件；
- 失败分类与重试策略：鉴权/参数类 4xx 不重试（ParseConfigError）；
  超时、网络错误、429/5xx、JSON/Schema 校验失败可重试
  （ParseRetryableError），总尝试次数由 worker 按 attempt ≤ 3 控制，
  provider 内部不自行重试。
"""

from __future__ import annotations

from typing import Protocol, TypedDict

from app.services.parser.extraction_schema import ExtractionResult


class VisionInputPage(TypedDict):
    """发送给视觉模型的一页内容。

    fileKey 与 page 会同时写入提示词，用于把模型证据稳定映射回原文件；
    content 统一为已缩放的 PNG 字节（PDF 渲染产物或图片解码重编码）。
    """

    fileKey: str
    page: int
    content: bytes
    mimeType: str


class VisionClient(Protocol):
    """视觉模型供应商统一接口。

    各供应商适配器只负责传输和结构化输出，不在此层执行业务归一化。
    """

    async def extractQuote(self, pages: list[VisionInputPage]) -> ExtractionResult: ...


__all__ = ["VisionClient", "VisionInputPage", "ExtractionResult"]
