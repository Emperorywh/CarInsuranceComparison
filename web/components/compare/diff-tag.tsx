/**
 * 差异标签（SPEC §7.3）：↑ 增加 / ↓ 减少 / + 新增 / − 缺失 / = 相同。
 * 颜色口径：↑ 绿（增加）、↓ 橙（减少）、+/− 用徽标、= 中性灰。
 */
import type { DiffTag } from "@/lib/api";

const TAG_META: Record<
  DiffTag,
  { symbol: string; label: string; className: string }
> = {
  UP: {
    symbol: "↑",
    label: "增加",
    className: "bg-emerald-100 text-emerald-700",
  },
  DOWN: {
    symbol: "↓",
    label: "减少",
    className: "bg-orange-100 text-orange-700",
  },
  ADD: {
    symbol: "+",
    label: "新增",
    className: "bg-sky-100 text-sky-700",
  },
  MISS: {
    symbol: "−",
    label: "缺失",
    className: "bg-rose-100 text-rose-700",
  },
  SAME: {
    symbol: "=",
    label: "相同",
    className: "bg-muted text-muted-foreground",
  },
};

export function DiffTagBadge({ tag }: { tag: DiffTag }) {
  const meta = TAG_META[tag];
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-0.5 rounded-full px-1.5 py-0.5 text-xs font-medium ${meta.className}`}
      aria-label={`${meta.symbol} ${meta.label}`}
    >
      <span aria-hidden>{meta.symbol}</span>
      {meta.label}
    </span>
  );
}
