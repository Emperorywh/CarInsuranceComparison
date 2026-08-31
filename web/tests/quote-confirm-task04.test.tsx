/**
 * TASK-04 确认页扩展组件测试：
 * 置信度三档徽标、证据来源定位（点击跳转文件页）、质量集中提示、
 * 多方案待拆分占位、公司冲突二选一阻断与确认载荷。
 * mock 统一 API 客户端，不访问网络。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import QuoteConfirmPage from "@/app/quotes/[id]/confirm/page";
import { ConfidenceBadge } from "@/components/quote/confidence-badge";
import { EvidenceChip } from "@/components/quote/evidence-chip";
import { loadDictionaries, quotesApi, type Quote, type QuoteFile } from "@/lib/api";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh: vi.fn() }),
  useParams: () => ({ id: "1" }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    loadDictionaries: vi.fn(),
  };
});

const dict = {
  insurers: [{ code: "PINGAN", label: "平安" }],
  coverageCodes: [
    { code: "THIRD_PARTY_LIABILITY", label: "三者险", category: "CORE", rowSelectable: true },
  ],
  packageCoverageTypes: [{ code: "DRIVER_ACCIDENT", label: "驾乘意外" }],
  serviceTypes: [{ code: "ROAD_RESCUE", label: "道路救援" }],
  annotationKinds: [{ code: "HANDWRITTEN", label: "手写标注" }],
  discountTypes: [{ code: "CASH", label: "现金返现" }],
  packageUnits: [{ code: "CNY", label: "元" }],
  statusLabels: {
    quoteStatus: { PENDING_CONFIRM: "待确认" },
    netPaymentStatus: { OK: "正常" },
    priceItemStatus: { INCLUDED: "已包含" },
  },
};

const files: QuoteFile[] = [
  {
    id: 101,
    fileName: "条款.pdf",
    mime: "application/pdf",
    sizeBytes: 1024,
    pageCount: 2,
    rawUrl: "/api/files/101/raw?projectId=10",
  },
  {
    id: 102,
    fileName: "拍照.jpg",
    mime: "image/jpeg",
    sizeBytes: 1024,
    pageCount: 1,
    rawUrl: "/api/files/102/raw?projectId=10",
  },
];

function makeQuote(overrides: Partial<Quote> = {}): Quote {
  return {
    id: 1,
    projectId: 10,
    insurerCode: "PICC",
    insurerName: "人保",
    agentName: null,
    planLabel: null,
    source: "UPLOADED",
    status: "PENDING_CONFIRM",
    note: null,
    vehicleModel: "Model Y",
    vehicleSeats: 5,
    firstRegDate: "2022-05",
    isNev: true,
    commercialPremium: 4093.91,
    computedCommercialPremium: null,
    commercialStatus: "INCLUDED",
    compulsoryPremium: 1045,
    compulsoryStatus: "INCLUDED",
    vehicleTax: 0,
    vehicleTaxStatus: "INCLUDED",
    packageTotal: 348,
    computedPackageTotal: 348,
    packageStatus: "INCLUDED",
    otherFees: null,
    otherFeesStatus: "NOT_INCLUDED",
    officialTotal: 5486.91,
    officialTotalStatus: "INCLUDED",
    computedTotal: 5486.91,
    totalCheckStatus: "PASSED",
    netPayment: 5486.91,
    netPaymentStatus: "OK",
    vehicleConflict: { fields: [], firstRegDateDiffers: false, resolutionRequired: false },
    insurerConflict: null,
    qualityWarnings: [],
    files,
    coverages: [],
    services: [],
    packages: [],
    annotations: [],
    discounts: [],
    evidences: [],
    createdAt: "2026-08-30T10:00:00Z",
    updatedAt: "2026-08-30T10:00:00Z",
    ...overrides,
  } as Quote;
}

function parseStatus(planCount: number | null) {
  return {
    taskId: 1,
    status: "SUCCEEDED",
    attempt: 1,
    error: null,
    fileCount: 2,
    quoteStatus: "PENDING_CONFIRM",
    planCount,
    startedAt: null,
    finishedAt: null,
  };
}

beforeEach(() => {
  vi.mocked(loadDictionaries).mockResolvedValue(dict as never);
  vi.spyOn(quotesApi, "getParseStatus").mockResolvedValue(parseStatus(1) as never);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  push.mockClear();
});

// ---- 置信度徽标 ----

describe("ConfidenceBadge", () => {
  it("LOW 显示“请核对”，MEDIUM 显示“置信度中”", () => {
    render(<ConfidenceBadge level="LOW" editedByUser={false} />);
    expect(screen.getByText("请核对")).toBeInTheDocument();
    cleanup();
    render(<ConfidenceBadge level="MEDIUM" editedByUser={false} />);
    expect(screen.getByText("置信度中")).toBeInTheDocument();
  });

  it("用户编辑字段优先显示“用户已确认”，HIGH 无标记", () => {
    render(<ConfidenceBadge level="LOW" editedByUser />);
    expect(screen.getByText("用户已确认")).toBeInTheDocument();
    cleanup();
    const { container } = render(<ConfidenceBadge level="HIGH" editedByUser={false} />);
    expect(container).toBeEmptyDOMElement();
  });
});

// ---- 证据来源定位 ----

describe("EvidenceChip", () => {
  it("可定位时显示文件序号与页码，点击回调来源", () => {
    const onOpen = vi.fn();
    render(
      <EvidenceChip
        files={files}
        source={{ sourceFileId: 101, sourcePage: 2, sourceText: "商业险合计" }}
        onOpen={onOpen}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /查看来源：文件1 第2页/ }));
    expect(onOpen).toHaveBeenCalledWith({
      sourceFileId: 101,
      sourcePage: 2,
      sourceText: "商业险合计",
    });
  });

  it("无法定位文件时只展示摘录；完全无来源渲染 null", () => {
    render(
      <EvidenceChip
        files={[]}
        source={{ sourceFileId: 999, sourcePage: 1, sourceText: "摘录文本" }}
      />
    );
    expect(screen.getByText(/摘录：摘录文本/)).toBeInTheDocument();
    cleanup();
    const { container } = render(
      <EvidenceChip files={files} source={{ sourceFileId: null, sourcePage: null, sourceText: null }} />
    );
    expect(container).toBeEmptyDOMElement();
  });
});

// ---- 确认页扩展 ----

describe("确认页 TASK-04 扩展", () => {
  it("质量集中提示以警告形式置顶展示", async () => {
    vi.spyOn(quotesApi, "get").mockResolvedValue(
      makeQuote({ qualityWarnings: ["较多字段置信度低，请逐项核对原文后再确认"] })
    );
    render(<QuoteConfirmPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "较多字段置信度低，请逐项核对原文后再确认"
    );
  });

  it("同公司多方案（planCount=2）进入拆分确认卡片流（TASK-05 取代占位）", async () => {
    vi.spyOn(quotesApi, "getParseStatus").mockResolvedValue(parseStatus(2) as never);
    vi.spyOn(quotesApi, "get").mockResolvedValue(makeQuote({ coverages: [] }));
    vi.spyOn(quotesApi, "getPlanSplit").mockResolvedValue({
      quoteId: 1,
      taskId: 5,
      planCount: 2,
      insurerName: "人保",
      plans: [
        {
          index: 0,
          planLabel: "方案A",
          prices: { commercialPremium: { value: 2800, status: "INCLUDED" } },
          coreCoverages: [],
          additionalCoverages: [],
          packageSummaries: [],
          serviceSummaries: [],
          annotationCount: 0,
          unmatchedCount: 0,
        },
        {
          index: 1,
          planLabel: "方案B",
          prices: { commercialPremium: { value: 3200, status: "INCLUDED" } },
          coreCoverages: [],
          additionalCoverages: [],
          packageSummaries: [],
          serviceSummaries: [],
          annotationCount: 0,
          unmatchedCount: 0,
        },
      ],
    } as never);
    render(<QuoteConfirmPage />);
    expect(await screen.findByRole("region", { name: "多方案拆分确认" })).toBeInTheDocument();
    expect(screen.getByText(/识别到 2 个方案/)).toBeInTheDocument();
    // 拆分流取代常规确认按钮（容器报价无明细，直接确认被禁止）
    expect(screen.queryByRole("button", { name: "确认无误，加入对比" })).toBeNull();
  });

  it("公司冲突未选择时确认禁用；选择后载荷携带 insurerConflictResolution", async () => {
    vi.spyOn(quotesApi, "get").mockResolvedValue(
      makeQuote({
        insurerConflict: {
          modelName: "太平洋",
          modelCode: "CPIC",
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

    fireEvent.click(screen.getByRole("radio", { name: /采用识别结果（更新本报价的保险公司）/ }));
    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);
    await waitFor(() =>
      expect(confirmApi).toHaveBeenCalledWith(1, {
        vehicleConflictResolution: null,
        insurerConflictResolution: "USE_MODEL",
      })
    );
  });

  it("证据点击打开对应文件的全屏查看器", async () => {
    vi.spyOn(quotesApi, "get").mockResolvedValue(
      makeQuote({
        evidences: [
          {
            id: 1,
            fieldName: "officialTotal",
            rawValue: "5486.91",
            sourceFileId: 101,
            sourcePage: 2,
            sourceText: "合计 5,486.91元",
            confidenceLevel: "HIGH",
            editedByUser: false,
          },
        ],
      })
    );
    render(<QuoteConfirmPage />);
    // 价格 Tab 中官方总价的来源定位按钮
    const chip = await screen.findByRole("button", { name: /查看来源：文件1 第2页/ });
    fireEvent.click(chip);
    expect(await screen.findByRole("dialog", { name: /文件预览 条款.pdf/ })).toBeInTheDocument();
  });
});
