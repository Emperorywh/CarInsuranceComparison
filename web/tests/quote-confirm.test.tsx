/**
 * 报价确认页组件测试（TASK-02 验证第 3 条）：
 * 7 Tab 结构、价格分项保存、未识别保障映射/丢弃、车辆冲突阻断与解除、
 * 确认接口错误提示。mock 统一 API 客户端，不访问网络。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import QuoteConfirmPage from "@/app/quotes/[id]/confirm/page";
import { loadDictionaries, quotesApi, type Quote } from "@/lib/api";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh: vi.fn() }),
  useParams: () => ({ id: "1" }),
}));

// 字典加载是模块级函数导出：用 mock 工厂替换，其余导出保持原实现，
// 保证 setDictionariesSnapshot 等真实逻辑继续工作
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    loadDictionaries: vi.fn(),
  };
});

// ---- 测试夹具 ----

const dict = {
  insurers: [{ code: "PINGAN", label: "平安" }],
  coverageCodes: [
    { code: "VEHICLE_LOSS", label: "车损险", category: "CORE", rowSelectable: true },
    { code: "THIRD_PARTY_LIABILITY", label: "三者险", category: "CORE", rowSelectable: true },
    { code: "TP_NON_MEDICAL", label: "三者医保外", category: "ADDITIONAL", rowSelectable: true },
    { code: "COMPULSORY", label: "交强险", category: "COMPULSORY", rowSelectable: false },
  ],
  packageCoverageTypes: [
    { code: "DRIVER_ACCIDENT", label: "驾乘意外" },
    { code: "OTHER", label: "其他" },
  ],
  serviceTypes: [{ code: "ROAD_RESCUE", label: "道路救援" }],
  annotationKinds: [{ code: "HANDWRITTEN", label: "手写标注" }],
  discountTypes: [
    { code: "CASH", label: "现金返现" },
    { code: "SERVICE", label: "服务权益" },
  ],
  packageUnits: [{ code: "CNY", label: "元" }],
  statusLabels: {
    quoteStatus: { PENDING_CONFIRM: "待确认", CONFIRMED: "已确认" },
    netPaymentStatus: { OK: "正常", MISSING_TOTAL: "总价缺失", INVALID_DISCOUNT: "优惠超额" },
    priceItemStatus: { INCLUDED: "已包含", NOT_INCLUDED: "不包含", UNKNOWN: "未知" },
  },
};

/** 构造最小可用的报价夹具；用例按需覆盖字段。 */
function makeQuote(overrides: Partial<Quote> = {}): Quote {
  return {
    id: 1,
    projectId: 10,
    insurerCode: "PINGAN",
    insurerName: "平安",
    agentName: "小王",
    planLabel: null,
    source: "MANUAL",
    status: "PENDING_CONFIRM",
    note: null,
    vehicleModel: "Model Y",
    vehicleSeats: 5,
    firstRegDate: "2022-05",
    isNev: true,
    commercialPremium: null,
    computedCommercialPremium: null,
    commercialStatus: "UNKNOWN",
    compulsoryPremium: 1045,
    compulsoryStatus: "INCLUDED",
    vehicleTax: 0,
    vehicleTaxStatus: "INCLUDED",
    packageTotal: null,
    computedPackageTotal: null,
    packageStatus: "UNKNOWN",
    otherFees: null,
    otherFeesStatus: "NOT_INCLUDED",
    officialTotal: 5785.14,
    officialTotalStatus: "INCLUDED",
    computedTotal: 3880.41,
    totalCheckStatus: "MISMATCH",
    netPayment: 5785.14,
    netPaymentStatus: "OK",
    vehicleConflict: { fields: [], firstRegDateDiffers: false, resolutionRequired: false },
    insurerConflict: null,
    qualityWarnings: [],
    files: [],
    coverages: [],
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

const parseStatusFixture = {
  taskId: 1,
  status: "SUCCEEDED",
  attempt: 1,
  error: null,
  fileCount: 0,
  quoteStatus: "PENDING_CONFIRM",
  planCount: null,
  startedAt: null,
  finishedAt: null,
};

beforeEach(() => {
  vi.mocked(loadDictionaries).mockResolvedValue(dict as never);
  // TASK-04：确认页会拉取解析任务状态（多方案占位提示）；默认单方案
  vi.spyOn(quotesApi, "getParseStatus").mockResolvedValue(parseStatusFixture as never);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  push.mockClear();
});

describe("报价确认页", () => {
  it("渲染固定 7 个 Tab 且可切换", async () => {
    vi.spyOn(quotesApi, "get").mockResolvedValue(makeQuote());
    render(<QuoteConfirmPage />);
    for (const label of ["价格", "基础车险", "附加险", "额外保障", "增值服务", "销售说明", "车辆信息"]) {
      expect(await screen.findByRole("tab", { name: label })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("tab", { name: "车辆信息" }));
    expect(await screen.findByLabelText("车型")).toBeInTheDocument();
  });

  it("价格 Tab：先置“已包含”再填写金额，保存提交“值 + INCLUDED”口径", async () => {
    vi.spyOn(quotesApi, "get").mockResolvedValue(makeQuote());
    vi.spyOn(quotesApi, "update").mockResolvedValue(
      makeQuote({ commercialPremium: 4392.14, commercialStatus: "INCLUDED" })
    );
    render(<QuoteConfirmPage />);

    // 状态为 UNKNOWN/不包含时金额输入禁用：先选择“已包含”再录入
    fireEvent.change(await screen.findByLabelText("商业险合计状态"), {
      target: { value: "INCLUDED" },
    });
    const input = screen.getByLabelText("商业险合计金额（元）");
    fireEvent.change(input, { target: { value: "4392.14" } });
    // 按钮带 aria-label“保存价格分项”（覆盖文本“保存价格”）
    fireEvent.click(screen.getByRole("button", { name: "保存价格分项" }));

    await waitFor(() =>
      expect(quotesApi.update).toHaveBeenCalledWith(1, {
        commercialPremium: "4392.14",
        commercialStatus: "INCLUDED",
      })
    );
  });

  /** 含未识别行的报价夹具：映射与丢弃用例共用。 */
  function unrecognizedQuote(): Quote {
    const unrecognizedRow = {
      id: 7,
      category: "UNRECOGNIZED",
      code: null,
      rawName: "神秘附加权益",
      rawValue: null,
      name: "神秘附加权益",
      status: "INCLUDED",
      coverageAmount: 50000,
      perSeatAmount: null,
      seatCount: null,
      sharedCoverage: null,
      premium: 66,
      multiplier: null,
      condition: null,
      description: null,
      confidenceLevel: "HIGH",
      sourceFileId: null,
      sourcePage: null,
      sourceText: null,
      editedByUser: true,
      amountRangeHint: null,
    };
    return makeQuote({ coverages: [unrecognizedRow as never] });
  }

  it("附加险 Tab：未识别保障映射到标准险种（PATCH 补码）", async () => {
    vi.spyOn(quotesApi, "get").mockResolvedValue(unrecognizedQuote());
    const updateCoverage = vi
      .spyOn(quotesApi, "updateCoverage")
      .mockResolvedValue(makeQuote({ coverages: [] }));

    render(<QuoteConfirmPage />);
    fireEvent.click(await screen.findByRole("tab", { name: "附加险" }));
    expect(await screen.findByText("神秘附加权益")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("映射神秘附加权益到标准险种"), {
      target: { value: "TP_NON_MEDICAL" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认映射神秘附加权益" }));
    await waitFor(() =>
      expect(updateCoverage).toHaveBeenCalledWith(1, 7, { code: "TP_NON_MEDICAL" })
    );
  });

  it("附加险 Tab：未识别保障可丢弃（两步确认后 DELETE）", async () => {
    vi.spyOn(quotesApi, "get").mockResolvedValue(unrecognizedQuote());
    const deleteCoverage = vi
      .spyOn(quotesApi, "deleteCoverage")
      .mockResolvedValue(makeQuote({ coverages: [] }));

    render(<QuoteConfirmPage />);
    fireEvent.click(await screen.findByRole("tab", { name: "附加险" }));
    expect(await screen.findByText("神秘附加权益")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "丢弃神秘附加权益" }));
    fireEvent.click(screen.getByRole("button", { name: "确认丢弃" }));
    await waitFor(() => expect(deleteCoverage).toHaveBeenCalledWith(1, 7));
  });

  it("车辆冲突未选择时确认按钮禁用；选择后携带 resolution 调用确认接口", async () => {
    vi.spyOn(quotesApi, "get").mockResolvedValue(
      makeQuote({
        vehicleModel: "汉EV",
        vehicleConflict: {
          fields: ["vehicleModel"],
          firstRegDateDiffers: false,
          resolutionRequired: true,
        },
      })
    );
    const confirmApi = vi
      .spyOn(quotesApi, "confirm")
      .mockResolvedValue(makeQuote({ status: "CONFIRMED" }));

    render(<QuoteConfirmPage />);
    const confirmButton = await screen.findByRole("button", { name: "确认无误，加入对比" });
    expect(confirmButton).toBeDisabled();
    expect(screen.getByText(/请先在“车辆信息”Tab 选择处理方式/)).toBeInTheDocument();

    // 到车辆信息 Tab 选择“以报价为准”
    fireEvent.click(screen.getByRole("tab", { name: "车辆信息" }));
    fireEvent.click(screen.getByRole("radio", { name: /以本报价为准（更新项目车辆摘要）/ }));
    expect(confirmButton).toBeEnabled();

    fireEvent.click(confirmButton);
    await waitFor(() =>
      expect(confirmApi).toHaveBeenCalledWith(1, {
        vehicleConflictResolution: "USE_QUOTE",
        insurerConflictResolution: null,
      })
    );
    await waitFor(() => expect(push).toHaveBeenCalledWith("/projects/10"));
  });

  it("确认接口返回价格分项缺失错误时展示中文提示", async () => {
    vi.spyOn(quotesApi, "get").mockResolvedValue(makeQuote());
    vi.spyOn(quotesApi, "confirm").mockRejectedValue(
      new Error("以下价格分项标记为已包含但缺少金额，请填写金额或改为“不包含/未知”：商业险")
    );

    render(<QuoteConfirmPage />);
    fireEvent.click(await screen.findByRole("button", { name: "确认无误，加入对比" }));
    expect(await screen.findByText(/以下价格分项标记为已包含但缺少金额/)).toBeInTheDocument();
  });
});
