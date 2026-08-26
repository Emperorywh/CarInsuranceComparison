import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

/**
 * Vitest 单测配置（按 Next.js 官方 Vitest 指南搭建）。
 * - jsdom 环境跑组件测试；网络请求一律 mock，不依赖真实后端；
 * - resolve.tsconfigPaths 启用 @/ 路径别名（与 Next.js tsconfig 一致）。
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    tsconfigPaths: true,
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    setupFiles: ["tests/setup.ts"],
  },
});
