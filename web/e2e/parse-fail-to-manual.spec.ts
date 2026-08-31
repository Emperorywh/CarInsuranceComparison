/**
 * E2E 主路径 6：模型最终失败后转手动录入（SPEC §15.2.10）。
 * 假模型固定失败 → 走完 3 次产品内置重试 → PARSE_FAILED →
 * 「转手动录入」保留已上传文件进入 PENDING_CONFIRM。
 */
import { expect, test } from "@playwright/test";

import { apiCreateProject, apiUploadFiles, writeFailFixture } from "./helpers";

test("解析失败转手动：重试耗尽 → 转手动 → 确认", async ({ page, request }) => {
  writeFailFixture();
  const projectId = await apiCreateProject(request, "E2E-失败转手动");
  const quoteId = await apiUploadFiles(request, projectId, [
    { asset: "quote-page.png", fileName: "quote-page.png", mimeType: "image/png" },
  ]);

  await page.goto(`/quotes/${quoteId}`);

  // 3 次尝试全部失败后进入 PARSE_FAILED：显示脱敏错误与两个出口
  await expect(page.getByLabel("解析失败信息")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/模型服务暂时不可用/)).toBeVisible();

  // 转手动：保留文件，报价进入 PENDING_CONFIRM（非 PARSE_FAILED 状态）
  await page.getByRole("button", { name: "转手动录入" }).click();
  await expect(page.getByRole("link", { name: /去确认报价/ })).toBeVisible();

  // 转手动后仍能走完整确认（此处仅验证状态迁移，填写路径见手动报价用例）
  await page.getByRole("link", { name: /去确认报价/ }).click();
  await page.waitForURL(/\/quotes\/\d+\/confirm$/);
  await expect(page.getByRole("tab", { name: "价格" })).toHaveAttribute(
    "aria-selected",
    "true"
  );
});
