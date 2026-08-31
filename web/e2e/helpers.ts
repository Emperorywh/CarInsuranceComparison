/**
 * E2E 公共助手（TASK-07）。
 *
 * 职责边界：
 * - API 侧数据构造（项目/报价容器/上传/等待解析/确认）走统一响应包，
 *   让每条用例把浏览器交互留给“被测路径”本身；
 * - fixture 假视觉模型的切换：原子改写 current.json（临时文件 + rename），
 *   避免 worker 领取任务时读到半截文件；
 * - 本文件只使用合成测试数据，绝不包含任何真实个人信息。
 */
import { readFileSync, writeFileSync, renameSync, unlinkSync, existsSync } from "node:fs";
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

/** E2E 专用 API 端口（与 api/scripts/e2e_harness.py 的 DEFAULT_API_PORT 一致） */
export const API_BASE = "http://127.0.0.1:8310";
/** E2E 前端地址（playwright.config.ts webServer 启动的 next start） */
export const WEB_BASE = "http://127.0.0.1:3310";

const FIXTURE_CURRENT = path.resolve(
  __dirname,
  "../../api/.e2e-run/vision-fixture/current.json"
);
const FIXTURES_DIR = path.resolve(__dirname, "fixtures");
const ASSETS_DIR = path.resolve(__dirname, "assets");

/** 后端统一响应包形状（仅 E2E 助手内部解包用） */
interface ApiEnvelope<T> {
  code: string;
  message: string;
  data: T | null;
}

/** 统一响应包解包：非 OK 抛错（E2E 数据构造失败要立即暴露） */
export function unwrap<T>(envelope: unknown): T {
  const body = envelope as ApiEnvelope<T>;
  if (!body || body.code !== "OK" || body.data === null) {
    const message = body?.message ?? "响应为空";
    throw new Error(`E2E API 调用失败：${body?.code ?? "UNKNOWN"} ${message}`);
  }
  return body.data;
}

/** 读取 fixtures 目录下的固定抽取结果（§4.1 Schema 的合成样本） */
export function fixturePayload(name: string): unknown {
  return JSON.parse(readFileSync(path.join(FIXTURES_DIR, `${name}.json`), "utf-8"));
}

/**
 * 原子改写“模型下次返回内容”。worker 领取任务时才读取该文件，
 * 因此用例在触发上传/重解析之前调用本函数即可确定模型行为。
 */
export function writeFixture(payload: unknown): void {
  const body = JSON.stringify(payload, null, 2);
  const tmp = `${FIXTURE_CURRENT}.tmp`;
  writeFileSync(tmp, body, "utf-8");
  try {
    renameSync(tmp, FIXTURE_CURRENT);
  } catch {
    // Windows 下目标被占用时 rename 可能失败：删除后重试一次
    if (existsSync(FIXTURE_CURRENT)) unlinkSync(FIXTURE_CURRENT);
    renameSync(tmp, FIXTURE_CURRENT);
  }
}

/** 注入“模型必然失败”指令：走完 3 次产品内置重试后终态失败 */
export function writeFailFixture(): void {
  writeFixture({ __fixture__: "fail" });
}

/** 读取测试图片（生成的无信息图样，非任何真实保单） */
export function assetBuffer(name: string): Buffer {
  return readFileSync(path.join(ASSETS_DIR, name));
}

/** 用标准 FormData 组装多文件上传（Playwright multipart 推荐形态） */
function buildUploadForm(files: UploadFileSpec[], consent: boolean): FormData {
  const form = new FormData();
  form.append("modelProcessingConsent", String(consent));
  for (const file of files) {
    form.append(
      "files",
      new File([new Uint8Array(assetBuffer(file.asset))], file.fileName, {
        type: file.mimeType,
      })
    );
  }
  return form;
}

// ---- API 侧数据构造（HTTP 直连，浏览器交互留给被测路径） ----

export interface UploadFileSpec {
  asset: string;
  fileName: string;
  mimeType: string;
}

/** 创建项目，返回项目 id */
export async function apiCreateProject(
  request: APIRequestContext,
  name: string
): Promise<number> {
  const response = await request.post(`${API_BASE}/api/projects`, {
    data: { name, vehicleName: "E2E 测试车", renewalYear: 2026 },
  });
  return unwrap<{ id: number }>(await response.json()).id;
}

/** 创建 UPLOADED 容器并上传文件（含首次模型传输同意），返回报价 id */
export async function apiUploadFiles(
  request: APIRequestContext,
  projectId: number,
  files: UploadFileSpec[]
): Promise<number> {
  const created = await request.post(`${API_BASE}/api/projects/${projectId}/quotes`, {
    data: { insurerCode: "PICC", agentName: null, source: "UPLOADED" },
  });
  const quote = unwrap<{ id: number }>(await created.json());

  const uploaded = await request.post(`${API_BASE}/api/quotes/${quote.id}/files`, {
    multipart: buildUploadForm(files, true),
  });
  unwrap(await uploaded.json());
  return quote.id;
}

/** 轮询直到解析任务成功（假模型毫秒级完成，超时说明环境异常） */
export async function apiWaitParseSucceeded(
  request: APIRequestContext,
  quoteId: number,
  timeoutMs = 30_000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const status = await request.get(`${API_BASE}/api/quotes/${quoteId}/parse-status`);
    const body = unwrap<{ status: string }>(await status.json());
    if (body.status === "SUCCEEDED") return;
    if (body.status === "FAILED") throw new Error("E2E：解析任务意外失败");
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("E2E：等待解析完成超时");
}

/** 上传 + 等待解析完成的组合（候选数据就绪，报价处于 PENDING_CONFIRM） */
export async function apiUploadAndParse(
  request: APIRequestContext,
  projectId: number,
  files: UploadFileSpec[]
): Promise<number> {
  const quoteId = await apiUploadFiles(request, projectId, files);
  await apiWaitParseSucceeded(request, quoteId);
  return quoteId;
}

/** 确认报价（无冲突时可省略裁决字段），进入 CONFIRMED */
export async function apiConfirmQuote(
  request: APIRequestContext,
  quoteId: number
): Promise<void> {
  const response = await request.post(`${API_BASE}/api/quotes/${quoteId}/confirm`, {
    data: {},
  });
  unwrap(await response.json());
}

/** 通过假模型上传 + 确认，构造一份已确认报价（对比/导出场景的数据准备） */
export async function apiCreateConfirmedQuote(
  request: APIRequestContext,
  projectId: number,
  fixtureName: string,
  agentName: string | null
): Promise<number> {
  writeFixture(fixturePayload(fixtureName));
  const created = await request.post(`${API_BASE}/api/projects/${projectId}/quotes`, {
    data: { insurerCode: "PICC", agentName, source: "UPLOADED" },
  });
  const quote = unwrap<{ id: number }>(await created.json());
  const uploaded = await request.post(`${API_BASE}/api/quotes/${quote.id}/files`, {
    multipart: buildUploadForm(
      [{ asset: "quote-page.png", fileName: "quote-page.png", mimeType: "image/png" }],
      true
    ),
  });
  unwrap(await uploaded.json());
  await apiWaitParseSucceeded(request, quote.id);
  await apiConfirmQuote(request, quote.id);
  return quote.id;
}
