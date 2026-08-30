"""上传文件预检服务（SPEC §9.5、§12；TASK-03 范围 2-3）。

校验矩阵（全部通过才允许落盘建库）：
- 扩展名、浏览器声明 MIME、文件真实签名三者必须指向同一支持格式，
  只接受 JPEG / PNG / PDF（HEIC/WebP 等一律明确报错并提示转存）；
- 单文件大小、单次总大小、文件数、PDF 单文件页数、单次总页数、
  图片声明像素数（解压炸弹防护）全部受 .env 上限约束；
- PDF 经 pypdfium2 预检“可打开、未加密、页数合法”，损坏/加密即刻拒绝；
- 图片经 Pillow 确认真实格式并校验头部声明尺寸，防止伪装与解压炸弹。

隐私与性能边界：
- 校验只读取字节与头部信息，不做页面渲染（渲染与入模缩放是 TASK-04）；
- pypdfium2 / Pillow 属 CPU 密集操作，一律经 asyncio.to_thread 在线程池
  执行，不阻塞 API 事件循环（SPEC §13）；
- 原始文件名只在内存中短暂存在，通过本服务后只剩脱敏展示名。
"""

from __future__ import annotations

import asyncio
import dataclasses
import io

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError
from pypdfium2 import PdfDocument, PdfiumError

from app.config import Settings
from app.core.errors import ValidationError
from app.core.privacy import sanitize_file_name

# 扩展名 -> 声明 MIME 的唯一合法映射；扩展名归一化到小写
_SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
}

# 声明 MIME -> 文件真实签名的唯一合法映射（与上表互为镜像）
_SIGNATURES: dict[str, bytes] = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "application/pdf": b"%PDF-",
}

# 对外可读的格式名（错误提示用）
_FORMAT_LABELS = {"image/jpeg": "JPEG 图片", "image/png": "PNG 图片", "application/pdf": "PDF 文档"}


@dataclasses.dataclass(slots=True)
class InspectedFile:
    """单个文件通过全部预检后的产物。

    data 保留在内存中供落盘使用；display_name 已脱敏，
    original_name 不进入任何持久化存储。
    """

    data: bytes
    display_name: str
    mime: str
    page_count: int
    size_bytes: int


def _reject(message: str) -> ValidationError:
    """统一 422 语义的上传拒绝错误。"""
    return ValidationError(message=message, code="FILE_REJECTED")


async def inspect_uploads(files: list[UploadFile], settings: Settings) -> list[InspectedFile]:
    """按提交顺序校验一批上传文件（多文件一次提交的整体入口）。

    任何一项整体限制（数量/总大小/总页数）或单文件校验失败都会抛 422，
    调用方据此放弃整批并清理已产生的临时数据。
    """
    if not files:
        raise _reject("请至少选择一个文件")
    if len(files) > settings.max_files_per_quote:
        raise _reject(f"单份报价最多上传 {settings.max_files_per_quote} 个文件")

    inspected: list[InspectedFile] = []
    total_bytes = 0
    total_pages = 0
    for index, upload in enumerate(files, start=1):
        # 逐个串行校验：校验本身在线程池执行，串行可让失败尽早暴露，
        # 避免超限批次把全部文件都读入内存
        item = await _inspect_single(upload, index, settings)
        total_bytes += item.size_bytes
        total_pages += item.page_count
        if total_bytes > settings.max_total_upload_mb * 1024 * 1024:
            raise _reject(f"单次上传总大小不能超过 {settings.max_total_upload_mb}MB")
        if total_pages > settings.max_total_pages_per_quote:
            raise _reject(
                f"单份报价总页数不能超过 {settings.max_total_pages_per_quote} 页"
                "（图片按 1 页计）"
            )
        inspected.append(item)
    return inspected


async def _inspect_single(
    upload: UploadFile, index: int, settings: Settings
) -> InspectedFile:
    """校验单个文件：类型三重一致 -> 大小 -> 页数/像素。"""
    original_name = upload.filename or ""
    # 扩展名与声明 MIME 是客户端输入，先做白名单交叉校验
    dot = original_name.rfind(".")
    extension = original_name[dot:].lower() if dot >= 0 else ""
    declared_mime = (upload.content_type or "").split(";")[0].strip().lower()

    expected_mime = _SUPPORTED_EXTENSIONS.get(extension)
    if expected_mime is None:
        raise _reject(
            f"第 {index} 个文件（{sanitize_file_name(original_name)}）不是支持的格式，"
            "请上传 JPEG、PNG 图片或 PDF 文档"
        )
    if declared_mime and declared_mime != expected_mime:
        # 浏览器未提供 MIME 时跳过该层；提供了就必须一致（伪造 MIME 场景）
        raise _reject(
            f"第 {index} 个文件的实际格式与声明类型不一致，"
            f"请上传真实的{_FORMAT_LABELS[expected_mime]}"
        )

    # 大小预检：Starlette 已在 multipart 解析时记录真实字节数
    size_bytes = upload.size if upload.size is not None else 0
    if size_bytes > settings.max_file_size_mb * 1024 * 1024:
        raise _reject(f"第 {index} 个文件超过单文件 {settings.max_file_size_mb}MB 限制")
    if size_bytes == 0:
        raise _reject(f"第 {index} 个文件是空文件")

    data = await upload.read()
    # 以实际字节复核（防 content-length 欺骗）
    if len(data) != size_bytes:
        raise _reject(f"第 {index} 个文件内容不完整，请重新选择后上传")

    signature = _SIGNATURES[expected_mime]
    if not data.startswith(signature):
        raise _reject(
            f"第 {index} 个文件的文件内容不是真实的{_FORMAT_LABELS[expected_mime]}，已拒绝"
        )

    if expected_mime == "application/pdf":
        page_count = await asyncio.to_thread(_inspect_pdf, data, index, settings)
    else:
        page_count = await asyncio.to_thread(_inspect_image, data, index, settings)

    return InspectedFile(
        data=data,
        # 展示名入库前脱敏；带序号避免同批重名展示混淆
        display_name=sanitize_file_name(original_name, fallback_stem=f"报价单{index}"),
        mime=expected_mime,
        page_count=page_count,
        size_bytes=size_bytes,
    )


def _inspect_pdf(data: bytes, index: int, settings: Settings) -> int:
    """PDF 预检：可打开、未加密、页数不超限（CPU 密集，线程池执行）。"""
    try:
        document = PdfDocument(data)
    except PdfiumError:
        # pdfium 不区分“已加密”与“已损坏”的具体原因，统一拒绝，
        # 错误文案不回显文件内部细节（SPEC §12：上传时即报错拦截）
        raise _reject(
            f"第 {index} 个 PDF 文档已损坏或已加密，无法解析；"
            "请提供未加密的 PDF 或改拍照片"
        ) from None
    try:
        page_count = len(document)
    finally:
        document.close()
    if page_count > settings.max_pdf_pages:
        raise _reject(f"第 {index} 个 PDF 超过 {settings.max_pdf_pages} 页限制")
    return page_count


def _inspect_image(data: bytes, index: int, settings: Settings) -> int:
    """图片预检：真实格式、可解码、声明像素不超限（CPU 密集，线程池执行）。"""
    try:
        image = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError):
        raise _reject(f"第 {index} 个图片无法解码，请重新拍摄或转存为 JPEG/PNG") from None
    try:
        # format 来自真实文件头而非扩展名，伪造格式在此暴露
        image_format = image.format
        width, height = image.size
        # verify 只校验文件完整性不渲染像素；之后 image 不可再用（按需重开）
        image.verify()
    except (OSError, ValueError):
        raise _reject(f"第 {index} 个图片文件已损坏，无法解析") from None
    if image_format not in {"JPEG", "PNG"}:
        raise _reject(
            f"第 {index} 个图片是 {image_format or '未知'} 格式，"
            "请转存为 JPEG 或 PNG 后再上传"
        )
    if width * height > settings.max_image_pixels:
        raise _reject(
            f"第 {index} 个图片分辨率过高（超过 {settings.max_image_pixels} 像素），"
            "请缩小后上传"
        )
    # 图片固定按 1 页参与总页数约束
    return 1
