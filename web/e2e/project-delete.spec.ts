/**
 * E2E 主路径 1：项目删除（SPEC §15.2.10 扩展覆盖项）。
 * 验证删除的二次确认：取消不删除、确认后项目消失（级联语义由后端
 * 集成测试保证，此处验证浏览器交互与页面跳转）。
 */
import { expect, test } from "@playwright/test";

import { apiCreateProject, API_BASE } from "./helpers";

test("删除项目：取消保留，确认后从列表消失", async ({ page, request }) => {
  const projectId = await apiCreateProject(request, "E2E-删除项目");
  await page.goto(`/projects/${projectId}`);
  await expect(page.getByRole("heading", { name: "E2E-删除项目" })).toBeVisible();

  // 取消：项目保留
  await page.getByRole("button", { name: "删除项目" }).click();
  await page.getByRole("button", { name: "取消" }).click();
  await expect(page.getByRole("heading", { name: "E2E-删除项目" })).toBeVisible();

  // 确认删除：跳转回首页且列表中不再出现
  await page.getByRole("button", { name: "删除项目" }).click();
  await page.getByRole("button", { name: "确认删除" }).click();
  await page.waitForURL("/");
  await expect(page.getByText("E2E-删除项目")).toHaveCount(0);

  // 数据面复核：详情接口 404（级联删除生效）
  const detail = await request.get(`${API_BASE}/api/projects/${projectId}`);
  expect(detail.status()).toBe(404);
  const body = (await detail.json()) as { message?: string };
  expect((body.message ?? "").length).toBeGreaterThan(0);
});
