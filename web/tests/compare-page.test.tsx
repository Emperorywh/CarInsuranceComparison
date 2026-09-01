/**
 * TASK-06 页面测试：对比页（quoteIds 参数 → 请求 → 渲染）与
 * 项目详情的对比勾选/同公司筛选/开始对比。
 * mock 统一 API 客户端与路由，不访问网络。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProjectComparePage from "@/app/projects/[id]/compare/page";
import ProjectDetailPage from "@/app/projects/[id]/page";
import { projectsApi, type ProjectDetail } from "@/lib/api";
import { makeCompareResult } from "./compare-fixtures";

const push = vi.fn();
let searchParamValue: string | null = "101,102";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh: vi.fn() }),
  useParams: () => ({ id: "10" }),
  useSearchParams: () => ({
    get: (key: string) => (key === "quoteIds" ? searchParamValue : null),
  }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      compare: vi.fn(),
      get: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
    },
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  searchParamValue = "101,102";
});

// ---- 对比页 ----

describe("对比页", () => {
  it("按 quoteIds 请求并渲染单一对比总表、异常标注与免责声明", async () => {
    vi.mocked(projectsApi.compare).mockResolvedValue(makeCompareResult());
    render(<ProjectComparePage />);
    await waitFor(() =>
      expect(projectsApi.compare).toHaveBeenCalledWith(10, [101, 102])
    );
    // 单一总表已渲染，五问卡片不再出现
    expect(await screen.findByText("指标")).toBeInTheDocument();
    expect(screen.queryByText("哪个最便宜？")).not.toBeInTheDocument();
    // 免责声明与基准说明
    expect(screen.getByText(/本工具用于整理报价差异/)).toBeInTheDocument();
    expect(screen.getByText(/差异基准：/)).toBeInTheDocument();
  });

  it("两种基准不同时分别标注身份", async () => {
    const result = makeCompareResult();
    // 价格基准切换为 B：A 需同时摘除标记（服务端恒只有一个价格基准）
    result.priceBaselineQuoteId = 2;
    result.quotes[0].isPriceBaseline = false;
    result.quotes[1].isPriceBaseline = true;
    vi.mocked(projectsApi.compare).mockResolvedValue(result);
    render(<ProjectComparePage />);
    await screen.findByText("指标");
    expect(screen.getByText(/价格基准：/)).toBeInTheDocument();
    expect(screen.getByText(/（净支出最低）/)).toBeInTheDocument();
  });

  it("quoteIds 数量不足时渲染引导错误态且不发请求", async () => {
    searchParamValue = "101";
    render(<ProjectComparePage />);
    expect(await screen.findByText("对比参数不正确")).toBeInTheDocument();
    expect(projectsApi.compare).not.toHaveBeenCalled();
  });

  it("接口错误展示可重试的错误态", async () => {
    vi.mocked(projectsApi.compare).mockRejectedValue(
      new Error("仅已确认或合并确认中的报价可参与对比，请先完成确认")
    );
    render(<ProjectComparePage />);
    expect(
      await screen.findByText(/仅已确认或合并确认中的报价可参与对比/)
    ).toBeInTheDocument();
  });
});

// ---- 项目详情：勾选 + 筛选 + 开始对比 ----

function makeProject(overrides: Partial<ProjectDetail> = {}): ProjectDetail {
  const confirmed = (id: number, label: string) => ({
    id,
    insurerCode: "PICC",
    insurerName: "人保",
    agentName: null,
    planLabel: label,
    source: "MANUAL" as const,
    status: "CONFIRMED" as const,
    netPayment: 5000,
    netPaymentStatus: "OK" as const,
    officialTotal: 5500,
    computedTotal: 5500,
    totalCheckStatus: "PASSED" as const,
    thirdPartyAmount: 3000000,
    tpNonMedicalAmount: 500000,
    createdAt: "2026-08-31T00:00:00Z",
  });
  return {
    id: 10,
    name: "2026 车辆续保",
    vehicleName: "Model Y",
    renewalYear: 2026,
    expireDate: null,
    note: null,
    vehicleModel: null,
    vehicleSeats: null,
    firstRegDate: null,
    isNev: null,
    modelConsentAt: null,
    createdAt: "2026-08-31T00:00:00Z",
    updatedAt: "2026-08-31T00:00:00Z",
    quoteGroups: [
      {
        insurerCode: "PICC",
        insurerName: "人保",
        agentName: null,
        sameSourceHint: false,
        quotes: [confirmed(101, "方案A"), confirmed(102, "方案B")],
      },
      {
        insurerCode: "PINGAN",
        insurerName: "平安",
        agentName: null,
        sameSourceHint: false,
        quotes: [
          {
            id: 201,
            insurerCode: "PINGAN",
            insurerName: "平安",
            agentName: null,
            planLabel: "草稿",
            source: "UPLOADED",
            status: "DRAFT",
            netPayment: null,
            netPaymentStatus: "MISSING_TOTAL",
            officialTotal: null,
            computedTotal: null,
            totalCheckStatus: "NOT_CHECKABLE",
            thirdPartyAmount: null,
            tpNonMedicalAmount: null,
            createdAt: "2026-08-31T00:00:00Z",
          },
        ],
      },
    ],
    ...overrides,
  } as ProjectDetail;
}

describe("项目详情：对比勾选", () => {
  it("勾选两个报价后按勾选顺序跳转对比页", async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(makeProject());
    render(<ProjectDetailPage />);
    // 等待真正要交互的元素出现（报价卡片可能晚于项目标题一拍提交）
    await screen.findByRole("checkbox", { name: "勾选 方案A 加入对比" });
    fireEvent.click(screen.getByRole("checkbox", { name: "勾选 方案A 加入对比" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "勾选 方案B 加入对比" }));
    expect(screen.getByText("已选 2/6 个报价")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /开始对比/ }));
    expect(push).toHaveBeenCalledWith("/projects/10/compare?quoteIds=101,102");
  });

  it("不足两个报价时开始对比禁用；DRAFT 勾选禁用", async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(makeProject());
    render(<ProjectDetailPage />);
    await screen.findByRole("checkbox", { name: "勾选 草稿 加入对比" });
    expect(screen.getByRole("button", { name: /开始对比/ })).toBeDisabled();
    expect(
      screen.getByRole("checkbox", { name: "勾选 草稿 加入对比" })
    ).toBeDisabled();
  });

  it("勾选顺序即 URL 顺序：先 B 后 A 生成 102,101", async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(makeProject());
    render(<ProjectDetailPage />);
    await screen.findByRole("checkbox", { name: "勾选 方案B 加入对比" });
    fireEvent.click(screen.getByRole("checkbox", { name: "勾选 方案B 加入对比" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "勾选 方案A 加入对比" }));
    fireEvent.click(screen.getByRole("button", { name: /开始对比/ }));
    expect(push).toHaveBeenCalledWith("/projects/10/compare?quoteIds=102,101");
  });

  it("同公司筛选隐藏其他公司分组", async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(makeProject());
    render(<ProjectDetailPage />);
    await screen.findByLabelText("只看公司");
    fireEvent.change(screen.getByLabelText("只看公司"), {
      target: { value: "PINGAN" },
    });
    expect(screen.queryByText("勾选 方案A 加入对比")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "勾选 草稿 加入对比" })).toBeInTheDocument();
  });

  it("勾满 6 个后其余报价禁用勾选并提示分批", async () => {
    // 构造 8 份已确认报价：勾满 6 后其余禁用
    const confirmed = (id: number) => ({
      id,
      insurerCode: "PICC",
      insurerName: "人保",
      agentName: null,
      planLabel: `方案${id}`,
      source: "MANUAL" as const,
      status: "CONFIRMED" as const,
      netPayment: 5000,
      netPaymentStatus: "OK" as const,
      officialTotal: 5500,
      computedTotal: 5500,
      totalCheckStatus: "PASSED" as const,
      thirdPartyAmount: null,
      tpNonMedicalAmount: null,
      createdAt: "2026-08-31T00:00:00Z",
    });
    vi.mocked(projectsApi.get).mockResolvedValue(
      makeProject({
        quoteGroups: [
          {
            insurerCode: "PICC",
            insurerName: "人保",
            agentName: null,
            sameSourceHint: true,
            quotes: [201, 202, 203, 204, 205, 206, 207, 208].map(confirmed),
          },
        ],
      })
    );
    render(<ProjectDetailPage />);
    await screen.findByRole("checkbox", { name: "勾选 方案201 加入对比" });
    for (const id of [201, 202, 203, 204, 205, 206]) {
      fireEvent.click(
        screen.getByRole("checkbox", { name: `勾选 方案${id} 加入对比` })
      );
    }
    expect(screen.getByText("已选 6/6 个报价")).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "勾选 方案207 加入对比" })
    ).toBeDisabled();
    // 取消一个后恢复可勾选
    fireEvent.click(
      screen.getByRole("checkbox", { name: "勾选 方案206 加入对比" })
    );
    expect(
      screen.getByRole("checkbox", { name: "勾选 方案207 加入对比" })
    ).not.toBeDisabled();
  });
});
