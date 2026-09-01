/**
 * TASK-07 长图导出测试：白名单 view model、导出画布、栅格化与分发。
 *
 * 核心隐私断言：CompareResult 中的保险员姓名（agentName="小王"）等
 * 白名单外字段不得出现在 view model、待栅格化 DOM 或任何导出产物中；
 * 下载/分享两条路径与超长画布的像素预算缩放各有覆盖。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExportCanvas } from "@/components/compare/export-canvas";
import { ExportCompareButton } from "@/components/compare/export-image-button";
import {
  exportFileName,
  rasterizeExportNode,
  readPngSize,
  shareOrDownload,
} from "@/lib/export-image";
import { buildExportViewModel } from "@/lib/export-model";
import { makeCompareResult } from "./compare-fixtures";

// html-to-image 打桩：不真正栅格化，返回带最小 PNG 头的 Blob
function fakePngBytes(width: number, height: number): Uint8Array {
  // PNG 签名 8 字节 + IHDR 长度/类型 8 字节 + 宽高各 4 字节；
  // 尾部追加填充字节，避免触发组件“dataUrl 过小=空白画布”防御分支
  const bytes = new Uint8Array(2048);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width);
  view.setUint32(20, height);
  for (let i = 24; i < bytes.length; i += 1) bytes[i] = i % 251;
  return bytes;
}

const toBlobMock = vi.hoisted(() => vi.fn());
vi.mock("html-to-image", () => ({ toBlob: toBlobMock }));

function mockToBlob(width = 750, height = 2000) {
  toBlobMock.mockResolvedValue(
    new Blob([fakePngBytes(width, height) as BlobPart], { type: "image/png" })
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  // 清理测试中注入的 navigator 分享能力，避免影响其他用例
  Reflect.deleteProperty(navigator, "canShare");
  Reflect.deleteProperty(navigator, "share");
});

describe("buildExportViewModel 白名单过滤", () => {
  it("只包含展示名/公司/价格/保障差异，不包含保险员姓名等白名单外字段", () => {
    const result = makeCompareResult(); // 方案B.agentName = "小王"
    const vm = buildExportViewModel(result);
    const serialized = JSON.stringify(vm);
    expect(serialized).not.toContain("小王");
    expect(serialized).not.toContain("agentName");
    // 白名单内容完整：方案名、公司、价格、免责声明都在
    expect(vm.plans.map((plan) => plan.displayName)).toEqual(["方案A", "方案B"]);
    expect(vm.plans.map((plan) => plan.insurerName)).toEqual(["人保", "平安"]);
    expect(vm.plans.map((plan) => plan.netPayment)).toEqual([5000, 5300]);
    expect(vm.plans[1]?.annotations).toEqual(["含用户估值"]);
    expect(vm.disclaimer).toContain("本工具用于整理报价差异");
  });

  it("总表行与页面同源同序，差异行带标签、相同行也保留", () => {
    const vm = buildExportViewModel(makeCompareResult());
    expect(vm.rows.map((row) => row.label)).toEqual([
      "实际净支出",
      "官方总价",
      "商业险",
      "三者险·保额",
      "车损险·保额",
      "道路救援",
      "计入折现合计",
    ]);
    // 三者保额差异行：UP 标签 + 行级 diff 标记（长图高亮用）
    const tp = vm.rows[3];
    expect(tp?.diff).toBe(true);
    expect(tp?.cells.map((cell) => cell.text)).toEqual(["300 万", "500 万"]);
    expect(tp?.cells[1]?.tag).toBe("UP");
    // 相同行（道路救援）不折叠，长图完整呈现
    const rescue = vm.rows[5];
    expect(rescue?.diff).toBe(false);
    expect(rescue?.cells[1]?.tag).toBe("SAME");
  });
});

describe("ExportCanvas 待栅格化 DOM", () => {
  it("渲染单一总表（方案表头 + 全部指标行）与免责声明，不含敏感字段", () => {
    const vm = buildExportViewModel(makeCompareResult());
    const ref = createRef<HTMLDivElement>();
    const { container } = render(<ExportCanvas data={vm} containerRef={ref} />);
    const node = container.querySelector('[data-testid="export-canvas"]');
    expect(node).not.toBeNull();
    const text = node?.textContent ?? "";
    expect(text).toContain("车险报价对比");
    expect(text).toContain("指标");
    expect(text).toContain("实际净支出");
    expect(text).toContain("500 万");
    expect(text).toContain("道路救援");
    expect(text).toContain("本工具用于整理报价差异");
    // 长图与页面同构：不再有五问/核心差异等独立板块
    expect(text).not.toContain("五问总结");
    expect(text).not.toContain("核心差异");
    // 隐私断言：白名单外字段（保险员姓名）不得进入待栅格化节点
    expect(text).not.toContain("小王");
  });
});

describe("shareOrDownload 分发路径", () => {
  it("不支持 Web Share 时走 a[download] 下载并回收 objectURL", async () => {
    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    URL.createObjectURL = createObjectURL as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = revokeObjectURL as unknown as typeof URL.revokeObjectURL;
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const blob = new Blob([fakePngBytes(10, 10) as BlobPart], { type: "image/png" });

    const outcome = await shareOrDownload(blob, "测试.png", "车险报价对比");

    expect(outcome).toBe("downloaded");
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock");
    clickSpy.mockRestore();
  });

  it("支持 Web Share 时优先 share 且不触发下载；取消分享不算失败", async () => {
    const share = vi.fn(async () => {});
    Object.defineProperty(navigator, "canShare", {
      value: vi.fn(() => true),
      configurable: true,
    });
    Object.defineProperty(navigator, "share", { value: share, configurable: true });
    const createObjectURL = vi.fn(() => "blob:mock");
    URL.createObjectURL = createObjectURL as unknown as typeof URL.createObjectURL;
    const blob = new Blob([fakePngBytes(10, 10) as BlobPart], { type: "image/png" });

    const outcome = await shareOrDownload(blob, "测试.png", "车险报价对比");
    expect(outcome).toBe("shared");
    expect(share).toHaveBeenCalledTimes(1);
    expect(createObjectURL).not.toHaveBeenCalled();

    // 用户关闭分享面板（AbortError）仍视为已完成，不退回下载
    share.mockRejectedValueOnce(new DOMException("abort", "AbortError"));
    await expect(shareOrDownload(blob, "测试.png", "车险报价对比")).resolves.toBe("shared");

    // 其他分享错误退回下载路径
    share.mockRejectedValueOnce(new Error("network"));
    const outcome2 = await shareOrDownload(blob, "测试.png", "车险报价对比");
    expect(outcome2).toBe("downloaded");
    expect(createObjectURL).toHaveBeenCalledTimes(1);
  });
});

describe("rasterizeExportNode 栅格化", () => {
  function makeNode(pixelsWide: number, pixelsHigh: number): HTMLElement {
    const node = document.createElement("div");
    vi.spyOn(node, "getBoundingClientRect").mockReturnValue({
      width: pixelsWide,
      height: pixelsHigh,
      top: 0,
      left: 0,
      bottom: pixelsHigh,
      right: pixelsWide,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    document.body.appendChild(node);
    return node;
  }

  it("常规内容使用 2x 高清倍率，返回 blob/dataUrl 与 PNG 尺寸", async () => {
    mockToBlob(1500, 4000);
    const node = makeNode(750, 2000); // 1.5M 像素，预算充足
    const result = await rasterizeExportNode(node, "测试.png");
    expect(toBlobMock).toHaveBeenCalledWith(node, expect.objectContaining({ pixelRatio: 2 }));
    expect(result.width).toBe(1500);
    expect(result.height).toBe(4000);
    expect(result.dataUrl).toMatch(/^data:image\/png;base64,/);
    node.remove();
  });

  it("超长画布按像素预算下调倍率（不低于 1x），避免移动端空白/裁切", async () => {
    mockToBlob(3000, 40000);
    // 120M 像素 > 16M 预算 → sqrt(16e6/120e6) ≈ 0.365 → 收敛到 1x
    const node = makeNode(1500, 80000);
    await rasterizeExportNode(node, "测试.png");
    expect(toBlobMock).toHaveBeenCalledWith(
      node,
      expect.objectContaining({ pixelRatio: 1 })
    );
    node.remove();
  });

  it("画布尺寸异常（0 像素）时抛出可读错误", async () => {
    mockToBlob(0, 0);
    const node = makeNode(750, 2000);
    await expect(rasterizeExportNode(node, "测试.png")).rejects.toThrow("画布尺寸异常");
    node.remove();
  });

  it("readPngSize 解析 PNG IHDR 宽高", () => {
    expect(readPngSize(fakePngBytes(640, 1280))).toEqual({ width: 640, height: 1280 });
  });

  it("exportFileName 只含日期与固定词，不携带用户数据", () => {
    const name = exportFileName(new Date("2026-08-31T09:05:00"));
    expect(name).toBe("车险报价对比-20260831-0905.png");
    expect(name).not.toContain("小王");
  });
});

describe("ExportCompareButton 集成", () => {
  it("点击后挂载画布并完成栅格化与下载；传给图像库的节点不含敏感字段", async () => {
    mockToBlob(750, 3000);
    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    URL.createObjectURL = createObjectURL as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = revokeObjectURL as unknown as typeof URL.revokeObjectURL;
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    render(<ExportCompareButton result={makeCompareResult()} />);
    // 点击前画布不挂载（避免在页面 DOM 常驻隐藏长表）
    expect(
      document.querySelector('[data-testid="export-canvas"]')
    ).toBeNull();

    fireEvent.click(screen.getByTestId("export-image-button"));
    await waitFor(() => expect(toBlobMock).toHaveBeenCalled());
    // 隐私断言：传给图像库的待栅格化节点是白名单画布，不含保险员字段
    const rasterizedNode = toBlobMock.mock.calls[0]?.[0] as HTMLElement;
    expect(rasterizedNode.dataset.testid).toBe("export-canvas");
    expect(rasterizedNode.textContent).not.toContain("小王");

    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // 栅格化完成后 working 复位是异步渲染：等按钮恢复「导出长图」再断言
    await waitFor(() =>
      expect(screen.getByText("导出长图")).toBeInTheDocument()
    );
    clickSpy.mockRestore();
  });

  it("栅格化失败时显示中文错误且可再次尝试", async () => {
    toBlobMock.mockRejectedValueOnce(new Error("导出失败：无法生成图片"));
    render(<ExportCompareButton result={makeCompareResult()} />);
    fireEvent.click(screen.getByTestId("export-image-button"));
    expect(await screen.findByRole("alert")).toHaveTextContent("导出失败：无法生成图片");
    // 失败后画布卸载，按钮恢复可用
    await waitFor(() =>
      expect(document.querySelector('[data-testid="export-canvas"]')).toBeNull()
    );
    expect(screen.getByTestId("export-image-button")).not.toBeDisabled();

    // 第二次成功路径：不再显示错误
    mockToBlob();
    const createObjectURL = vi.fn(() => "blob:mock");
    URL.createObjectURL = createObjectURL as unknown as typeof URL.createObjectURL;
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    fireEvent.click(screen.getByTestId("export-image-button"));
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    clickSpy.mockRestore();
  });
});
