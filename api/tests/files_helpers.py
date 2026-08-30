"""文件上传测试夹具构造器：在测试进程内动态生成合法/非法文件字节。

不依赖任何二进制 fixture 文件；pypdf（仅 dev 依赖）用于生成合法与加密
PDF，Pillow 生成图片。所有内容均为测试自造数据，不含任何真实个人信息。
"""

from __future__ import annotations

import io

from PIL import Image
from pypdf import PdfWriter


def jpeg_bytes(size: tuple[int, int] = (64, 48), color=(30, 144, 255)) -> bytes:
    """生成一张最小合法 JPEG。"""
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def png_bytes(size: tuple[int, int] = (64, 48), color=(255, 99, 71)) -> bytes:
    """生成一张最小合法 PNG。"""
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def webp_bytes(size: tuple[int, int] = (64, 48)) -> bytes:
    """生成 WebP 字节（用于白名单外格式拒绝用例）。"""
    buffer = io.BytesIO()
    Image.new("RGB", size).save(buffer, format="WEBP")
    return buffer.getvalue()


def pdf_bytes(pages: int = 1) -> bytes:
    """生成指定页数的最小合法 PDF。"""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def encrypted_pdf_bytes() -> bytes:
    """生成带用户密码的加密 PDF（用于“加密文档拒绝”用例）。"""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(user_password="test-secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def broken_pdf_bytes() -> bytes:
    """生成损坏 PDF：带 PDF 魔数但结构非法（签名层无法拦截）。"""
    return b"%PDF-1.4 this is not a real pdf body"


def text_bytes() -> bytes:
    return "这不是图片也不是 PDF".encode()
