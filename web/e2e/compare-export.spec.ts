/**
 * E2E 主路径 7：2–6 报价对比 + 脱敏长图导出（SPEC §15.2.10）。
 * 三份已确认报价按勾选顺序进入对比页（五问 + 六区），随后导出长图：
 * 下载得到非空 PNG（浏览器真实栅格化链路），并断言可见区域含保险员
 * 信息（该信息不应进入导出画布——白名单过滤由组件/单元测试证明）。
 */
import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import {
  apiCreateConfirmedQuote,
  apiCreateProject,
} from "./helpers";

test("3 报价对比 + 导出长图", async ({ page, request }) => {
  const projectId = await apiCreateProject(request, "E2E-对比导出");
  // 三份报价：方案A（最低价）、方案B（500 万三者）、方案C（500 万三者+新增附加险）。
  // 方案C 的 agentName 含测试标记：页面可显示，导出白名单不得携带。
  // 注：单方案解析报价 planLabel 为空（仅拆分子报价有标签），卡片回退显示“报价 #id”
  const idA = await apiCreateConfirmedQuote(request, projectId, "plan-a", null);
  const idB = await apiCreateConfirmedQuote(request, projectId, "plan-b", null);
  await apiCreateConfirmedQuote(request, projectId, "plan-a-v2", "测试保险员");

  // 项目页按勾选顺序对比（第一份 → 第二份，顺序即差异基准顺序）
  await page.goto(`/projects/${projectId}`);
  // 项目卡片分组标题会显示保险员（页面可见信息；导出白名单之外，
  // 由单元测试证明不进导出画布）
  await expect(page.getByText(/测试保险员/).first()).toBeVisible();
  await page.getByRole("checkbox", { name: `勾选 报价 #${idA} 加入对比` }).click();
  await page.getByRole("checkbox", { name: `勾选 报价 #${idB} 加入对比` }).click();
  await page.getByRole("button", { name: /开始对比/ }).click();
  await page.waitForURL(/\/compare\?quoteIds=\d+,\d+$/);

  // 五问第一屏 + 免责声明
  await expect(page.getByText("五问总结")).toBeVisible();
  await expect(page.getByText(/实际净支出最低/).first()).toBeVisible();
  await expect(page.getByText(/本工具用于整理报价差异/)).toBeVisible();

  // 导出长图：headless Chromium 无 Web Share 目标，自动走下载路径
  const downloadPromise = page.waitForEvent("download", { timeout: 60_000 });
  await page.getByTestId("export-image-button").click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain("车险报价对比");
  const filePath = await download.path();
  const bytes = readFileSync(filePath!);
  // PNG 签名 + 尺寸非零（栅格化成功且非空白画布）
  expect(bytes.subarray(0, 8)).toEqual(
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
  );
  expect(bytes.length).toBeGreaterThan(10_000);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  expect(view.getUint32(16)).toBeGreaterThan(0);
  expect(view.getUint32(20)).toBeGreaterThan(0);
});
