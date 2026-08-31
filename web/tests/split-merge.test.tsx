/**
 * TASK-05 组件测试：多方案拆分卡片流、MERGE_REVIEW 变更清单、
 * 已确认报价的解析状态提示（进度/失败不遮挡旧数据）。
 * mock 统一 API 客户端，不访问网络。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PlanSplitFlow } from "@/components/quote/plan-split-flow";
import { MergeReviewList } from "@/components/quote/merge-review-list";
import { ParseStatusPanel } from "@/components/files/parse-status-panel";
import { quotesApi, type MergePreview, type ParseStatus, type Quote } from "@/lib/api";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh: vi.fn() }),
  useParams: () => ({ id: "1" }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    quotesApi: {
      ...actual.quotesApi,
      getPlanSplit: vi.fn(),
      confirmPlanSplit: vi.fn(),
      getMergePreview: vi.fn(),
      resolveMerge: vi.fn(),
      getParseStatus: vi.fn(),
      get: vi.fn(),
      reparse: vi.fn(),
    },
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

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
    vehicleModel: null,
    vehicleSeats: null,
    firstRegDate: null,
    isNev: null,
    commercialPremium: null,
    computedCommercialPremium: null,
    commercialStatus: "UNKNOWN",
    compulsoryPremium: null,
    compulsoryStatus: "UNKNOWN",
    vehicleTax: null,
    vehicleTaxStatus: "UNKNOWN",
    packageTotal: null,
    computedPackageTotal: null,
    packageStatus: "UNKNOWN",
    otherFees: null,
    otherFeesStatus: "UNKNOWN",
    officialTotal: null,
    officialTotalStatus: "UNKNOWN",
    computedTotal: null,
    totalCheckStatus: "NOT_CHECKABLE",
    netPayment: null,
    netPaymentStatus: "MISSING_TOTAL",
    vehicleConflict: null,
    insurerConflict: null,
    qualityWarnings: [],
    files: [],
    coverages: [],
    services: [],
    packages: [],
    annotations: [],
    discounts: [],
    evidences: [],
    createdAt: "2026-08-31T10:00:00Z",
    updatedAt: "2026-08-31T10:00:00Z",
    ...overrides,
  } as Quote;
}

const planSplitPreview = {
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
      packageSummaries: ["车主保障 348元"],
      serviceSummaries: ["道路救援"],
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
      unmatchedCount: 1,
    },
  ],
};

describe("PlanSplitFlow：拆分确认卡片流", () => {
  it("渲染各方案价格摘要；改标签并丢弃一个方案后按保留项提交", async () => {
    (quotesApi.getPlanSplit as ReturnType<typeof vi.fn>).mockResolvedValue(planSplitPreview);
    (quotesApi.confirmPlanSplit as ReturnType<typeof vi.fn>).mockResolvedValue({
      quotes: [{ id: 11, planLabel: "低配方案" }],
    });

    render(<PlanSplitFlow quote={makeQuote()} />);

    // 方案摘要展示（价格与保障包）
    expect(await screen.findByText(/保障包：车主保障 348元/)).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox", { name: /保留 方案/ })).toHaveLength(2);

    // 修改方案 A 标签
    const labelInputs = screen.getAllByRole("textbox", { name: "方案标签" });
    fireEvent.change(labelInputs[0], { target: { value: "低配方案" } });

    // 丢弃方案 B：仅剩一个可提交
    fireEvent.click(screen.getByRole("checkbox", { name: "保留 方案B" }));
    const submit = screen.getByRole("button", { name: /确认拆分，创建 1 份报价/ });
    fireEvent.click(submit);

    await waitFor(() => {
      expect(quotesApi.confirmPlanSplit).toHaveBeenCalledWith(1, {
        plans: [{ index: 0, planLabel: "低配方案" }],
      });
    });
    // 容器已删除：拆分成功后跳转项目页
    await waitFor(() => expect(push).toHaveBeenCalledWith("/projects/10"));
  });

  it("全部丢弃时按钮禁用；接口 422 展示中文错误", async () => {
    (quotesApi.getPlanSplit as ReturnType<typeof vi.fn>).mockResolvedValue(planSplitPreview);
    render(<PlanSplitFlow quote={makeQuote()} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "保留 方案A" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "保留 方案B" }));
    expect(screen.getByRole("button", { name: "请至少保留一个方案" })).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "保留 方案A" }));
    (quotesApi.confirmPlanSplit as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("方案序号 0 不存在，请刷新拆分预览后重试")
    );
    fireEvent.click(screen.getByRole("button", { name: /确认拆分，创建 1 份报价/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("方案序号 0 不存在");
  });
});

const files = [
  {
    id: 101,
    fileName: "原单.jpg",
    mime: "image/jpeg",
    sizeBytes: 1,
    pageCount: 1,
    rawUrl: "/api/files/101/raw?projectId=10",
  },
];

const mergePreview: MergePreview = {
  quoteId: 1,
  quoteStatus: "MERGE_REVIEW",
  taskId: 9,
  pendingCount: 3,
  changes: [
    {
      id: 1,
      entityType: "scalar",
      entityKey: "commercialPremium",
      entityLabel: "商业险",
      fieldName: "commercialPremium",
      kind: "CONFLICT",
      oldValue: { value: 4093.91, status: "INCLUDED" },
      newValue: { value: 4500, status: "INCLUDED", sourceFileId: 101, sourcePage: 1, sourceText: "商业险合计" },
      sourceFileId: 101,
      sourcePage: 1,
      sourceText: "商业险合计",
      userEdited: true,
      resolution: "PENDING",
      defaultResolution: "KEEP",
    },
    {
      id: 2,
      entityType: "coverage",
      entityKey: "THIRD_PARTY_LIABILITY",
      entityLabel: "三者险",
      fieldName: "coverageAmount",
      kind: "CONFLICT",
      oldValue: 3000000,
      newValue: { value: 5000000, sourceFileId: 101, sourcePage: 1, sourceText: "三者 500万" },
      sourceFileId: 101,
      sourcePage: 1,
      sourceText: "三者 500万",
      userEdited: false,
      resolution: "PENDING",
      defaultResolution: "ACCEPT",
    },
    {
      id: 3,
      entityType: "coverage",
      entityKey: "SCRATCH",
      entityLabel: "车身划痕",
      fieldName: "__row__",
      kind: "ADD",
      oldValue: null,
      newValue: {
        code: "SCRATCH",
        category: "ADDITIONAL",
        rawName: "附加车身划痕损失",
        name: "车身划痕",
        status: "INCLUDED",
        premium: 140,
      },
      sourceFileId: null,
      sourcePage: null,
      sourceText: null,
      userEdited: false,
      resolution: "PENDING",
      defaultResolution: "ACCEPT",
    },
  ],
};

describe("MergeReviewList：合并变更清单", () => {
  it("展示旧值/新值/来源/用户编辑标识，用户编辑项默认保留旧值", async () => {
    (quotesApi.getMergePreview as ReturnType<typeof vi.fn>).mockResolvedValue(mergePreview);
    render(<MergeReviewList quote={makeQuote({ status: "MERGE_REVIEW" })} files={files} onResolved={vi.fn()} />);

    expect(await screen.findByText(/共 3 项待确认变更/)).toBeInTheDocument();
    // 商业险（第一组）：用户已编辑 → “保留旧值”默认被预选
    const keepRadios = screen.getAllByRole("radio", { name: "保留旧值" }) as HTMLInputElement[];
    expect(keepRadios[0].checked).toBe(true);
    // 三者保额（第二组）：未被编辑 → “采纳新值”默认被预选
    const acceptRadios = screen.getAllByRole("radio", { name: "采纳新值" }) as HTMLInputElement[];
    expect(acceptRadios[1].checked).toBe(true);
    expect(screen.getByText("用户已编辑")).toBeInTheDocument();
    // 三者保额旧值 300万 → 新值 500万（万元格式化）
    expect(screen.getByText(/500 万/)).toBeInTheDocument();
    // 来源定位显示文件序号与摘录
    expect(screen.getAllByText(/来源：文件 1 · 第 1 页/)[0]).toBeInTheDocument();
    // 新增行摘要
    expect(screen.getByText(/车身划痕（保费 ¥140\.00，已包含）/)).toBeInTheDocument();
  });

  it("全部裁决后提交 resolveMerge，并以上一状态刷新报价", async () => {
    (quotesApi.getMergePreview as ReturnType<typeof vi.fn>).mockResolvedValue(mergePreview);
    (quotesApi.resolveMerge as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeQuote({ status: "CONFIRMED" })
    );
    const onResolved = vi.fn();
    render(
      <MergeReviewList
        quote={makeQuote({ status: "MERGE_REVIEW" })}
        files={files}
        onResolved={onResolved}
      />
    );

    await screen.findByText(/共 3 项待确认变更/);
    // 把三者保额改为保留旧值（默认是采纳）
    const radios = screen.getAllByRole("radio", { name: "保留旧值" });
    fireEvent.click(radios[1]);
    fireEvent.click(screen.getByRole("button", { name: /完成合并（采纳 1 \/ 保留 2）/ }));

    await waitFor(() => {
      expect(quotesApi.resolveMerge).toHaveBeenCalledWith(1, [
        { changeId: 1, resolution: "KEEP" },
        { changeId: 2, resolution: "KEEP" },
        { changeId: 3, resolution: "ACCEPT" },
      ]);
      expect(onResolved).toHaveBeenCalledWith(expect.objectContaining({ status: "CONFIRMED" }));
    });
  });

  it("resolve 失败（如仍有未裁决项）展示中文错误且不清空清单", async () => {
    (quotesApi.getMergePreview as ReturnType<typeof vi.fn>).mockResolvedValue(mergePreview);
    (quotesApi.resolveMerge as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("还有 1 项变更未裁决，请逐项选择“采纳新值”或“保留旧值”")
    );
    render(
      <MergeReviewList
        quote={makeQuote({ status: "MERGE_REVIEW" })}
        files={files}
        onResolved={vi.fn()}
      />
    );

    fireEvent.click(await screen.findByRole("button", { name: /完成合并/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("还有 1 项变更未裁决");
    expect(screen.getByText(/共 3 项待确认变更/)).toBeInTheDocument();
  });
});

describe("ParseStatusPanel：已确认报价的补传解析提示（TASK-05）", () => {
  function confirmedQuote(): Quote {
    return makeQuote({ status: "CONFIRMED" });
  }

  it("CONFIRMED 且任务运行中：显示非阻断进度提示，不遮挡旧数据", async () => {
    const running: ParseStatus = {
      taskId: 7,
      status: "RUNNING",
      attempt: 1,
      error: null,
      fileCount: 1,
      quoteStatus: "CONFIRMED",
      planCount: null,
      startedAt: null,
      finishedAt: null,
    };
    (quotesApi.getParseStatus as ReturnType<typeof vi.fn>).mockResolvedValue(running);

    render(<ParseStatusPanel quoteId={1} status="CONFIRMED" onQuoteChange={vi.fn()} pollIntervalMs={10} />);
    expect(await screen.findByLabelText("解析进度")).toBeTruthy();
    expect(screen.getByText(/正在解析报价单/)).toBeTruthy();

    // 终态 SUCCEEDED：刷新报价（可能进入 MERGE_REVIEW）
    (quotesApi.getParseStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...running,
      status: "SUCCEEDED",
    });
    (quotesApi.get as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeQuote({ status: "MERGE_REVIEW" })
    );
    const onQuoteChange = vi.fn();
    cleanup();
    render(
      <ParseStatusPanel
        quoteId={1}
        status="CONFIRMED"
        onQuoteChange={onQuoteChange}
        pollIntervalMs={10}
      />
    );
    await waitFor(() => {
      expect(onQuoteChange).toHaveBeenCalledWith(
        expect.objectContaining({ status: "MERGE_REVIEW" })
      );
    });
  });

  it("CONFIRMED 且补传解析失败：显示失败提示与重试，旧数据不受影响", async () => {
    const failed: ParseStatus = {
      taskId: 8,
      status: "FAILED",
      attempt: 1,
      error: "视觉模型鉴权失败（HTTP 401），请检查 VISION_API_KEY 配置",
      fileCount: 1,
      quoteStatus: "CONFIRMED",
      planCount: null,
      startedAt: null,
      finishedAt: null,
    };
    (quotesApi.getParseStatus as ReturnType<typeof vi.fn>).mockResolvedValue(failed);
    (quotesApi.reparse as ReturnType<typeof vi.fn>).mockResolvedValue({ taskId: 9, quoteId: 1 });
    (quotesApi.get as ReturnType<typeof vi.fn>).mockResolvedValue(confirmedQuote());

    render(<ParseStatusPanel quoteId={1} status="CONFIRMED" onQuoteChange={vi.fn()} />);
    expect(
      await screen.findByText(/本次补传\/重解析失败，已确认数据不受影响/)
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重试解析/ }));
    await waitFor(() => {
      expect(quotesApi.reparse).toHaveBeenCalledWith(1);
    });
  });

  it("CONFIRMED 且从未补传（无任务 404）：不渲染任何提示", async () => {
    (quotesApi.getParseStatus as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("该报价暂无解析任务")
    );
    render(<ParseStatusPanel quoteId={1} status="CONFIRMED" onQuoteChange={vi.fn()} />);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByLabelText("解析进度")).toBeNull();
    expect(screen.queryByLabelText("补传解析失败提示")).toBeNull();
    // 只探测一次，不无限轮询
    expect(quotesApi.getParseStatus).toHaveBeenCalledTimes(1);
  });

  it("MERGE_REVIEW：提示前往确认页处理合并变更", () => {
    render(<ParseStatusPanel quoteId={1} status="MERGE_REVIEW" onQuoteChange={vi.fn()} />);
    expect(screen.getByLabelText("合并确认提示")).toBeTruthy();
    expect(screen.getByText(/有待确认的合并变更/)).toBeTruthy();
  });
});
