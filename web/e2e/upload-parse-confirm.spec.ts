/**
 * E2E 主路径 3：上传 → 解析 → 候选确认（SPEC §15.2.10）。
 * 首次上传必须先经过模型传输同意弹窗；解析完成进入候选确认页，
 * 字段带证据来源；确认后项目页出现已确认卡片。
 */
import { expect, test } from "@playwright/test";

import { apiCreateProject, writeFixture, fixturePayload, assetBuffer } from "./helpers";

test("上传解析确认：同意弹窗 → 轮询候选 → 确认", async ({ page, request }) => {
  // 假模型固定返回 plan-a 抽取结果（合成数据：商业险 3000 + 交强 950）
  writeFixture(fixturePayload("plan-a"));
  const projectId = await apiCreateProject(request, "E2E-上传解析确认");
  await page.goto(`/projects/${projectId}`);
  // 空状态与页头各有一个入口，取第一个即可
  await page.getByRole("link", { name: "添加报价" }).first().click();

  // 选公司（与 fixture 的识别结果一致，避免公司冲突阻断）
  await page.getByRole("radio", { name: "人保" }).click();
  await page
    .getByLabel("选择报价单文件")
    .setInputFiles({ name: "quote-page.png", mimeType: "image/png", buffer: assetBuffer("quote-page.png") });
  await page.getByRole("button", { name: /上传并自动识别/ }).click();

  // 首次解析前必须明确同意模型传输（SPEC §9.1）；拒绝路径由组件测试覆盖
  await expect(page.getByText("将报价单发送至视觉模型解析？")).toBeVisible();
  await page.getByRole("button", { name: "同意并开始上传" }).click();

  // 上传受理后进入详情页轮询；候选写入完成后状态变 PENDING_CONFIRM，
  // 此时操作区链接才从「编辑确认内容」（PARSING 期即可见）切换为「去确认报价」
  await page.waitForURL(/\/quotes\/\d+$/);
  await expect(page.getByRole("link", { name: "去确认报价" })).toBeVisible({
    timeout: 30_000,
  });
  await page.getByRole("link", { name: "去确认报价" }).click();
  await page.waitForURL(/\/quotes\/\d+\/confirm$/);

  // 候选数据已就绪：官方总价/净支出均在页面出现（价格摘要 + 底部净支出）
  await expect(page.getByText("¥3,950.00").first()).toBeVisible();

  // 确认 → 项目页出现已确认卡片（净支出 ¥3,950）
  await page.getByRole("button", { name: "确认无误，加入对比" }).click();
  await page.waitForURL(/\/projects\/\d+$/);
  await expect(page.getByText("¥3,950.00").first()).toBeVisible();
});
