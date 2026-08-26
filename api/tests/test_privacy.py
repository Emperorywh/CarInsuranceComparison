"""统一脱敏服务单测：覆盖任务清单要求的手机号、身份证号、VIN、完整车牌与个人字段标签。"""

from __future__ import annotations

import logging

from app.core.logging import SensitiveDataFilter
from app.core.privacy import HIDDEN_TEXT, contains_sensitive, sanitize_text


def test_mask_phone_number() -> None:
    assert sanitize_text("联系 13812345678 确认") == "联系 [已脱敏:手机号] 确认"
    # 前后紧邻数字的长串不是手机号，不能被截断匹配
    assert sanitize_text("订单号 91381234567800 不变") == "订单号 91381234567800 不变"


def test_mask_id_card() -> None:
    # 18 位身份证（含校验位 X）
    assert sanitize_text("投保人证件 11010119900307863X") == "投保人证件 [已脱敏:身份证号]"
    # 15 位旧身份证
    assert "110101900307861" not in sanitize_text("旧证 110101900307861 备案")


def test_mask_vin() -> None:
    vin = "LSVAA1234E5678901"
    masked = sanitize_text(f"车架 {vin}")
    assert vin not in masked
    assert "[已脱敏:车架号]" in masked
    # 纯数字 17 位编号（如订单号）不应被误判为 VIN
    assert sanitize_text("单号 12345678901234567") == "单号 12345678901234567"


def test_mask_full_plate() -> None:
    assert sanitize_text("车辆 京A12345 已到店") == "车辆 [已脱敏:车牌] 已到店"
    # 新能源 8 位车牌（省+机关字母+6 位序号，中间无空格）
    assert sanitize_text("新车 粤BD12345 挂牌") != "新车 粤BD12345 挂牌"


def test_remove_personal_label_fragments() -> None:
    # “标签+冒号+值”整段删除
    assert sanitize_text("客户信息：姓名：张三，车型 Model Y") == "客户信息：，车型 Model Y"
    assert sanitize_text("车主:李四 的报价") == " 的报价"
    # 无分隔符的产品名不得被误伤（隐私规则与业务词表冲突时以最小误伤为准）
    assert sanitize_text("平安车主尊享保障 348 元") == "平安车主尊享保障 348 元"


def test_sanitize_none_and_empty() -> None:
    assert sanitize_text(None) == ""
    assert sanitize_text("") == ""


def test_contains_sensitive() -> None:
    assert contains_sensitive("手机 13812345678")
    assert contains_sensitive("车牌 沪C88888")
    assert not contains_sensitive("平安车主尊享保障 348 元")
    assert not contains_sensitive(None)


def test_hidden_text_constant() -> None:
    # 无法安全处理时的统一文案（供后续 Task 的 evidence 兜底使用）
    assert HIDDEN_TEXT == "来源文本已隐藏"


def test_log_filter_sanitizes_records() -> None:
    # 安全日志过滤器：消息与插值参数都要脱敏
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="项目备注：%s", args=("13812345678",), exc_info=None,
    )
    assert SensitiveDataFilter().filter(record) is True
    assert "13812345678" not in record.getMessage()
    assert "[已脱敏:手机号]" in record.getMessage()
