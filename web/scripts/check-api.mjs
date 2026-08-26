/**
 * OpenAPI 类型漂移检查：
 * 1. 重新导出后端契约到 api/openapi.json，与仓库提交版本逐字节比较；
 * 2. 用契约重新生成 TS 类型到临时文件，与 web/lib/api-types.d.ts 比较。
 * 存在漂移则失败退出；检查结束恢复原文件，不改动工作区。
 *
 * 约定：任何后端接口变更都必须伴随 pnpm gen:api 的产物更新。
 */
import { spawnSync } from "node:child_process";
import { readFileSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const apiRoot = path.resolve(webRoot, "..", "api");
const openapiPath = path.join(apiRoot, "openapi.json");
const typesPath = path.join(webRoot, "lib", "api-types.d.ts");

function run(command, args, cwd) {
  const result = spawnSync(command, args, { shell: true, cwd, stdio: "pipe" });
  if (result.status !== 0) {
    console.error(`[check:api] 命令失败：${command} ${args.join(" ")}\n${result.stderr}`);
    process.exit(result.status ?? 1);
  }
}

let failed = false;

// ---- 1. OpenAPI 契约 ----
const committedOpenapi = readFileSync(openapiPath, "utf8");
run("uv", [
  "run",
  "--directory",
  JSON.stringify(apiRoot),
  "python",
  "scripts/export_openapi.py",
]);
const freshOpenapi = readFileSync(openapiPath, "utf8");
if (freshOpenapi !== committedOpenapi) {
  console.error("[check:api] 漂移：api/openapi.json 与当前后端代码生成的契约不一致");
  console.error("[check:api] 请执行 pnpm gen:api 并提交更新后的契约产物");
  failed = true;
  // 恢复提交版本，保持工作区干净（改动本身应通过 gen:api 显式产生）
  writeFileSync(openapiPath, committedOpenapi);
} else {
  console.log("[check:api] api/openapi.json 与后端代码一致");
}

// ---- 2. 生成的 TS 类型 ----
if (!failed) {
  const committedTypes = readFileSync(typesPath, "utf8");
  const tmpTypes = path.join(webRoot, "lib", ".api-types.check.d.ts");
  run("pnpm", [
    "exec",
    "openapi-typescript",
    JSON.stringify(openapiPath),
    "-o",
    JSON.stringify(tmpTypes),
  ], webRoot);
  const freshTypes = readFileSync(tmpTypes, "utf8");
  rmSync(tmpTypes, { force: true });
  if (freshTypes !== committedTypes) {
    console.error("[check:api] 漂移：web/lib/api-types.d.ts 与 openapi.json 生成的类型不一致");
    console.error("[check:api] 请执行 pnpm gen:api 并提交更新后的产物");
    failed = true;
  } else {
    console.log("[check:api] web/lib/api-types.d.ts 与契约一致");
  }
}

process.exit(failed ? 1 : 0);
