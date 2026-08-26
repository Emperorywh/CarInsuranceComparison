/**
 * 统一 API 客户端单测：响应包解包、401/422 错误分类、令牌注入。
 * 全部 mock fetch，不依赖真实后端与网络。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  UnauthorizedError,
  ValidationErrorApiError,
  addUnauthorizedListener,
  clearAccessToken,
  getAccessToken,
  projectsApi,
  setAccessToken,
} from "@/lib/api";

function mockFetchOnce(status: number, body: unknown) {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  );
}

beforeEach(() => {
  clearAccessToken();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("令牌存储", () => {
  it("令牌只写入 localStorage，可读取与清除", () => {
    setAccessToken("my-token");
    expect(getAccessToken()).toBe("my-token");
    expect(window.localStorage.getItem("car-insurance.access-token")).toBe("my-token");
    clearAccessToken();
    expect(getAccessToken()).toBeNull();
  });

  it("请求自动携带 X-Access-Token 头", async () => {
    setAccessToken("my-token");
    const fetchMock = mockFetchOnce(200, { code: "OK", message: "ok", data: [] });
    vi.stubGlobal("fetch", fetchMock);
    await projectsApi.list();
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(new Headers(init.headers).get("X-Access-Token")).toBe("my-token");
  });

  it("无令牌时不发送令牌头", async () => {
    const fetchMock = mockFetchOnce(200, { code: "OK", message: "ok", data: [] });
    vi.stubGlobal("fetch", fetchMock);
    await projectsApi.list();
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(new Headers(init.headers).get("X-Access-Token")).toBeNull();
  });
});

describe("统一响应包处理", () => {
  it("成功响应解包 data 并返回类型化结果", async () => {
    const data = [
      { id: 1, name: "2026 车辆续保", quoteCount: 3, minNetPayment: 5420.0 },
    ];
    vi.stubGlobal("fetch", mockFetchOnce(200, { code: "OK", message: "ok", data }));
    const items = await projectsApi.list();
    expect(items).toEqual(data);
  });

  it("创建请求携带 JSON 请求体", async () => {
    const fetchMock = mockFetchOnce(201, {
      code: "OK",
      message: "ok",
      data: { id: 9, name: "x" },
    });
    vi.stubGlobal("fetch", fetchMock);
    await projectsApi.create({
      name: "2026 车辆续保",
      vehicleName: "Model Y",
      renewalYear: 2026,
      expireDate: null,
      note: null,
    });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
  });
});

describe("错误分类", () => {
  it("401 抛 UnauthorizedError 并通知监听者（弹令牌输入框）", async () => {
    const listener = vi.fn();
    const unsubscribe = addUnauthorizedListener(listener);
    vi.stubGlobal(
      "fetch",
      mockFetchOnce(401, {
        code: "UNAUTHORIZED",
        message: "缺少或错误的访问令牌，请先在页面中输入访问令牌",
        data: null,
      })
    );
    await expect(projectsApi.list()).rejects.toBeInstanceOf(UnauthorizedError);
    expect(listener).toHaveBeenCalledOnce();
    unsubscribe();
  });

  it("422 抛 ValidationErrorApiError，携带后端中文提示", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchOnce(422, {
        code: "VALIDATION_ERROR",
        message: "参数校验失败：name 不能为空",
        data: null,
      })
    );
    const cause = await projectsApi.create({
      name: "",
      vehicleName: "x",
      renewalYear: 2026,
      expireDate: null,
      note: null,
    }).catch((error: unknown) => error);
    expect(cause).toBeInstanceOf(ValidationErrorApiError);
    expect((cause as ValidationErrorApiError).message).toContain("参数校验失败");
  });

  it("其他非 2xx 抛 ApiError（含业务错误码）", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchOnce(404, { code: "PROJECT_NOT_FOUND", message: "项目不存在或已被删除", data: null })
    );
    const cause = await projectsApi.get(99).catch((error: unknown) => error);
    expect(cause).toBeInstanceOf(ApiError);
    expect((cause as ApiError).code).toBe("PROJECT_NOT_FOUND");
  });

  it("网络失败（后端未启动）给出可操作提示", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const cause = await projectsApi.list().catch((error: unknown) => error);
    expect(cause).toBeInstanceOf(ApiError);
    expect((cause as ApiError).message).toContain("无法连接后端服务");
  });
});
