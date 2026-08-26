"""统一脱敏服务（SPEC §9.3）。

不变量：
- 所有用户自由文本（项目备注、公司名、请求错误信息、日志，以及后续的
  原始文件名/解析结果/证据摘录）在落库或写日志之前必须经过本模块，
  各路由与服务不得自行拼正则，避免规则漂移；
- 手机号、身份证号、VIN、完整车牌替换为占位符；“个人字段标签 + 取值”
  （姓名/车主/被保险人 等）命中的片段整段删除；
- 无法安全处理时调用方可使用 HIDDEN_TEXT（“来源文本已隐藏”）整段替换。

后续 Task（解析流水线）在此模块上扩展原始文件名与模型输出白名单过滤。
"""

from __future__ import annotations

import re

# 无法安全处理时的整段替换文案（SPEC §9.3）
HIDDEN_TEXT = "来源文本已隐藏"

# ---- 各类敏感模式的占位符 ----
_PHONE_PLACEHOLDER = "[已脱敏:手机号]"
_ID_PLACEHOLDER = "[已脱敏:身份证号]"
_VIN_PLACEHOLDER = "[已脱敏:车架号]"
_PLATE_PLACEHOLDER = "[已脱敏:车牌]"

# 手机号：11 位大陆手机号；前后不能再紧邻数字，避免截取长数字串的一部分
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

# 身份证号：18 位（含校验位 X）与 15 位旧证；日期部分做形状校验以降低误报
_ID_18_RE = re.compile(
    r"(?<!\d)"
    r"\d{6}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]"
    r"(?![0-9Xx])"
)
_ID_15_RE = re.compile(
    r"(?<!\d)\d{6}\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}(?!\d)"
)

# VIN 候选：恰好 17 位、不含易混淆字符 I/O/Q、两侧不紧邻其他字母数字。
# 真实车架号必然同时含字母与数字，用该条件过滤普通 17 位纯数字编号（如订单号）
_VIN_CANDIDATE_RE = re.compile(r"(?<![A-Za-z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Za-z0-9])")

# 完整车牌：省份简称 + 发牌机关字母 + 4~6 位序号（覆盖新能源 8 位与挂学警港澳）
_PLATE_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领]"
    r"[A-HJ-NP-Z]"
    r"[A-HJ-NP-Z0-9挂学警港澳]{4,6}"
    r"(?![A-Za-z0-9])"
)

# 个人字段标签：命中“标签 + 冒号/空白 + 取值”的片段整段删除。
# 标签后必须跟显式分隔（冒号或空白），避免误伤“车主尊享保障”等产品名
_LABEL_VALUE_RE = re.compile(
    r"(?:姓名|车主名?|被保险人|投保人|驾驶人|乘车人|"
    r"身份证号码?|证件号码?|手机号码?|联系电话?|联系手机|"
    r"车架号码?|VIN号?|vin号?|发动机号码?)"
    r"\s*(?:[:：]\s*|\s+)"
    r"[^\s，。;；,、\-—]{1,32}"
)


def _mask_vin(text: str) -> str:
    """对 17 位 VIN 候选做字母+数字构成校验后替换为占位符。"""

    def _replace(match: re.Match[str]) -> str:
        candidate = match.group(0)
        has_digit = any(ch.isdigit() for ch in candidate)
        has_letter = any(ch.isalpha() for ch in candidate)
        return _VIN_PLACEHOLDER if has_digit and has_letter else candidate

    return _VIN_CANDIDATE_RE.sub(_replace, text)


def sanitize_text(text: str | None) -> str:
    """对自由文本统一脱敏；None 视为空串。

    处理顺序：先整段删除“个人字段标签+取值”片段，再对剩余文本中的
    身份证号/手机号/车牌/VIN 做占位符替换。
    """
    if not text:
        return ""
    result = _LABEL_VALUE_RE.sub("", text)
    result = _ID_18_RE.sub(_ID_PLACEHOLDER, result)
    result = _ID_15_RE.sub(_ID_PLACEHOLDER, result)
    result = _PHONE_RE.sub(_PHONE_PLACEHOLDER, result)
    result = _PLATE_RE.sub(_PLATE_PLACEHOLDER, result)
    result = _mask_vin(result)
    return result


def contains_sensitive(text: str | None) -> bool:
    """检测文本是否命中任一敏感模式（不改写原文）。

    用途：上传原始文件名等场景的预检——命中即改用通用文件名。
    """
    if not text:
        return False
    patterns: tuple[re.Pattern[str], ...] = (
        _LABEL_VALUE_RE,
        _ID_18_RE,
        _ID_15_RE,
        _PHONE_RE,
        _PLATE_RE,
    )
    if any(pattern.search(text) for pattern in patterns):
        return True
    # VIN 需要走构成校验，不能只用候选正则判断
    return any(
        any(ch.isdigit() for ch in m.group(0)) and any(ch.isalpha() for ch in m.group(0))
        for m in _VIN_CANDIDATE_RE.finditer(text)
    )
