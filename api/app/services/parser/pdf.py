"""解析输入页面准备（SPEC §4 步骤 1-2；TASK-04 范围 4）。

职责：
- 图片：EXIF 方向纠正 → 等比缩放至最长边 ≤ MAX_IMAGE_LONG_EDGE → 重编码 PNG；
- PDF：pypdfium2 逐页渲染为 PNG，渲染比例按“最长边 ≈ 上限”计算；
- fileKey / page 沿用 worker 按 parse_task_file 输入顺序的分配结果
  （F1/F2…，页码 1 起），本模块不重新编号；
- 全部页面在单次多图调用中发给模型（SPEC §4 步骤 3）：总页数超过
  MAX_TOTAL_PAGES_PER_QUOTE 时任务失败并提示调低页数或换供应商，
  绝不自动分批（SPEC §12 边界）。

性能与隐私边界：
- Pillow / pypdfium2 属 CPU 密集操作，统一经 asyncio.to_thread 执行，
  不阻塞事件循环（SPEC §13）；
- 页面 PNG 字节只在内存中短暂存在，随请求发送后即释放，不落盘、不写日志。
"""

from __future__ import annotations

import asyncio
import io

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdfium2 import PdfDocument, PdfiumError

from app.config import Settings
from app.services.parser.pipeline import ParseConfigError, ParseInputError, ParseTaskFileInput
from app.services.parser.vision_client import VisionInputPage
from app.services.storage import local_files

# 渲染/缩放后的 PNG 统一 MIME
_OUTPUT_MIME = "image/png"


def _read_file_bytes(settings: Settings, relative_path: str) -> bytes:
    """读取落盘原文件（线程池执行）；路径经防穿越校验。"""
    absolute = local_files.resolve_absolute(settings, relative_path)
    return absolute.read_bytes()


def _scale_long_edge(image: Image.Image, long_edge_limit: int) -> Image.Image:
    """等比缩小到最长边不超过上限；小于上限的图片原样返回。"""
    width, height = image.size
    longest = max(width, height)
    if longest <= long_edge_limit:
        return image
    scale = long_edge_limit / longest
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(new_size, Image.LANCZOS)


def _prepare_image(data: bytes, long_edge_limit: int, label: str) -> list[Image.Image]:
    """解码单张图片：EXIF 方向纠正 + 缩放；失败按不可重试输入错误处理。"""
    try:
        image = Image.open(io.BytesIO(data))
        # exif_transpose 依据 EXIF 方向标记转正像素并清除该标记，
        # 手机直拍照片不纠正会导致模型看到旋转 90° 的内容
        image = ImageOps.exif_transpose(image)
        image = _scale_long_edge(image, long_edge_limit)
    except (UnidentifiedImageError, OSError, ValueError):
        # 上传预检已验过签名与可解码性；到达这里说明存储后文件损坏等
        # 确定性问题，重试不会有不同结果，按不可重试失败处理
        raise ParseInputError(
            f"{label}无法解码，请删除后重新上传该文件"
        ) from None
    if image.mode not in ("RGB", "L"):
        # PNG 透明通道/调色板统一转 RGB，避免部分供应商拒绝带 alpha 的图
        image = image.convert("RGB")
    return [image]


def _prepare_pdf(data: bytes, long_edge_limit: int, label: str) -> list[Image.Image]:
    """PDF 逐页渲染为 PIL 图片；页数以文档实际页数为准。"""
    try:
        document = PdfDocument(data)
    except PdfiumError:
        raise ParseInputError(
            f"{label}无法打开，可能已损坏或加密，请重新上传"
        ) from None
    images: list[Image.Image] = []
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                # 页面尺寸单位为点（1/72 英寸）；按最长边上限换算渲染比例，
                # 同时限制放大倍数 ≤2，避免低分辨率 PDF 渲染出超大位图
                width, height = page.get_size()
                longest_points = max(width, height)
                scale = min(long_edge_limit / longest_points, 2.0) if longest_points else 1.0
                bitmap = page.render(scale=scale)
                images.append(bitmap.to_pil())
            finally:
                page.close()
    except PdfiumError:
        raise ParseInputError(f"{label}第 {len(images) + 1} 页渲染失败，请重新上传") from None
    finally:
        document.close()
    for image in images:
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
    return images


def _encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _prepare_pages_sync(
    settings: Settings, files: list[ParseTaskFileInput]
) -> list[VisionInputPage]:
    """同步实现：读盘 → 解码/渲染 → 编码 PNG（整体在线程池执行）。"""
    total_pages = sum(item.page_count for item in files)
    if total_pages > settings.max_total_pages_per_quote:
        raise ParseConfigError(
            f"本次任务共 {total_pages} 页，超过单次解析上限"
            f"（{settings.max_total_pages_per_quote} 页）；请调低"
            " MAX_TOTAL_PAGES_PER_QUOTE、减少文件，或更换支持更长输入的供应商"
        )

    pages: list[VisionInputPage] = []
    for item in files:
        data = _read_file_bytes(settings, item.relative_path)
        label = f"文件 {item.file_key}"
        if item.mime == "application/pdf":
            images = _prepare_pdf(data, settings.max_image_long_edge, label)
            # 存储页数与实际渲染页数不一致说明数据被绕过校验改动，
            # 以实际渲染为准并要求页码校验按渲染结果收敛
            start_page = 1
            for image in images:
                pages.append(
                    VisionInputPage(
                        fileKey=item.file_key,
                        page=start_page,
                        content=_encode_png(image),
                        mimeType=_OUTPUT_MIME,
                    )
                )
                start_page += 1
        else:
            images = _prepare_image(data, settings.max_image_long_edge, label)
            for image in images:
                pages.append(
                    VisionInputPage(
                        fileKey=item.file_key,
                        page=1,
                        content=_encode_png(image),
                        mimeType=_OUTPUT_MIME,
                    )
                )
    return pages


async def prepare_task_pages(
    settings: Settings, files: list[ParseTaskFileInput]
) -> list[VisionInputPage]:
    """异步入口：CPU 密集的页面准备整体放入线程池（SPEC §13）。"""
    return await asyncio.to_thread(_prepare_pages_sync, settings, files)
