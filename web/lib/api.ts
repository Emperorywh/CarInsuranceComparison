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

// 注：创建接口自 TASK-02 起返回 ProjectDetail（原 ProjectRead 已并入），
// 前端统一使用同一形状，quoteGroups 恒为数组（可能为空）
export type Project = components["schemas"]["ProjectDetail"];
export type ProjectDetail = components["schemas"]["ProjectDetail"];
export type ProjectListItem = components["schemas"]["ProjectListItem"];
export type ProjectCreate = components["schemas"]["ProjectCreate"];
export type ProjectUpdate = components["schemas"]["ProjectUpdate"];
export type QuoteGroup = components["schemas"]["QuoteGroup"];
export type QuoteCardSummary = components["schemas"]["QuoteCardSummary"];

export type Quote = components["schemas"]["QuoteRead"];
export type QuoteCreate = components["schemas"]["QuoteCreate"];
export type QuoteUpdate = components["schemas"]["QuoteUpdate"];
export type QuoteConfirmPayload = components["schemas"]["QuoteConfirm"];
export type VehicleConflictInfo = components["schemas"]["VehicleConflictInfo"];
export type Coverage = components["schemas"]["CoverageRead"];
export type CoverageCreate = components["schemas"]["CoverageCreate"];
export type CoverageUpdate = components["schemas"]["CoverageUpdate"];
export type ServiceRow = components["schemas"]["ServiceRead"];
export type ServiceCreate = components["schemas"]["ServiceCreate"];
export type ServiceUpdate = components["schemas"]["ServiceUpdate"];
export type PackageRow = components["schemas"]["PackageRead"];
export type PackageCreate = components["schemas"]["PackageCreate"];
export type PackageUpdate = components["schemas"]["PackageUpdate"];
export type PackageCoverage = components["schemas"]["PackageCoverageRead"];
export type PackageCoverageCreate = components["schemas"]["PackageCoverageCreate"];
export type PackageCoverageUpdate = components["schemas"]["PackageCoverageUpdate"];
export type AnnotationRow = components["schemas"]["AnnotationRead"];
export type AnnotationCreate = components["schemas"]["AnnotationCreate"];
export type AnnotationUpdate = components["schemas"]["AnnotationUpdate"];
export type DiscountRow = components["schemas"]["DiscountRead"];
export type DiscountCreate = components["schemas"]["DiscountCreate"];
export type DiscountUpdate = components["schemas"]["DiscountUpdate"];
export type Dictionaries = components["schemas"]["DictionariesRead"];

// TASK-03：文件资产与解析任务
export type QuoteFile = components["schemas"]["FileRead"];
export type ParseStatus = components["schemas"]["ParseStatusRead"];
export type TaskCreated = components["schemas"]["TaskCreatedRead"];
export type UploadFilesResult = components["schemas"]["UploadFilesResultRead"];

// TASK-05：多方案拆分与补传合并
export type PlanSplitPreview = components["schemas"]["PlanSplitPreviewRead"];
export type PlanSplitPlanPreview = components["schemas"]["PlanSplitPlanPreview"];
export type PlanSplitRequest = components["schemas"]["PlanSplitRequest"];
export type PlanSplitResult = components["schemas"]["PlanSplitResultRead"];
export type MergePreview = components["schemas"]["MergePreviewRead"];
export type MergeChange = components["schemas"]["MergeChangeRead"];
export type MergeResolutionChoice = "ACCEPT" | "KEEP";

// TASK-06：规则对比引擎（单一总表差异）
export type CompareResult = components["schemas"]["ComparisonResult"];
export type CompareQuoteMeta = components["schemas"]["CompareQuoteMeta"];
export type CompareCell = components["schemas"]["CompareCell"];
export type CompareRow = components["schemas"]["CompareRow"];
export type DiffTag = NonNullable<CompareCell["tag"]>;

/** 金额请求值统一用字符串发送（后端 Decimal 精确解析，避免 JS 浮点误差） */
export type AmountInput = number | string;

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
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8877";

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
  // FormData 由浏览器自动生成 multipart 边界，绝不能手动设 Content-Type
  if (init.body !== undefined && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
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
  get(projectId: number): Promise<ProjectDetail> {
    return request<ProjectDetail>(`/api/projects/${projectId}`);
  },
  update(projectId: number, payload: ProjectUpdate): Promise<ProjectDetail> {
    return request<ProjectDetail>(`/api/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  async remove(projectId: number): Promise<void> {
    await request<null>(`/api/projects/${projectId}`, { method: "DELETE" });
  },
  /**
   * 多报价对比（TASK-06）：quoteIds 顺序即勾选顺序，
   * 服务端以第一个为差异基准、最低净支出为价格归因基准。
   */
  compare(projectId: number, quoteIds: number[]): Promise<CompareResult> {
    return request<CompareResult>(
      `/api/projects/${projectId}/compare?quoteIds=${quoteIds.join(",")}`
    );
  },
};

// ---- 字典（单一代码来源：展示值由后端驱动，前端不复制第二套字典）----

let dictionariesCache: Promise<Dictionaries> | null = null;

/** 加载字典（模块级缓存：多个页面/组件共享一次请求）。 */
export function loadDictionaries(): Promise<Dictionaries> {
  if (!dictionariesCache) {
    dictionariesCache = request<Dictionaries>("/api/dictionaries").catch((cause) => {
      // 失败允许重试：清空缓存避免永久卡在失败态
      dictionariesCache = null;
      throw cause;
    });
  }
  return dictionariesCache;
}

/** 按枚举名查中文标签；未知值回退为原码（字典缺失时不至于渲染空白）。 */
export function statusLabel(group: string, value: string | null | undefined): string {
  if (!value) return "—";
  const labels = DICTIONARIES_SNAPSHOT?.statusLabels?.[group];
  return labels?.[value] ?? value;
}

/** 字典快照：由 DictProvider 加载后填充，供纯展示组件同步查标签。 */
export let DICTIONARIES_SNAPSHOT: Dictionaries | null = null;

export function setDictionariesSnapshot(dictionaries: Dictionaries): void {
  DICTIONARIES_SNAPSHOT = dictionaries;
}

/** 报价资源 API：所有明细层写操作都返回重算后的完整报价，前端整体刷新。 */
export const quotesApi = {
  create(projectId: number, payload: QuoteCreate): Promise<Quote> {
    return request<Quote>(`/api/projects/${projectId}/quotes`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  get(quoteId: number): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}`);
  },
  update(quoteId: number, payload: QuoteUpdate): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  async remove(quoteId: number): Promise<void> {
    await request<null>(`/api/quotes/${quoteId}`, { method: "DELETE" });
  },
  confirm(quoteId: number, payload: QuoteConfirmPayload): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/confirm`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  createCoverage(quoteId: number, payload: CoverageCreate): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/coverages`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateCoverage(quoteId: number, rowId: number, payload: CoverageUpdate): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/coverages/${rowId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteCoverage(quoteId: number, rowId: number): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/coverages/${rowId}`, {
      method: "DELETE",
    });
  },

  createService(quoteId: number, payload: ServiceCreate): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/services`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateService(quoteId: number, rowId: number, payload: ServiceUpdate): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/services/${rowId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteService(quoteId: number, rowId: number): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/services/${rowId}`, {
      method: "DELETE",
    });
  },

  createPackage(quoteId: number, payload: PackageCreate): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/packages`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updatePackage(quoteId: number, packageId: number, payload: PackageUpdate): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/packages/${packageId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deletePackage(quoteId: number, packageId: number): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/packages/${packageId}`, {
      method: "DELETE",
    });
  },
  createPackageCoverage(
    quoteId: number,
    packageId: number,
    payload: PackageCoverageCreate
  ): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/packages/${packageId}/coverages`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updatePackageCoverage(
    quoteId: number,
    packageId: number,
    coverageId: number,
    payload: PackageCoverageUpdate
  ): Promise<Quote> {
    return request<Quote>(
      `/api/quotes/${quoteId}/packages/${packageId}/coverages/${coverageId}`,
      { method: "PATCH", body: JSON.stringify(payload) }
    );
  },
  deletePackageCoverage(
    quoteId: number,
    packageId: number,
    coverageId: number
  ): Promise<Quote> {
    return request<Quote>(
      `/api/quotes/${quoteId}/packages/${packageId}/coverages/${coverageId}`,
      { method: "DELETE" }
    );
  },

  createAnnotation(quoteId: number, payload: AnnotationCreate): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/annotations`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateAnnotation(quoteId: number, rowId: number, payload: AnnotationUpdate): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/annotations/${rowId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteAnnotation(quoteId: number, rowId: number): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/annotations/${rowId}`, {
      method: "DELETE",
    });
  },

  createDiscount(quoteId: number, payload: DiscountCreate): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/discounts`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateDiscount(quoteId: number, rowId: number, payload: DiscountUpdate): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/discounts/${rowId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteDiscount(quoteId: number, rowId: number): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/discounts/${rowId}`, {
      method: "DELETE",
    });
  },

  // ---- TASK-03：解析任务与上传 ----

  /** 解析任务轮询（排队中/解析中/失败；3 秒一次，终态停止）。 */
  getParseStatus(quoteId: number): Promise<ParseStatus> {
    return request<ParseStatus>(`/api/quotes/${quoteId}/parse-status`);
  },

  /**
   * 未确认报价的重新解析（PARSE_FAILED 重试 / PENDING_CONFIRM 重解析）。
   * consent 仅在项目首次解析且未记录同意时需要，否则后端忽略。
   */
  reparse(quoteId: number, modelProcessingConsent = false): Promise<TaskCreated> {
    const form = new FormData();
    form.append("modelProcessingConsent", String(modelProcessingConsent));
    return request<TaskCreated>(`/api/quotes/${quoteId}/reparse`, {
      method: "POST",
      body: form,
    });
  },

  /** 解析失败转纯手动：保留已上传文件，报价进入 PENDING_CONFIRM。 */
  convertToManual(quoteId: number): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/convert-manual`, { method: "POST" });
  },

  // ---- TASK-05：多方案拆分与补传合并 ----

  /** 拆分确认视图：各方案标签、价格与关键保障摘要（planCount>1 时可用）。 */
  getPlanSplit(quoteId: number): Promise<PlanSplitPreview> {
    return request<PlanSplitPreview>(`/api/quotes/${quoteId}/plan-split`);
  },

  /** 确认拆分：保留方案创建平级子报价，容器报价删除（单事务）。 */
  confirmPlanSplit(quoteId: number, payload: PlanSplitRequest): Promise<PlanSplitResult> {
    return request<PlanSplitResult>(`/api/quotes/${quoteId}/plan-split`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  /** 补传合并变更清单：旧值/新值/来源/用户编辑标识与默认裁决。 */
  getMergePreview(quoteId: number): Promise<MergePreview> {
    return request<MergePreview>(`/api/quotes/${quoteId}/merge-preview`);
  },

  /** 逐项 ACCEPT/KEEP；全部解决后原子合并并回到 CONFIRMED。 */
  resolveMerge(
    quoteId: number,
    resolutions: { changeId: number; resolution: MergeResolutionChoice }[]
  ): Promise<Quote> {
    return request<Quote>(`/api/quotes/${quoteId}/merge-resolve`, {
      method: "POST",
      body: JSON.stringify({ resolutions }),
    });
  },
};

/**
 * 多文件上传并创建解析任务（接口返回 202 + taskId）。
 *
 * 用 XHR 而非 fetch：浏览器 fetch 没有上传进度事件，无法展示上传进度。
 * 令牌、统一响应包与 401 监听与 request() 保持同一套约定。
 */
export function uploadQuoteFiles(
  quoteId: number,
  files: File[],
  options: {
    modelProcessingConsent?: boolean;
    onProgress?: (percent: number) => void;
  } = {}
): Promise<UploadFilesResult> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    form.append(
      "modelProcessingConsent",
      String(options.modelProcessingConsent ?? false)
    );

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE_URL}/api/quotes/${quoteId}/files`);
    const token = getAccessToken();
    if (token) xhr.setRequestHeader("X-Access-Token", token);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        options.onProgress?.(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      let envelope: ApiEnvelope<UploadFilesResult> | null = null;
      try {
        envelope = JSON.parse(xhr.responseText) as ApiEnvelope<UploadFilesResult>;
      } catch {
        // 非 JSON 响应按状态码兜底
      }
      if (xhr.status === 401) {
        emitUnauthorized();
        reject(new UnauthorizedError(envelope?.message ?? "缺少或错误的访问令牌"));
        return;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        const code = envelope?.code ?? `HTTP_${xhr.status}`;
        reject(new ApiError(code, envelope?.message ?? "上传失败，请稍后重试", xhr.status));
        return;
      }
      if (!envelope || envelope.data === null) {
        reject(new ApiError("EMPTY_RESPONSE", "上传响应为空，请稍后重试", xhr.status));
        return;
      }
      resolve(envelope.data);
    };
    xhr.onerror = () => {
      reject(new ApiError("NETWORK_ERROR", "无法连接后端服务，请确认 API 已启动", 0));
    };
    xhr.send(form);
  });
}

/**
 * 经受控原文件接口加载 blob 预览地址。
 *
 * 原 file 绝不直接放进 <img src>：令牌模式（局域网）下无法给 <img> 附
 * X-Access-Token 头，改为带令牌 fetch 后转 objectURL；401 复用全局
 * 令牌输入流程（ApiProvider 监听）。
 */
export async function fetchFileBlobUrl(rawPath: string): Promise<string> {
  const headers: Record<string, string> = {};
  const token = getAccessToken();
  if (token) headers["X-Access-Token"] = token;

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${rawPath}`, { headers });
  } catch {
    throw new ApiError("NETWORK_ERROR", "无法连接后端服务，请确认 API 已启动", 0);
  }
  if (response.status === 401) {
    emitUnauthorized();
    throw new UnauthorizedError("缺少或错误的访问令牌");
  }
  if (!response.ok) {
    throw new ApiError(`HTTP_${response.status}`, "原文件加载失败", response.status);
  }
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}
