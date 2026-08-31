/**
 * E2E 主路径 5：已确认报价补传合并（SPEC §15.2.10）。
 * 已确认报价补传新文件 → 解析生成 MERGE_REVIEW 变更清单 → 逐项
 * 「采纳新值 / 保留旧值」→ 全部解决后回到 CONFIRMED。
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import {
  API_BASE,
  apiConfirmQuote,
  apiCreateProject,
  apiUploadAndParse,
  writeFixture,
  fixturePayload,
} from "./helpers";

test("补传合并：MERGE_REVIEW 逐项裁决 → 回到已确认", async ({ page, request }) => {
  // 首传 plan-a 并确认；随后把“模型”切到 v2（三者 300万→500万 + 新增外部电网）
  writeFixture(fixturePayload("plan-a"));
  const projectId = await apiCreateProject(request, "E2E-补传合并");
  const quoteId = await apiUploadAndParse(request, projectId, [
    { asset: "quote-page.png", fileName: "quote-page.png", mimeType: "image/png" },
  ]);
  await apiConfirmQuote(request, quoteId);

  // 已确认报价补传：只解析本次新增文件，旧数据全程可读
  writeFixture(fixturePayload("plan-a-v2"));
  const form = new FormData();
  form.append(
    "files",
    new File(
      [new Uint8Array(readFileSync(path.resolve(__dirname, "assets/quote-page.png")))],
      "quote-page-v2.png",
      { type: "image/png" }
    )
  );
  const reupload = await request.post(`${API_BASE}/api/quotes/${quoteId}/files`, {
    multipart: form,
  });
  expect(reupload.status()).toBe(202);

  // 详情页显示非阻断合并提示；前往确认页处理变更
  await page.goto(`/quotes/${quoteId}`);
  await expect(page.getByText("补传解析完成，有待确认的合并变更")).toBeVisible({
    timeout: 30_000,
  });
  await page.getByRole("link", { name: "处理合并变更" }).click();
  await page.waitForURL(/\/quotes\/\d+\/confirm$/);
  await expect(page.getByLabel("合并变更确认")).toBeVisible();

  // 旧值未被静默覆盖：变更清单展示 旧值 → 新值
  await expect(page.getByText("300 万").first()).toBeVisible();
  await expect(page.getByText("500 万").first()).toBeVisible();

  // 逐项裁决：默认预选（采纳新值/保留旧值），直接完成合并
  await page.getByRole("button", { name: /完成合并/ }).click();

  // 全部解决后回到正常确认页（CONFIRMED 状态）
  await expect(page.getByText("该报价已确认")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("合并变更确认")).toHaveCount(0);
});
