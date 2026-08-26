/**
 * 组件测试：首页主路径（加载 → 项目卡片渲染 / 空状态引导）。
 * mock 统一 API 客户端，不访问网络。
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import HomePage from "@/app/page";
import { projectsApi } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("首页", () => {
  it("空列表时展示空状态引导与新建按钮", async () => {
    vi.spyOn(projectsApi, "list").mockResolvedValue([]);
    render(<HomePage />);
    // 骨架屏先出现
    expect(await screen.findByText("还没有续保项目")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /新建续保对比/ })).toHaveAttribute(
      "href",
      "/projects/new"
    );
  });

  it("有项目时渲染项目卡片（报价数与最低净支出）", async () => {
    vi.spyOn(projectsApi, "list").mockResolvedValue([
      {
        id: 1,
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
        createdAt: "2026-08-26T10:00:00Z",
        updatedAt: "2026-08-26T10:00:00Z",
        quoteCount: 8,
        minNetPayment: 5420,
      },
    ]);
    render(<HomePage />);
    expect(await screen.findByText("2026 车辆续保")).toBeInTheDocument();
    expect(screen.getByText("8 份报价 · 最低 ¥5,420.00")).toBeInTheDocument();
    // 卡片链接到详情页
    expect(
      screen.getByRole("link", { name: /2026 车辆续保/ }).closest("a")
    ).toHaveAttribute("href", "/projects/1");
  });

  it("加载失败时展示中文错误与重试入口", async () => {
    vi.spyOn(projectsApi, "list").mockRejectedValue(
      new Error("无法连接后端服务，请确认 API 已启动")
    );
    render(<HomePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接后端服务");
    expect(screen.getByRole("button", { name: /重试/ })).toBeInTheDocument();
  });
});

describe("waitFor 冒烟", () => {
  it("异步渲染稳定完成（防止悬空 promise 警告）", async () => {
    vi.spyOn(projectsApi, "list").mockResolvedValue([]);
    render(<HomePage />);
    await waitFor(() => expect(screen.getByText("我的续保项目")).toBeInTheDocument());
  });
});
