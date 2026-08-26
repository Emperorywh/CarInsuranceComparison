/**
 * 展示格式化工具单测：金额/保额格式与空值语义（null 不是 0）。
 */
import { describe, expect, it } from "vitest";

import { formatCoverageAmount, formatDate, formatMoney } from "@/lib/format";

describe("formatMoney", () => {
  it("金额两位小数千分位", () => {
    expect(formatMoney(5420)).toBe("¥5,420.00");
    expect(formatMoney(1234567.8)).toBe("¥1,234,567.80");
  });

  it("缺失金额显示占位符，绝不显示 ¥0.00", () => {
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney(undefined)).toBe("—");
  });
});

describe("formatCoverageAmount", () => {
  it("整万保额按万展示", () => {
    expect(formatCoverageAmount(3000000)).toBe("300 万");
  });

  it("非整万保额保留两位小数", () => {
    expect(formatCoverageAmount(147719.12)).toBe("14.77 万");
  });

  it("万元以下按元展示；缺失显示占位符", () => {
    expect(formatCoverageAmount(500)).toBe("500 元");
    expect(formatCoverageAmount(null)).toBe("—");
  });
});

describe("formatDate", () => {
  it("空日期显示未设置；有效日期本地化展示", () => {
    expect(formatDate(null)).toBe("未设置");
    expect(formatDate("2026-05-31")).toContain("2026");
  });
});
