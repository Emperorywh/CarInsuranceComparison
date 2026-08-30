/**
 * 报价详情页组件测试（TASK-02）：优惠编辑（SERVICE 默认无折现值）、
 * 净支出异常标注、官方总价异常提示。mock 统一 API 客户端，不访问网络。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import QuoteDetailPage from "@/app/quotes/[id]/page";
import { loadDictionaries, quotesApi, type Quote } from "@/lib/api";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh: vi.fn() }),
  useParams: () => ({ id: "1" }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, loadDictionaries: vi.fn() };
});

const dict = {
  insurers: [],
  coverageCodes: [],
  packageCoverageTypes: [],
  serviceTypes: [],
  annotationKinds: [],
  discountTypes: [
    { code: "CASH", label: "现金返现" },
    { code: "SERVICE", label: "服务权益" },
  ],
  packageUnits: [],
  statusLabels: {
    quoteStatus: { PENDING_CONFIRM: "待确认", CONFIRMED: "已确认" },
    netPaymentStatus: { OK: "正常", MISSING_TOTAL: "总价缺失", INVALID_DISCOUNT: "优惠超额" },
    totalCheckStatus: { PASSED: "校验通过", MISMATCH: "金额不一致", NOT_CHECKABLE: "无法校验" },
  },
};

function makeQuote(overrides: Partial<Quote> = {}): Quote {
  return {
    id: 1,
    projectId: 10,
    insurerCode: "PINGAN",
    insurerName: "平安",
    agentName: "小王",
    planLabel: null,
    source: "MANUAL",
    status: "CONFIRMED",
    note: null,
    vehicleModel: null,
    vehicleSeats: null,
    firstRegDate: null,
    isNev: null,
    commercialPremium: 4392.14,
    computedCommercialPremium: null,
    commercialStatus: "INCLUDED",
    compulsoryPremium: 1045,
    compulsoryStatus: "INCLUDED",
    vehicleTax: 0,
    vehicleTaxStatus: "INCLUDED",
    packageTotal: 348,
    computedPackageTotal: null,
    packageStatus: "INCLUDED",
    otherFees: null,
    otherFeesStatus: "NOT_INCLUDED",
    officialTotal: 5785.14,
    officialTotalStatus: "INCLUDED",
    computedTotal: 5785.14,
    totalCheckStatus: "PASSED",
    netPayment: 5485.14,
    netPaymentStatus: "OK",
    vehicleConflict: { fields: [], firstRegDateDiffers: false, resolutionRequired: false },
    // TASK-03：报价关联文件（手动报价恒为空数组）
    files: [],
    coverages: [
      {
        id: 3,
        category: "CORE",
        code: "THIRD_PARTY_LIABILITY",
        rawName: "三者险",
        rawValue: null,
        name: "三者险",
        status: "INCLUDED",
        coverageAmount: 3000000,
        perSeatAmount: null,
        seatCount: null,
        sharedCoverage: null,
        premium: 1237.41,
        multiplier: null,
        condition: null,
        description: null,
        confidenceLevel: "HIGH",
        sourceFileId: null,
        sourcePage: null,
        sourceText: null,
        editedByUser: true,
        amountRangeHint: null,
      } as never,
    ],
    services: [],
    packages: [],
    annotations: [],
    discounts: [],
    evidences: [],
    createdAt: "2026-08-26T10:00:00Z",
    updatedAt: "2026-08-26T10:00:00Z",
    ...overrides,
  } as Quote;
}

beforeEach(() => {
  vi.mocked(loadDictionaries).mockResolvedValue(dict as never);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  push.mockClear();
});

describe("报价详情页", () => {
  it("展示价格摘要、三者摘要与净支出", async () => {
    vi.spyOn(quotesApi, "get").mockResolvedValue(makeQuote());
    render(<QuoteDetailPage />);
    expect(await screen.findByText("平安 · 小王")).toBeInTheDocument();
    expect(await screen.findByText("¥5,485.14")).toBeInTheDocument();
    expect(screen.getByText("300 万")).toBeInTheDocument();
    // 已确认报价的编辑入口是链接按钮（Button asChild + Link）
    expect(screen.getByRole("link", { name: /编辑确认内容/ })).toBeInTheDocument();
  });

  it("添加优惠：SERVICE 类不填折现值时按空提交（不自动折现）", async () => {
    vi.spyOn(quotesApi, "get").mockResolvedValue(makeQuote());
    const createDiscount = vi
      .spyOn(quotesApi, "createDiscount")
      .mockResolvedValue(makeQuote({ discounts: [] }));

    render(<QuoteDetailPage />);
    // 表单初始为现金返现，切换为服务权益
    fireEvent.change(await screen.findByLabelText("新增优惠类型"), {
      target: { value: "SERVICE" },
    });
    fireEvent.change(screen.getByLabelText("新增优惠说明"), {
      target: { value: "洗车5次" },
    });
    fireEvent.change(screen.getByLabelText("新增优惠名义金额（元）"), {
      target: { value: "200" },
    });
    fireEvent.click(screen.getByRole("button", { name: "+ 添加优惠" }));

    await waitFor(() =>
      expect(createDiscount).toHaveBeenCalledWith(
        1,
        expect.objectContaining({
          discountType: "SERVICE",
          amount: "200",
          cashEquivalent: null, // 无折现值不减钱（PRD §26）
          includeInNet: true,
        })
      )
    );
  });

  it("净支出异常时按状态标注（优惠超额不当 0）", async () => {
    vi.spyOn(quotesApi, "get").mockResolvedValue(
      makeQuote({ netPayment: null, netPaymentStatus: "INVALID_DISCOUNT" })
    );
    render(<QuoteDetailPage />);
    expect(await screen.findByText("优惠超额")).toBeInTheDocument();
    // 净支出缺失显示“—”（不得显示为 ¥0）：页面至少出现一个空值占位
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("官方总价与系统总价不一致时保留两者并提示", async () => {
    vi.spyOn(quotesApi, "get").mockResolvedValue(
      makeQuote({ totalCheckStatus: "MISMATCH", computedTotal: 3880.41 })
    );
    render(<QuoteDetailPage />);
    expect(await screen.findByText("金额不一致")).toBeInTheDocument();
    expect(
      screen.getByText(/官方总价与系统计算总价不一致，请核对/)
    ).toBeInTheDocument();
  });
});
