/**
 * 从后端 FastAPI 导出 OpenAPI 并生成前端类型（唯一契约链路）。
 *
 * 产物：
 * - api/openapi.json          后端契约快照（提交入库）
 * - web/lib/api-types.d.ts    openapi-typescript 生成的类型（提交入库）
 *
 * 后端接口变更后必须执行：pnpm gen:api，并把两个产物一起提交；
 * 前端业务代码禁止手写与 api-types 冲突的类型。
 */
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const apiRoot = path.resolve(webRoot, "..", "api");

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    shell: true,
    ...options,
  });
  if (result.status !== 0) {
    console.error(`[gen:api] 命令失败：${command} ${args.join(" ")}`);
    process.exit(result.status ?? 1);
  }
}

// 1. 导出后端 OpenAPI（uv 在 api/ 目录内执行，无需数据库）
run("uv", ["run", "--directory", JSON.stringify(apiRoot), "python", "scripts/export_openapi.py"]);

// 2. 生成 TypeScript 类型
run("pnpm", [
  "exec",
  "openapi-typescript",
  JSON.stringify(path.join(apiRoot, "openapi.json")),
  "-o",
  JSON.stringify(path.join(webRoot, "lib", "api-types.d.ts")),
]);

console.log("[gen:api] 完成：api/openapi.json 与 web/lib/api-types.d.ts 已同步");
