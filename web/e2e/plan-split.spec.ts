/**
 * E2E 主路径 4：多方案拆分（SPEC §15.2.10）。
 * 同公司 planCount=2 的批次先进入拆分卡片流：改写方案标签、确认拆分 →
 * 创建平级子报价（共享原文件），容器报价删除。
 */
import { expect, test } from "@playwright/test";

import {
  apiCreateProject,
  apiUploadAndParse,
  writeFixture,
  fixturePayload,
} from "./helpers";

test("多方案拆分：拆分卡片流 → 改标签 → 确认拆分", async ({ page, request }) => {
  writeFixture(fixturePayload("multi-plan"));
  const projectId = await apiCreateProject(request, "E2E-多方案拆分");
  const quoteId = await apiUploadAndParse(request, projectId, [
    { asset: "quote-page.png", fileName: "quote-page.png", mimeType: "image/png" },
  ]);

  // 拆分卡片流取代 7 Tab（planCount=2）
  await page.goto(`/quotes/${quoteId}/confirm`);
  await expect(page.getByLabel("多方案拆分确认")).toBeVisible();

  // 改写方案B标签后确认拆分（保留全部方案）
  const labelInputs = page.getByLabel("方案标签");
  await expect(labelInputs).toHaveCount(2);
  await labelInputs.nth(1).fill("优选方案");
  await page.getByRole("button", { name: /确认拆分，创建 2 份报价/ }).click();

  // 拆分成功导航回项目页：两份平级子报价（容器已删除）
  await page.waitForURL(/\/projects\/\d+$/);
  await expect(page.getByText("方案A")).toBeVisible();
  await expect(page.getByText("优选方案")).toBeVisible();

  // 数据面复核：原容器报价已删除，两份子报价共享同一原文件
  const container = await request.get(`http://127.0.0.1:8310/api/quotes/${quoteId}`);
  expect(container.status()).toBe(404);
});
