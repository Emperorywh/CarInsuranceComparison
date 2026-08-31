/**
 * E2E 收尾：调用 e2e_harness.py down 清理一次性资源（数据库/嵌入式
 * 实例/运行目录）。清理是尽力而为：单次失败不影响下次运行——up 总是
 * 先销毁重建一切。
 */
import { execSync } from "node:child_process";
import path from "node:path";

export default function globalTeardown(): void {
  const apiRoot = path.resolve(__dirname, "../../api");
  try {
    execSync("uv run python scripts/e2e_harness.py down", {
      cwd: apiRoot,
      stdio: "inherit",
      timeout: 120_000,
    });
  } catch (cause) {
    console.warn("[e2e-teardown] 清理失败（下次 up 会重建）：", cause);
  }
}
