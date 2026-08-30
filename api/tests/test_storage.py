"""存储与上传预检纯单元测试（不依赖数据库）。

覆盖：原始文件名脱敏、损坏/加密/超页 PDF 拒绝、图片真实格式与像素上限
校验（TASK-03 范围 1-3 的纯函数部分）。格式三重一致性在 API 集成测试
中覆盖（test_files_api.py）。
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, UnidentifiedImageError

from app.config import Settings
from app.core.errors import ValidationError
from app.core.privacy import contains_sensitive, sanitize_file_name
from app.services.storage.validation import _inspect_image, _inspect_pdf
from tests.files_helpers import (
    broken_pdf_bytes,
    encrypted_pdf_bytes,
    jpeg_bytes,
    pdf_bytes,
    png_bytes,
    webp_bytes,
)

_DEFAULT_SETTINGS = Settings(app_bind_host="127.0.0.1")


# ---- 原始文件名脱敏（SPEC §9.3：originalName 先脱敏再入库）----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 普通名称：保留清洗后的展示名
        ("报价单.jpg", "报价单.jpg"),
        ("PICC-2026 quote.pdf", "PICC-2026 quote.pdf"),
        # 命中车牌/手机号：整个改用通用名，不做局部替换
        ("京A12345的保单.jpg", "报价单.jpg"),
        ("保单 13812345678.pdf", "报价单.pdf"),
        # 个人字段标签命中后无剩余内容：用通用名
        ("车主_张三.jpg", "报价单.jpg"),
        # 路径成分被剥掉（防穿越），只留 basename
        ("..\\..\\etc\\passwd.png", "passwd.png"),
        # 空名兜底
        ("", "报价单.bin"),
        (".pdf", "报价单.pdf"),
    ],
)
def test_sanitize_file_name(raw: str, expected: str) -> None:
    assert sanitize_file_name(raw) == expected


def test_sanitize_file_name_custom_fallback() -> None:
    # 多文件批次用“报价单N”作通用名，避免重名
    assert sanitize_file_name("京A12345.png", fallback_stem="报价单2") == "报价单2.png"


def test_contains_sensitive_plate_and_phone() -> None:
    assert contains_sensitive("车牌京A12345.jpg")
    assert contains_sensitive("联系13800138000.png")
    assert not contains_sensitive("平安2026续保报价.pdf")


# ---- PDF 预检 ----


def test_pdf_ok_and_page_count() -> None:
    assert _inspect_pdf(pdf_bytes(3), 1, _DEFAULT_SETTINGS) == 3


def test_pdf_encrypted_rejected() -> None:
    with pytest.raises(ValidationError):
        _inspect_pdf(encrypted_pdf_bytes(), 1, _DEFAULT_SETTINGS)


def test_pdf_broken_rejected() -> None:
    with pytest.raises(ValidationError):
        _inspect_pdf(broken_pdf_bytes(), 1, _DEFAULT_SETTINGS)


def test_pdf_too_many_pages_rejected() -> None:
    settings = Settings(app_bind_host="127.0.0.1", max_pdf_pages=2)
    with pytest.raises(ValidationError):
        _inspect_pdf(pdf_bytes(3), 1, settings)


# ---- 图片预检 ----


def test_image_jpeg_ok() -> None:
    assert _inspect_image(jpeg_bytes(), 1, _DEFAULT_SETTINGS) == 1


def test_image_png_ok() -> None:
    assert _inspect_image(png_bytes(), 1, _DEFAULT_SETTINGS) == 1


def test_image_pixel_bomb_rejected() -> None:
    # 头部声明 4000x4000 = 1600 万像素，超过收窄后的 20 万上限即拒绝，
    # 无需真实解码大图（解压炸弹防护以头部声明尺寸为准）
    settings = Settings(app_bind_host="127.0.0.1", max_image_pixels=200_000)
    buffer = io.BytesIO()
    Image.new("RGB", (4000, 4000)).save(buffer, format="JPEG")
    with pytest.raises(ValidationError):
        _inspect_image(buffer.getvalue(), 1, settings)


def test_image_webp_rejected() -> None:
    with pytest.raises(ValidationError):
        _inspect_image(webp_bytes(), 1, _DEFAULT_SETTINGS)


def test_image_corrupted_rejected() -> None:
    # JPEG 魔数 + 截断正文：verify 完整性校验失败
    with pytest.raises(ValidationError):
        _inspect_image(b"\xff\xd8\xff" + b"\x00" * 32, 1, _DEFAULT_SETTINGS)


def test_image_wrong_format_bytes_rejected_by_pillow() -> None:
    # PNG 字节无法在“仅支持 JPEG/PNG”校验之外的伪装场景存活：
    # 图片服务对非图片字节直接抛解码错误（签名层由 API 层前置拦截）
    with pytest.raises((UnidentifiedImageError, OSError, ValidationError)):
        _inspect_image(b"\x89PNG-corrupted", 1, _DEFAULT_SETTINGS)
