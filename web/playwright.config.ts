/**
 * Playwright 端到端配置（TASK-07，SPEC §15.2 主路径门禁）。
 *
 * 环境：
 * - API 由 api/scripts/e2e_harness.py 以 webServer 方式托管：一次性测试库
 *   （外部 PostgreSQL 优先、嵌入式兜底）+ fixture 假视觉模型；
 * - 前端用生产构建（next start），构建时以 E2E 前端端口注入 API 地址
 *   （NEXT_PUBLIC_ 变量在构建期内联，见 scripts/run-e2e.mjs）；
 * - 移动优先（SPEC §8）：默认视口 390×844（iPhone 级别）。
 */
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // 拆分/合并等主路径包含轮询与重试等待，单用例放宽到 2 分钟
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    viewport: { width: 390, height: 844 },
    baseURL: "http://127.0.0.1:3310",
    locale: "zh-CN",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: "uv run --project ../api python ../api/scripts/e2e_harness.py up",
      url: "http://127.0.0.1:8310/health",
      reuseExistingServer: false,
      timeout: 180_000,
    },
    {
      command: "pnpm exec next start -p 3310",
      url: "http://127.0.0.1:3310/",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
