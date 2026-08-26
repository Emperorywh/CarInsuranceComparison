/**
 * 统一类型化 API 客户端（唯一后端访问入口）。
 *
 * 约定（SPEC §9.4 / TASK-01）：
 * - 后端所有响应都是 {code, message, data} 统一包；本模块集中解包；
 * - 422 抛 ValidationErrorApiError（带中文提示）、401 抛 UnauthorizedError
 *   并通知监听者（ApiProvider 弹出令牌输入框），其余非 2xx 抛 ApiError；
 * - 访问令牌只放在 X-Access-Token 请求头与 localStorage，
 *   绝不进入 URL、查询串或日志；
 * - 业务类型一律来自 openapi-typescript 生成产物，不手写第二份。
 */
import type { components } from "@/lib/api-types";

export type Project = components["schemas"]["ProjectRead"];
export type ProjectListItem = components["schemas"]["ProjectListItem"];
export type ProjectCreate = components["schemas"]["ProjectCreate"];
export type ProjectUpdate = components["schemas"]["ProjectUpdate"];

/** 后端统一响应包（仅在此处声明一次形状，data 泛型化） */
interface ApiEnvelope<T> {
  code: string;
  message: string;
  data: T | null;
}

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class UnauthorizedError extends ApiError {
  constructor(message: string) {
    super("UNAUTHORIZED", message, 401);
    this.name = "UnauthorizedError";
  }
}

export class ValidationErrorApiError extends ApiError {
  constructor(message: string) {
    super("VALIDATION_ERROR", message, 422);
    this.name = "ValidationErrorApiError";
  }
}

/** 后端基地址：手机局域网访问时改为电脑局域网 IP */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

// ---- 访问令牌存储（只放 localStorage，不进 URL/SSR/日志） ----
const TOKEN_STORAGE_KEY = "car-insurance.access-token";

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setAccessToken(token: string): void {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearAccessToken(): void {
  window.localStorage.removeItem(TOKEN_STORAGE_KEY);
}

// ---- 401 监听：ApiProvider 订阅后弹出令牌输入框 ----
type UnauthorizedListener = () => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();

export function addUnauthorizedListener(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

function emitUnauthorized(): void {
  for (const listener of unauthorizedListeners) listener();
}

/**
 * 发起请求并解包统一响应；任何错误路径都产出带 code/message 的异常。
 */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  // 令牌存在时随请求发送；服务端未启用令牌时该头被忽略，不影响本机模式
  const token = getAccessToken();
  if (token) headers.set("X-Access-Token", token);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  } catch {
    // 网络层失败（后端未启动等）：不暴露内部细节，给用户可操作的提示
    throw new ApiError("NETWORK_ERROR", "无法连接后端服务，请确认 API 已启动", 0);
  }

  let envelope: ApiEnvelope<T> | null = null;
  try {
    envelope = (await response.json()) as ApiEnvelope<T>;
  } catch {
    // 非统一包响应（如代理错误页），用状态码兜底
  }

  if (response.status === 401) {
    emitUnauthorized();
    throw new UnauthorizedError(envelope?.message ?? "缺少或错误的访问令牌");
  }

  if (!response.ok) {
    const code = envelope?.code ?? `HTTP_${response.status}`;
    const message = envelope?.message ?? "请求失败，请稍后重试";
    if (response.status === 422) throw new ValidationErrorApiError(message);
    throw new ApiError(code, message, response.status);
  }

  if (envelope && envelope.code !== "OK") {
    // HTTP 200 但业务码非 OK（防御性分支，当前后端不会出现）
    throw new ApiError(envelope.code, envelope.message, response.status);
  }

  return (envelope?.data ?? null) as T;
}

/** 项目资源 API：类型由 OpenAPI 产物驱动 */
export const projectsApi = {
  list(): Promise<ProjectListItem[]> {
    return request<ProjectListItem[]>("/api/projects");
  },
  create(payload: ProjectCreate): Promise<Project> {
    return request<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  get(projectId: number): Promise<Project> {
    return request<Project>(`/api/projects/${projectId}`);
  },
  update(projectId: number, payload: ProjectUpdate): Promise<Project> {
    return request<Project>(`/api/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  async remove(projectId: number): Promise<void> {
    await request<null>(`/api/projects/${projectId}`, { method: "DELETE" });
  },
};
