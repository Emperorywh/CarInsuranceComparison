/**
 * E2E 主路径 2：纯手动报价（SPEC §15.2.10）。
 * 新建项目 → 选择公司（不经文件）→ 7 Tab 中填写价格分项 → 确认 →
 * 返回项目页出现已确认卡片。全程不触发模型（无同意弹窗）。
 */
import { expect, test } from "@playwright/test";

test("纯手动报价：创建 → 填写 → 确认 → 项目卡片", async ({ page }) => {
  await page.goto("/");

  // 新建项目
  await page.getByRole("link", { name: /新建续保对比/ }).click();
  await page.getByLabel("项目名称").fill("E2E-手动报价");
  await page.getByLabel("车辆名称").fill("E2E 测试车");
  await page.getByLabel("续保年份").fill("2026");
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.waitForURL(/\/projects\/\d+$/);

  // 添加报价：选公司 + 保险员，跳过上传
  await page.getByRole("link", { name: /添加报价/ }).first().click();
  await page.getByRole("radio", { name: "平安" }).click();
  await page.getByLabel("保险员称呼").fill("测试保险员");
  // 未选择文件时主按钮即手动录入，不弹出模型传输同意（隐私边界）
  await page.getByRole("button", { name: /跳过上传，手动录入/ }).click();
  await page.waitForURL(/\/quotes\/\d+\/confirm$/);

  // 价格 Tab：分项先标“已包含”再填金额（值⟺INCLUDED 不变量），保存后净支出实时重算
  await expect(page.getByRole("tab", { name: "价格" })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await page.getByLabel("商业险合计状态").selectOption("INCLUDED");
  await page.getByLabel("商业险合计金额（元）").fill("3000");
  await page.getByLabel("交强险状态").selectOption("INCLUDED");
  await page.getByLabel("交强险金额（元）").fill("950");
  // 官方总价有值时净支出基于官方总价（车船税等未知分项不阻断）
  await page.getByLabel("官方总价（元）").fill("3950");
  await page.getByRole("button", { name: "保存价格分项" }).click();
  await expect(page.getByText("¥3,950.00").first()).toBeVisible();

  // 确认 → 回项目页
  await page.getByRole("button", { name: "确认无误，加入对比" }).click();
  await page.waitForURL(/\/projects\/\d+$/);
  await expect(page.getByText("平安").first()).toBeVisible();
  await expect(page.getByText("测试保险员").first()).toBeVisible();
  // 确认后回填项目车辆摘要（首份报价，无冲突）
  await expect(page.getByText("E2E 测试车").first()).toBeVisible();
});
