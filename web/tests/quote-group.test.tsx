/**
 * 项目页报价分组卡片测试（TASK-02）：按“公司+保险员”分组、同来源提示、
 * 净支出与摘要展示、官方总价异常可见。
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QuoteGroupCard } from "@/components/quote/quote-group-card";
import type { QuoteGroup } from "@/lib/api";

// StatusBadge 依赖字典快照查中文标签：设置快照避免渲染空标签
import { setDictionariesSnapshot } from "@/lib/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const group: QuoteGroup = {
  insurerCode: "PINGAN",
  insurerName: "平安",
  agentName: "小王",
  sameSourceHint: true,
  quotes: [
    {
      id: 1,
      insurerCode: "PINGAN",
      insurerName: "平安",
      agentName: "小王",
      planLabel: "方案A",
      source: "MANUAL",
      status: "CONFIRMED",
      netPayment: 5420,
      netPaymentStatus: "OK",
      officialTotal: 5420,
      computedTotal: 5420,
      totalCheckStatus: "PASSED",
      thirdPartyAmount: 2000000,
      tpNonMedicalAmount: 1000000,
      createdAt: "2026-08-26T10:00:00Z",
    },
    {
      id: 2,
      insurerCode: "PINGAN",
      insurerName: "平安",
      agentName: "小王",
      planLabel: "方案B",
      source: "MANUAL",
      status: "PENDING_CONFIRM",
      netPayment: null,
      netPaymentStatus: "MISSING_TOTAL",
      officialTotal: 5785.14,
      computedTotal: 5785.14,
      totalCheckStatus: "MISMATCH",
      thirdPartyAmount: 3000000,
      tpNonMedicalAmount: 500000,
      createdAt: "2026-08-26T11:00:00Z",
    },
  ],
} as unknown as QuoteGroup;

describe("项目报价分组卡片", () => {
  it("按公司+保险员分组展示，多份报价提示“同来源”", () => {
    setDictionariesSnapshot({
      statusLabels: {
        quoteStatus: { CONFIRMED: "已确认", PENDING_CONFIRM: "待确认" },
        netPaymentStatus: { OK: "正常", MISSING_TOTAL: "总价缺失", INVALID_DISCOUNT: "优惠超额" },
        totalCheckStatus: { PASSED: "校验通过", MISMATCH: "金额不一致", NOT_CHECKABLE: "无法校验" },
      },
    } as never);
    render(<QuoteGroupCard group={group} />);
    expect(screen.getByText("平安")).toBeInTheDocument();
    expect(screen.getByText(/同来源报价/)).toBeInTheDocument();
    expect(screen.getByText("方案A")).toBeInTheDocument();
    expect(screen.getByText("方案B")).toBeInTheDocument();
  });

  it("净支出缺失标注“总价缺失”，官方总价异常给出提示", () => {
    setDictionariesSnapshot({
      statusLabels: {
        quoteStatus: { CONFIRMED: "已确认", PENDING_CONFIRM: "待确认" },
        netPaymentStatus: { OK: "正常", MISSING_TOTAL: "总价缺失", INVALID_DISCOUNT: "优惠超额" },
        totalCheckStatus: { PASSED: "校验通过", MISMATCH: "金额不一致", NOT_CHECKABLE: "无法校验" },
      },
    } as never);
    render(<QuoteGroupCard group={group} />);
    expect(screen.getByText("总价缺失")).toBeInTheDocument();
    expect(screen.getByText(/官方总价与系统计算不一致/)).toBeInTheDocument();
    // 三者与医保外摘要按“万”展示
    expect(screen.getAllByText(/300 万/).length).toBeGreaterThan(0);
  });

  it("待确认报价勾选禁用时展示可见原因与“去确认”入口", () => {
    setDictionariesSnapshot({
      statusLabels: {
        quoteStatus: { CONFIRMED: "已确认", PENDING_CONFIRM: "待确认" },
        netPaymentStatus: { OK: "正常" },
        totalCheckStatus: { PASSED: "校验通过" },
      },
    } as never);
    render(
      <QuoteGroupCard
        group={group}
        selection={{ selected: [], onToggle: vi.fn(), limitReached: false }}
      />
    );
    const pendingCheckbox = screen.getByRole("checkbox", { name: "勾选 方案B 加入对比" });
    expect(pendingCheckbox).toBeDisabled();
    // 禁用原因必须在页面上可见，而不是只放在触屏无效的 title 悬停提示里
    expect(screen.getByText(/待确认报价需先确认才能参与对比/)).toBeInTheDocument();
    // 提供直达确认流程的入口（与报价详情页“去确认报价”同一路径）
    expect(screen.getByRole("link", { name: "去确认 方案B" })).toHaveAttribute(
      "href",
      "/quotes/2/confirm"
    );
    // 已确认报价不受影响，可正常勾选
    expect(screen.getByRole("checkbox", { name: "勾选 方案A 加入对比" })).toBeEnabled();
  });

  it("达到勾选上限后，未勾选报价在页面上提示最多 6 个", () => {
    setDictionariesSnapshot({
      statusLabels: {
        quoteStatus: { CONFIRMED: "已确认", PENDING_CONFIRM: "待确认" },
        netPaymentStatus: { OK: "正常" },
        totalCheckStatus: { PASSED: "校验通过" },
      },
    } as never);
    render(
      <QuoteGroupCard
        group={group}
        selection={{ selected: [], onToggle: vi.fn(), limitReached: true }}
      />
    );
    // 已确认但未勾选：上限禁用原因页面可见
    expect(screen.getByRole("checkbox", { name: "勾选 方案A 加入对比" })).toBeDisabled();
    expect(screen.getByText("最多对比 6 个报价")).toBeInTheDocument();
    // 待确认报价的禁用原因优先于上限提示
    expect(screen.getByText(/待确认报价需先确认才能参与对比/)).toBeInTheDocument();
  });
});
