"use client";

import { cn } from "@/lib/utils";
import { statusLabel } from "@/lib/api";

/**
 * 状态徽标：统一按“枚举组 + 值”查后端字典的中文标签。
 * 颜色只表达状态语义（成功/警告/危险/中性），不携带额外语义。
 */

type Tone = "success" | "warn" | "danger" | "info" | "muted";

const TONE_CLASSES: Record<Tone, string> = {
  success: "bg-emerald-100 text-emerald-700",
  warn: "bg-amber-100 text-amber-700",
  danger: "bg-red-100 text-red-700",
  info: "bg-sky-100 text-sky-700",
  muted: "bg-muted text-muted-foreground",
};

// 各枚举值的展示色调；未列出的值统一中性灰
const TONE_MAP: Record<string, Tone> = {
  // 报价状态
  "quoteStatus:DRAFT": "muted",
  "quoteStatus:PARSING": "info",
  "quoteStatus:PENDING_CONFIRM": "warn",
  "quoteStatus:CONFIRMED": "success",
  "quoteStatus:PARSE_FAILED": "danger",
  "quoteStatus:MERGE_REVIEW": "info",
  // 总额校验三态
  "totalCheckStatus:PASSED": "success",
  "totalCheckStatus:MISMATCH": "danger",
  "totalCheckStatus:NOT_CHECKABLE": "muted",
  // 净支出状态
  "netPaymentStatus:OK": "success",
  "netPaymentStatus:MISSING_TOTAL": "warn",
  "netPaymentStatus:INVALID_DISCOUNT": "danger",
  // 明细行状态
  "itemStatus:INCLUDED": "success",
  "itemStatus:FREE": "success",
  "itemStatus:NOT_INCLUDED": "muted",
  "itemStatus:NOT_APPLICABLE": "muted",
  "itemStatus:UNKNOWN": "warn",
  // 价格分项状态
  "priceItemStatus:INCLUDED": "success",
  "priceItemStatus:NOT_INCLUDED": "muted",
  "priceItemStatus:UNKNOWN": "warn",
  // 置信度
  "confidenceLevel:HIGH": "success",
  "confidenceLevel:MEDIUM": "warn",
  "confidenceLevel:LOW": "danger",
};

export function StatusBadge({
  group,
  value,
  className,
}: {
  group: string;
  value: string | null | undefined;
  className?: string;
}) {
  if (!value) return null;
  const tone = TONE_MAP[`${group}:${value}`] ?? "muted";
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium",
        TONE_CLASSES[tone],
        className
      )}
    >
      {statusLabel(group, value)}
    </span>
  );
}
