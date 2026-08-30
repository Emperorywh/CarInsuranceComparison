"use client";

/**
 * 解析任务状态面板（TASK-03 范围 9）。
 *
 * - PARSING：每 3 秒轮询 parse-status（SPEC §8），展示“排队中/解析中”、
 *   已尝试次数与文件数；任务进入终态后停止轮询并刷新报价；
 * - PARSE_FAILED：展示脱敏错误摘要，提供“重试解析”与“转手动录入”
 *   两个出口（SPEC §2.10）；重试期间保持面板不可重复提交；
 * - 转手动保留已上传文件，报价进入 PENDING_CONFIRM 后走既有确认页。
 */

import * as React from "react";
import { Loader2, RefreshCw, Keyboard } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { quotesApi, type ParseStatus, type Quote } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

export function ParseStatusPanel({
  quoteId,
  status,
  onQuoteChange,
  pollIntervalMs = POLL_INTERVAL_MS,
}: {
  quoteId: number;
  status: Quote["status"];
  onQuoteChange: (quote: Quote) => void;
  /** 轮询间隔毫秒数；生产固定 3 秒（SPEC §8），测试可注入更短间隔 */
  pollIntervalMs?: number;
}) {
  const [parseStatus, setParseStatus] = React.useState<ParseStatus | null>(null);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [retrying, setRetrying] = React.useState(false);
  const [converting, setConverting] = React.useState(false);

  const active = status === "PARSING";

  React.useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const next = await quotesApi.getParseStatus(quoteId);
        if (cancelled) return;
        setParseStatus(next);
        if (
          next.status === "PENDING" ||
          next.status === "RUNNING"
        ) {
          timer = window.setTimeout(poll, pollIntervalMs);
          return;
        }
        // 任务终态：刷新报价（SUCCEEDED 的候选展示由 TASK-04 接入，
        // FAILED 的报价状态已由后端联动为 PARSE_FAILED）
        const quote = await quotesApi.get(quoteId);
        if (cancelled) return;
        onQuoteChange(quote);
      } catch {
        // 瞬时网络/加载失败：继续按间隔轮询，不打断解析等待
        if (!cancelled) timer = window.setTimeout(poll, pollIntervalMs);
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [active, quoteId, onQuoteChange, pollIntervalMs]);

  async function handleRetry() {
    setRetrying(true);
    setActionError(null);
    try {
      await quotesApi.reparse(quoteId);
      // 重试受理后报价回到 PARSING，由轮询接管；刷新一次保持状态一致
      onQuoteChange(await quotesApi.get(quoteId));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "重试失败，请稍后重试");
    } finally {
      setRetrying(false);
    }
  }

  async function handleConvert() {
    setConverting(true);
    setActionError(null);
    try {
      onQuoteChange(await quotesApi.convertToManual(quoteId));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "转手动失败，请稍后重试");
    } finally {
      setConverting(false);
    }
  }

  if (status === "PARSE_FAILED") {
    return (
      <Card aria-label="解析失败信息">
        <CardContent className="flex flex-col gap-3 pt-4">
          <div className="flex flex-col gap-1">
            <p className="text-destructive text-sm font-medium">报价单解析失败</p>
            <p className="text-muted-foreground text-sm">
              {parseStatus?.error ?? "解析未能完成；可重试解析，或保留已上传文件转手动录入。"}
            </p>
          </div>
          {actionError ? (
            <p role="alert" className="text-destructive text-sm">
              {actionError}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-3">
            <Button
              className="flex-1"
              disabled={retrying || converting}
              onClick={() => void handleRetry()}
            >
              {retrying ? <Loader2 className="animate-spin" aria-hidden /> : <RefreshCw aria-hidden />}
              {retrying ? "重试中…" : "重试解析"}
            </Button>
            <Button
              variant="outline"
              className="flex-1"
              disabled={retrying || converting}
              onClick={() => void handleConvert()}
            >
              {converting ? <Loader2 className="animate-spin" aria-hidden /> : <Keyboard aria-hidden />}
              {converting ? "转换中…" : "转手动录入"}
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!active) return null;

  return (
    <Card aria-label="解析进度">
      <CardContent className="flex items-center gap-3 pt-4">
        <Loader2 className="text-primary animate-spin shrink-0" aria-hidden />
        <div className="flex flex-col gap-0.5 text-sm">
          <span className="font-medium">
            {parseStatus?.status === "RUNNING" ? "正在解析报价单…" : "排队等待解析…"}
          </span>
          <span className="text-muted-foreground text-xs">
            {parseStatus
              ? `共 ${parseStatus.fileCount} 个文件，单次模型调用，无分文件进度${
                  parseStatus.attempt > 1 ? `；已尝试 ${parseStatus.attempt} 次` : ""
                }`
              : "正在获取解析任务信息…"}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
