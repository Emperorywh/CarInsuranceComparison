/**
 * 项目详情页组件测试：重点验证删除的二次确认流程与编辑入口。
 * mock 路由与 API 客户端，不访问网络。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProjectDetailPage from "@/app/projects/[id]/page";
import { projectsApi } from "@/lib/api";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "1" }),
  useRouter: () => ({ push, refresh: vi.fn() }),
}));

const project = {
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
  // TASK-02 起项目详情附带分组报价卡数据；空项目为稳定空数组
  quoteGroups: [],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  push.mockClear();
});

describe("项目详情页", () => {
  it("加载并展示项目信息与报价空状态", async () => {
    vi.spyOn(projectsApi, "get").mockResolvedValue(project);
    render(<ProjectDetailPage />);
    expect(await screen.findByText("2026 车辆续保")).toBeInTheDocument();
    expect(screen.getByText("Model Y")).toBeInTheDocument();
    expect(screen.getByText("还没有报价")).toBeInTheDocument();
  });

  it("删除必须经过二次确认：取消不删除，确认才调用接口并返回首页", async () => {
    vi.spyOn(projectsApi, "get").mockResolvedValue(project);
    const remove = vi
      .spyOn(projectsApi, "remove")
      .mockResolvedValue(undefined as never);
    render(<ProjectDetailPage />);
    await screen.findByText("2026 车辆续保");

    // 打开确认弹层
    fireEvent.click(screen.getByRole("button", { name: /删除项目/ }));
    expect(
      await screen.findByRole("alertdialog", { name: "确定删除这个项目？" })
    ).toBeInTheDocument();

    // 取消：不触发删除，弹层关闭
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
    );
    expect(remove).not.toHaveBeenCalled();

    // 再次打开并确认：调用删除接口后跳回首页
    fireEvent.click(screen.getByRole("button", { name: /删除项目/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确认删除/ }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith(1));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
  });

  it("项目不存在（404）时展示引导返回", async () => {
    vi.spyOn(projectsApi, "get").mockRejectedValue(
      new Error("项目不存在或已被删除")
    );
    render(<ProjectDetailPage />);
    expect(await screen.findByText("项目不存在或已被删除")).toBeInTheDocument();
  });
});
