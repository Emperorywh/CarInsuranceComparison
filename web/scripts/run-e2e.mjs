/**
 * E2E 启动器：以 E2E 专用 API 地址构建前端并运行 Playwright。
 *
 * NEXT_PUBLIC_ 变量在构建期内联，因此必须在此设置环境变量后再执行
 * `next build`（直接跑 `playwright test` 会用到指向 8000 的旧构建产物）。
 */
import { spawnSync } from "node:child_process";

process.env.NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8310";

console.log("[e2e] 构建前端（NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8310）…");
const build = spawnSync("pnpm", ["exec", "next", "build"], {
  stdio: "inherit",
  shell: process.platform === "win32",
});
if (build.status !== 0) {
  process.exitCode = build.status ?? 1;
  process.exit(process.exitCode);
}

console.log("[e2e] 运行 Playwright 主路径门禁…");
const test = spawnSync("pnpm", ["exec", "playwright", "test", ...process.argv.slice(2)], {
  stdio: "inherit",
  shell: process.platform === "win32",
});
process.exitCode = test.status ?? 1;
