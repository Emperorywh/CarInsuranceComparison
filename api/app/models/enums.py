"""全部数据库枚举（SPEC §2/§3）。

单一来源：前后端展示值由 OpenAPI 契约驱动，不在前端另抄一份字典。
"""

from __future__ import annotations

from enum import StrEnum


class QuoteSource(StrEnum):
    """报价来源：上传解析 / 纯手动录入。"""

    UPLOADED = "UPLOADED"
    MANUAL = "MANUAL"


class QuoteStatus(StrEnum):
    """报价状态机（SPEC §2.10），MVP 冻结不允许改动取值。"""

    DRAFT = "DRAFT"
    PARSING = "PARSING"
    PENDING_CONFIRM = "PENDING_CONFIRM"
    CONFIRMED = "CONFIRMED"
    PARSE_FAILED = "PARSE_FAILED"
    MERGE_REVIEW = "MERGE_REVIEW"


class PriceItemStatus(StrEnum):
    """价格分项状态：不包含按 0 参与；未知使系统合计不可计算。"""

    INCLUDED = "INCLUDED"
    NOT_INCLUDED = "NOT_INCLUDED"
    UNKNOWN = "UNKNOWN"


class OfficialTotalStatus(StrEnum):
    """官方总价只可能出现“识别到”或“未知”。"""

    INCLUDED = "INCLUDED"
    UNKNOWN = "UNKNOWN"


class TotalCheckStatus(StrEnum):
    """总额校验三态，避免把无法校验误写为“通过”。"""

    NOT_CHECKABLE = "NOT_CHECKABLE"
    PASSED = "PASSED"
    MISMATCH = "MISMATCH"


class NetPaymentStatus(StrEnum):
    """净支出状态：缺失总价 / 优惠超额都不得自行算 0。"""

    OK = "OK"
    MISSING_TOTAL = "MISSING_TOTAL"
    INVALID_DISCOUNT = "INVALID_DISCOUNT"


class CoverageCategory(StrEnum):
    """险种行类别：主险 / 附加险 / 未识别。"""

    CORE = "CORE"
    ADDITIONAL = "ADDITIONAL"
    UNRECOGNIZED = "UNRECOGNIZED"


class ItemStatus(StrEnum):
    """险种与服务行的状态语义（SPEC §6 第 6 项）。"""

    INCLUDED = "INCLUDED"
    NOT_INCLUDED = "NOT_INCLUDED"
    FREE = "FREE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class ConfidenceLevel(StrEnum):
    """置信度三档（合成规则见 SPEC §4.2）。"""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ServiceType(StrEnum):
    """增值服务类型（SPEC §3.4）。"""

    ROAD_RESCUE = "ROAD_RESCUE"
    INSPECTION = "INSPECTION"
    DRIVER_SERVICE = "DRIVER_SERVICE"
    INSPECTION_AGENT = "INSPECTION_AGENT"
    OTHER = "OTHER"


class PackageUnit(StrEnum):
    """保障包内部保障的计量单位；无法安全换算用 OTHER。"""

    CNY = "CNY"
    TIMES = "TIMES"
    DAYS = "DAYS"
    OTHER = "OTHER"


class AnnotationKind(StrEnum):
    """销售标注的呈现形式，仅影响展示不影响隔离规则。"""

    RED_TEXT = "RED_TEXT"
    ARROW = "ARROW"
    HANDWRITTEN = "HANDWRITTEN"
    EXTRA_PROMO = "EXTRA_PROMO"
    OTHER = "OTHER"


class AnnotationSourceType(StrEnum):
    """标注来源：模型识别的销售标注 / 用户自己补充。"""

    SALES_ANNOTATION = "SALES_ANNOTATION"
    USER_ANNOTATION = "USER_ANNOTATION"


class DiscountType(StrEnum):
    """优惠类型（用户填写）。"""

    CASH = "CASH"
    RED_PACKET = "RED_PACKET"
    GIFT_CARD = "GIFT_CARD"
    OIL_CARD = "OIL_CARD"
    COUPON = "COUPON"
    SERVICE = "SERVICE"
    OTHER = "OTHER"


class ParseTaskStatus(StrEnum):
    """解析任务状态。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class MergeChangeKind(StrEnum):
    """补传合并变更类型：只生成新增与冲突，不自动删除。"""

    ADD = "ADD"
    CONFLICT = "CONFLICT"


class MergeResolution(StrEnum):
    """合并逐项解决状态；全部处理完才回 CONFIRMED。"""

    ACCEPT = "ACCEPT"
    KEEP = "KEEP"
    PENDING = "PENDING"
