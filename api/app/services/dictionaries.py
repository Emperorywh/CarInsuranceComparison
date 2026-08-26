"""对外字典装配（TASK-02 实施范围第 3 条）。

单一代码来源：险种/公司/保障包类型来自 alias_map，服务类型、标注形式、
优惠类型与各状态枚举来自 models.enums；前端只消费 /api/dictionaries
或 OpenAPI 生成类型，不复制第二套易漂移字典。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.models.enums import (
    AnnotationKind,
    ConfidenceLevel,
    CoverageCategory,
    DiscountType,
    ItemStatus,
    NetPaymentStatus,
    OfficialTotalStatus,
    PackageUnit,
    PriceItemStatus,
    QuoteSource,
    QuoteStatus,
    ServiceType,
    TotalCheckStatus,
)
from app.services.normalization.alias_map import (
    COVERAGE_DEFINITIONS,
    INSURER_DEFINITIONS,
    PACKAGE_COVERAGE_DEFINITIONS,
)


def _pair(enum_cls, labels: Sequence[str]):  # noqa: ANN001 - StrEnum 通用
    """枚举成员与中文标签按声明顺序配对（长度不一致时开发期即报错）。"""
    members = list(enum_cls)
    if len(members) != len(labels):
        raise ValueError(f"{enum_cls.__name__} 枚举与标签数量不一致")
    return zip(members, labels, strict=True)


# 各状态枚举的中文展示名；键名与 SPEC/OpenAPI 枚举名对应，前端按 key 查表
STATUS_LABELS: dict[str, dict[str, str]] = {
    "quoteStatus": dict(
        _pair(
            QuoteStatus,
            ("草稿", "解析中", "待确认", "已确认", "解析失败", "合并确认中"),
        )
    ),
    "quoteSource": dict(_pair(QuoteSource, ("上传解析", "手动录入"))),
    "priceItemStatus": dict(_pair(PriceItemStatus, ("已包含", "不包含", "未知"))),
    "officialTotalStatus": dict(_pair(OfficialTotalStatus, ("已识别", "未知"))),
    "itemStatus": dict(_pair(ItemStatus, ("已包含", "不包含", "免费", "不适用", "未知"))),
    "coverageCategory": dict(_pair(CoverageCategory, ("基础车险", "附加险", "未识别"))),
    "confidenceLevel": dict(_pair(ConfidenceLevel, ("高", "中", "低"))),
    "serviceType": dict(
        _pair(ServiceType, ("道路救援", "车辆安全检测", "代驾", "代办送检", "其他"))
    ),
    "packageUnit": dict(_pair(PackageUnit, ("元", "次", "天", "其他"))),
    "annotationKind": dict(
        _pair(AnnotationKind, ("红字标注", "箭头标注", "手写标注", "额外宣传", "其他"))
    ),
    "discountType": dict(
        _pair(
            DiscountType,
            ("现金返现", "微信红包", "购物卡", "油卡", "优惠券", "服务权益", "其他"),
        )
    ),
    "totalCheckStatus": dict(_pair(TotalCheckStatus, ("无法校验", "校验通过", "金额不一致"))),
    "netPaymentStatus": dict(_pair(NetPaymentStatus, ("正常", "总价缺失", "优惠超额"))),
}


def build_dictionaries() -> dict:
    """组装 /api/dictionaries 响应数据（顺序即推荐展示顺序）。"""
    return {
        "insurers": [
            {"code": code, "label": label} for code, label in INSURER_DEFINITIONS.items()
        ],
        # 交强险条目带 COMPULSORY 类别且 rowSelectable=false：
        # 只允许出现在价格分项，不允许作为险种行录入
        "coverage_codes": [
            {
                "code": definition.code,
                "label": definition.label,
                "category": definition.category,
                "rowSelectable": definition.row_selectable,
            }
            for definition in COVERAGE_DEFINITIONS.values()
        ],
        "package_coverage_types": [
            {"code": code, "label": label}
            for code, label in PACKAGE_COVERAGE_DEFINITIONS.items()
        ],
        "service_types": [
            {"code": member.value, "label": STATUS_LABELS["serviceType"][member.value]}
            for member in ServiceType
        ],
        "annotation_kinds": [
            {"code": member.value, "label": STATUS_LABELS["annotationKind"][member.value]}
            for member in AnnotationKind
        ],
        "discount_types": [
            {"code": member.value, "label": STATUS_LABELS["discountType"][member.value]}
            for member in DiscountType
        ],
        "package_units": [
            {"code": member.value, "label": STATUS_LABELS["packageUnit"][member.value]}
            for member in PackageUnit
        ],
        "status_labels": STATUS_LABELS,
    }
