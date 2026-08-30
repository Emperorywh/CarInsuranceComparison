/**
 * 解析状态面板与文件预览条组件测试（TASK-03）：
 * PARSING 每 3 秒轮询、终态刷新报价、PARSE_FAILED 的重试/转手动、
 * 文件条的多文件渲染与 PDF 页数徽标。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ParseStatusPanel } from "@/components/files/parse-status-panel";
import { QuoteFileStrip } from "@/components/files/quote-file-strip";
import {
  fetchFileBlobUrl,
  quotesApi,
  type ParseStatus,
  type Quote,
} from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    quotesApi: {
      ...actual.quotesApi,
      getParseStatus: vi.fn(),
      get: vi.fn(),
      reparse: vi.fn(),
      convertToManual: vi.fn(),
    },
    fetchFileBlobUrl: vi.fn(),
  };
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

function makeQuote(status: Quote["status"]): Quote {
  return {
    id: 1,
    projectId: 10,
    insurerCode: "PICC",
    insurerName: "人保",
    status,
    files: [],
    coverages: [],
    services: [],
    packages: [],
    annotations: [],
    discounts: [],
    evidences: [],
  } as unknown as Quote;
}

function makeStatus(status: ParseStatus["status"]): ParseStatus {
  return {
    taskId: 500,
    status,
    attempt: 1,
    error: null,
    fileCount: 3,
    quoteStatus: "PARSING",
    startedAt: null,
    finishedAt: null,
  };
}

describe("ParseStatusPanel：解析中轮询（短间隔注入）", () => {
  it("PENDING 显示排队中；间隔后再次轮询并更新为解析中", async () => {
    const getParseStatus = quotesApi.getParseStatus as ReturnType<typeof vi.fn>;
    getParseStatus
      .mockResolvedValueOnce(makeStatus("PENDING"))
      .mockResolvedValueOnce(makeStatus("RUNNING"));

    render(
      <ParseStatusPanel
        quoteId={1}
        status="PARSING"
        onQuoteChange={vi.fn()}
        pollIntervalMs={10}
      />
    );

    // 首次轮询：排队中 + 文件数
    await screen.findByText("排队等待解析…");
    await screen.findByText(/共 3 个文件/);
    expect(getParseStatus).toHaveBeenCalledTimes(1);

    // 一个轮询间隔后：第二次轮询显示解析中
    await screen.findByText("正在解析报价单…");
    expect(getParseStatus).toHaveBeenCalledTimes(2);
  });

  it("任务终态失败后停止轮询并刷新报价（后端已联动 PARSE_FAILED）", async () => {
    const getParseStatus = quotesApi.getParseStatus as ReturnType<typeof vi.fn>;
    getParseStatus
      .mockResolvedValueOnce({
        ...makeStatus("FAILED"),
        error: "视觉模型尚未配置：请检查 VISION_* 配置",
      })
      .mockResolvedValue(makeStatus("FAILED"));
    (quotesApi.get as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeQuote("PARSE_FAILED")
    );
    const onQuoteChange = vi.fn();

    render(
      <ParseStatusPanel
        quoteId={1}
        status="PARSING"
        onQuoteChange={onQuoteChange}
        pollIntervalMs={10}
      />
    );

    await waitFor(() => {
      expect(onQuoteChange).toHaveBeenCalledWith(
        expect.objectContaining({ status: "PARSE_FAILED" })
      );
    });
    // 终态后不再继续轮询
    const calls = getParseStatus.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(getParseStatus.mock.calls.length).toBe(calls);
  });
});

describe("ParseStatusPanel：解析失败出口", () => {
  it("显示错误摘要；重试调用 reparse，转手动调用 convertToManual", async () => {
    (quotesApi.reparse as ReturnType<typeof vi.fn>).mockResolvedValue({
      taskId: 600,
      quoteId: 1,
    });
    (quotesApi.convertToManual as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeQuote("PENDING_CONFIRM")
    );
    (quotesApi.get as ReturnType<typeof vi.fn>).mockResolvedValue(makeQuote("PARSING"));
    const onQuoteChange = vi.fn();

    render(
      <ParseStatusPanel
        quoteId={1}
        status="PARSE_FAILED"
        onQuoteChange={onQuoteChange}
      />
    );

    await screen.findByText("报价单解析失败");
    fireEvent.click(screen.getByRole("button", { name: /重试解析/ }));
    await waitFor(() => {
      expect(quotesApi.reparse).toHaveBeenCalledWith(1);
    });

    fireEvent.click(screen.getByRole("button", { name: /转手动录入/ }));
    await waitFor(() => {
      expect(quotesApi.convertToManual).toHaveBeenCalledWith(1);
      expect(onQuoteChange).toHaveBeenCalledWith(
        expect.objectContaining({ status: "PENDING_CONFIRM" })
      );
    });
  });

  it("非解析中/失败状态不渲染面板", () => {
    render(
      <ParseStatusPanel
        quoteId={1}
        status="PENDING_CONFIRM"
        onQuoteChange={vi.fn()}
      />
    );
    expect(screen.queryByLabelText("解析进度")).toBeNull();
    expect(screen.queryByLabelText("解析失败信息")).toBeNull();
  });
});

describe("QuoteFileStrip：受控文件预览条", () => {
  const files = [
    {
      id: 11,
      fileName: "车损报价.jpg",
      mime: "image/jpeg",
      sizeBytes: 1024,
      pageCount: 1,
      rawUrl: "/api/files/11/raw?projectId=10",
    },
    {
      id: 12,
      fileName: "条款.pdf",
      mime: "application/pdf",
      sizeBytes: 2048,
      pageCount: 4,
      rawUrl: "/api/files/12/raw?projectId=10",
    },
  ];

  it("渲染多文件缩略图：图片经受控 blob 加载，PDF 显示页数徽标", async () => {
    (fetchFileBlobUrl as ReturnType<typeof vi.fn>).mockResolvedValue("blob:img-11");

    render(<QuoteFileStrip files={files} />);

    expect(screen.getByText("报价单文件（2）")).toBeTruthy();
    // PDF 显示页数徽标，不加载 blob
    expect(screen.getByText("4 页")).toBeTruthy();
    await waitFor(() => {
      expect(fetchFileBlobUrl).toHaveBeenCalledWith(files[0].rawUrl);
    });
    expect(fetchFileBlobUrl).toHaveBeenCalledTimes(1); // PDF 不预加载
    await screen.findByAltText("车损报价.jpg");
  });

  it("点击缩略图打开全屏查看器并可关闭", async () => {
    (fetchFileBlobUrl as ReturnType<typeof vi.fn>).mockResolvedValue("blob:img-11");

    render(<QuoteFileStrip files={files} />);
    fireEvent.click(screen.getByRole("button", { name: /查看文件 车损报价\.jpg/ }));

    const dialog = await screen.findByRole("dialog", { name: /文件预览 车损报价\.jpg/ });
    expect(dialog).toBeTruthy();
    expect(screen.getByRole("button", { name: "下一个文件" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭预览" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });
});
