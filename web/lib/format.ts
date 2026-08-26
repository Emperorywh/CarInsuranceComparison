/**
 * 展示格式化工具（SPEC §8 通用约定）：
 * 金额两位小数 + 千分位；保额按“万”展示（3000000 → 300 万）。
 */

/** 金额：null 表示价格缺失，必须显示“—”，不得当作 0 */
export function formatMoney(amount: number | null | undefined): string {
  if (amount === null || amount === undefined) return "—";
  return `¥${amount.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** 保额（元）→ “300 万”；1 万以下保持元 */
export function formatCoverageAmount(amount: number | null | undefined): string {
  if (amount === null || amount === undefined || Number.isNaN(amount)) return "—";
  if (amount >= 10000) {
    const wan = amount / 10000;
    // 整万显示整数，否则保留两位小数（147719.12 元 → 14.77 万）
    return `${wan % 1 === 0 ? wan : wan.toFixed(2)} 万`;
  }
  return `${amount} 元`;
}

/** 日期（ISO 字符串或 null）→ 本地日期展示；null 显示“未设置” */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "未设置";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric" });
}
