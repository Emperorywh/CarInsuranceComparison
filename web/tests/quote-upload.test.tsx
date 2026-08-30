/**
 * 添加报价页上传交互测试（TASK-03）：文件选择过滤、首次模型传输同意、
 * 上传调用与跳转、拒绝同意后手动录入路径可用。mock 统一 API 客户端。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import NewQuotePage from "@/app/projects/[id]/quotes/new/page";
import {
  loadDictionaries,
  projectsApi,
  quotesApi,
  uploadQuoteFiles,
  type ProjectDetail,
} from "@/lib/api";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh: vi.fn() }),
  useParams: () => ({ id: "10" }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    loadDictionaries: vi.fn(),
    projectsApi: { ...actual.projectsApi, get: vi.fn() },
    quotesApi: { ...actual.quotesApi, create: vi.fn() },
    uploadQuoteFiles: vi.fn(),
  };
});

const dict = {
  insurers: [
    { code: "PICC", label: "人保" },
    { code: "PINGAN", label: "平安" },
    { code: "OTHER", label: "其他" },
  ],
  coverageCodes: [],
  packageCoverageTypes: [],
  serviceTypes: [],
  annotationKinds: [],
  discountTypes: [],
  packageUnits: [],
  statusLabels: {
    quoteStatus: {},
    netPaymentStatus: {},
    totalCheckStatus: {},
  },
};

function makeProject(modelConsentAt: string | null): ProjectDetail {
  return {
    id: 10,
    name: "续保项目",
    vehicleName: "Model Y",
    renewalYear: 2026,
    expireDate: null,
    note: null,
    vehicleModel: null,
    vehicleSeats: null,
    firstRegDate: null,
    isNev: null,
    modelConsentAt,
    createdAt: "2026-08-30T00:00:00Z",
    updatedAt: "2026-08-30T00:00:00Z",
    quoteGroups: [],
  } as unknown as ProjectDetail;
}

function makeQuote(quoteId: number) {
  return {
    id: quoteId,
    projectId: 10,
    insurerCode: "PICC",
    insurerName: "人保",
    agentName: null,
    planLabel: null,
    source: "UPLOADED",
    status: "PARSING",
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
    files: [],
    coverages: [],
    services: [],
    packages: [],
    annotations: [],
    discounts: [],
    evidences: [],
  } as unknown as import("@/lib/api").Quote;
}

function chooseFiles(names: string[]) {
  const files = names.map(
    (name) => new File(["content"], name, { type: "image/jpeg" })
  );
  const input = screen.getByLabelText("选择报价单文件") as HTMLInputElement;
  const list = {
    length: files.length,
    item: (i: number) => files[i],
    ...Object.fromEntries(files.map((f, i) => [i, f])),
    [Symbol.iterator]: function* () {
      yield* files;
    },
  } as unknown as FileList;
  Object.defineProperty(input, "files", { value: list, configurable: true });
  fireEvent.change(input);
}

beforeEach(() => {
  vi.clearAllMocks();
  (loadDictionaries as ReturnType<typeof vi.fn>).mockResolvedValue(dict);
});

afterEach(() => {
  cleanup();
});

describe("添加报价页：上传解析路径", () => {
  it("默认显示手动录入按钮；选择不支持的格式被拒绝", async () => {
    render(<NewQuotePage />);
    await screen.findByText("人保");

    expect(screen.getByRole("button", { name: /跳过上传，手动录入/ })).toBeTruthy();

    chooseFiles(["photo.heic"]);
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toContain("不是支持的格式");
    });
    // 非法文件不进入待上传列表，主按钮仍是手动录入
    expect(screen.getByRole("button", { name: /跳过上传，手动录入/ })).toBeTruthy();
  });

  it("选择合法文件后走 UPLOADED 容器创建 + 上传 + 跳转（已同意过，不弹窗）", async () => {
    (projectsApi.get as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeProject("2026-08-30T00:00:00Z")
    );
    (quotesApi.create as ReturnType<typeof vi.fn>).mockResolvedValue(makeQuote(66));
    (uploadQuoteFiles as ReturnType<typeof vi.fn>).mockResolvedValue({
      taskId: 900,
      quoteId: 66,
      files: [],
    });

    render(<NewQuotePage />);
    await screen.findByText("人保");
    fireEvent.click(screen.getByRole("radio", { name: "人保" }));

    chooseFiles(["quote1.jpg", "quote2.pdf"]);
    expect(screen.getByText("quote1.jpg")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /上传并自动识别/ }));

    await waitFor(() => {
      expect(quotesApi.create).toHaveBeenCalledWith(10, {
        insurerCode: "PICC",
        insurerName: null,
        agentName: null,
        source: "UPLOADED",
      });
    });
    await waitFor(() => {
      expect(uploadQuoteFiles).toHaveBeenCalledWith(
        66,
        expect.anything(),
        expect.objectContaining({ modelProcessingConsent: true })
      );
    });
    await waitFor(() => {
      expect(push).toHaveBeenCalledWith("/quotes/66");
    });
  });

  it("项目首次解析弹出模型传输同意框；同意后携带 consent 上传", async () => {
    (projectsApi.get as ReturnType<typeof vi.fn>).mockResolvedValue(makeProject(null));
    (quotesApi.create as ReturnType<typeof vi.fn>).mockResolvedValue(makeQuote(77));
    (uploadQuoteFiles as ReturnType<typeof vi.fn>).mockResolvedValue({
      taskId: 901,
      quoteId: 77,
      files: [],
    });

    render(<NewQuotePage />);
    await screen.findByText("人保");
    fireEvent.click(screen.getByRole("radio", { name: "人保" }));
    chooseFiles(["a.jpg"]);

    fireEvent.click(screen.getByRole("button", { name: /上传并自动识别/ }));

    // 同意弹窗出现，上传尚未发生
    await screen.findByText("将报价单发送至视觉模型解析？");
    expect(uploadQuoteFiles).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "同意并开始上传" }));
    await waitFor(() => {
      expect(uploadQuoteFiles).toHaveBeenCalledWith(
        77,
        expect.anything(),
        expect.objectContaining({ modelProcessingConsent: true })
      );
    });
    await waitFor(() => {
      expect(push).toHaveBeenCalledWith("/quotes/77");
    });
  });

  it("拒绝同意不上传；仍可手动录入（隐私边界）", async () => {
    (projectsApi.get as ReturnType<typeof vi.fn>).mockResolvedValue(makeProject(null));
    (quotesApi.create as ReturnType<typeof vi.fn>).mockResolvedValue(makeQuote(88));

    render(<NewQuotePage />);
    await screen.findByText("人保");
    fireEvent.click(screen.getByRole("radio", { name: "人保" }));
    chooseFiles(["a.jpg"]);
    fireEvent.click(screen.getByRole("button", { name: /上传并自动识别/ }));

    await screen.findByText("将报价单发送至视觉模型解析？");
    fireEvent.click(
      screen.getByRole("button", { name: /暂不同意/ })
    );
    await waitFor(() => {
      expect(screen.queryByText("将报价单发送至视觉模型解析？")).toBeNull();
    });
    expect(uploadQuoteFiles).not.toHaveBeenCalled();

    // 清空选择后走手动录入（或直接点手动按钮：文件仍在但按钮文案是上传）
    // 这里验证拒绝后的手动路径本身可用：重选项目同意状态无关
    (quotesApi.create as ReturnType<typeof vi.fn>).mockResolvedValue(makeQuote(89));
    // 移除文件回到手动入口
    fireEvent.click(screen.getByRole("button", { name: /移除 a\.jpg/ }));
    fireEvent.click(screen.getByRole("button", { name: /跳过上传，手动录入/ }));
    await waitFor(() => {
      expect(quotesApi.create).toHaveBeenCalledWith(
        10,
        expect.objectContaining({ source: "MANUAL" })
      );
    });
    expect(uploadQuoteFiles).not.toHaveBeenCalled();
  });
});
