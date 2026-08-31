"use client";

/**
 * 解析任务状态面板（TASK-03 范围 9；TASK-05 扩展已确认报价场景）。
 *
 * - PARSING：每 3 秒轮询 parse-status（SPEC §8），展示“排队中/解析中”、
 *   已尝试次数与文件数；任务进入终态后停止轮询并刷新报价；
 * - PARSE_FAILED：展示脱敏错误摘要，提供“重试解析”与“转手动录入”
 *   两个出口（SPEC §2.10）；重试期间保持面板不可重复提交；
 * - 转手动保留已上传文件，报价进入 PENDING_CONFIRM 后走既有确认页。
 * TASK-05（SPEC §2.10 已确认报价合并解析）：
 * - CONFIRMED 补传/重解析期间报价保持 CONFIRMED（旧数据可读可对比）：
 *   面板探测到活动任务时以非阻断提示条展示进度，绝不遮挡旧内容；
 * - 合并解析失败：旧数据不受影响，提供“重试解析”（仍为已确认口径）；
 * - MERGE_REVIEW：提示前往确认页逐项处理合并变更。
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
  // 已确认/合并审阅报价：解析任务独立运行，报价状态不变，需要探测任务
  const probingConfirmed = status === "CONFIRMED" || status === "MERGE_REVIEW";

  React.useEffect(() => {
    if (!active && !probingConfirmed) return;
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const next = await quotesApi.getParseStatus(quoteId);
        if (cancelled) return;
        setParseStatus(next);
        if (next.status === "PENDING" || next.status === "RUNNING") {
          timer = window.setTimeout(poll, pollIntervalMs);
          return;
        }
        // 任务终态：刷新报价（PARSING 成功进入候选确认；合并成功进入
        // MERGE_REVIEW；失败保持原状态，均由后端状态机保证）
        const quote = await quotesApi.get(quoteId);
        if (cancelled) return;
        onQuoteChange(quote);
      } catch (cause) {
        if (cancelled) return;
        // 无任务（已确认报价且从未补传）：停止探测，不做无意义轮询
        if (cause instanceof Error && cause.message.includes("不存在")) return;
        if (!active) return;
        // PARSING 报价的瞬时网络失败：继续按间隔轮询，不打断解析等待
        timer = window.setTimeout(poll, pollIntervalMs);
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [active, probingConfirmed, quoteId, onQuoteChange, pollIntervalMs]);

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

  if (status === "MERGE_REVIEW") {
    return (
      <Card aria-label="合并确认提示" className="border-sky-200 bg-sky-50/70">
        <CardContent className="flex flex-col gap-1 pt-4">
          <p className="text-sm font-medium text-sky-800">
            补传解析完成，有待确认的合并变更
          </p>
          <p className="text-muted-foreground text-xs">
            请前往确认页逐项选择「采纳新值 / 保留旧值」；在完成前，旧的已确认数据继续参与对比。
          </p>
        </CardContent>
      </Card>
    );
  }

  // 已确认报价的合并解析失败：非阻断提示条 + 重试（不转手动）
  if (status === "CONFIRMED" && parseStatus?.status === "FAILED") {
    return (
      <Card aria-label="补传解析失败提示" className="border-amber-300 bg-amber-50/70">
        <CardContent className="flex flex-col gap-2 pt-4">
          <p className="text-sm font-medium text-amber-800">
            本次补传/重解析失败，已确认数据不受影响
          </p>
          <p className="text-muted-foreground text-sm">
            {parseStatus.error ?? "解析未能完成；可重试解析，旧数据继续有效。"}
          </p>
          {actionError ? (
            <p role="alert" className="text-destructive text-sm">
              {actionError}
            </p>
          ) : null}
          <div>
            <Button size="sm" variant="outline" disabled={retrying} onClick={() => void handleRetry()}>
              {retrying ? <Loader2 className="animate-spin" aria-hidden /> : <RefreshCw aria-hidden />}
              {retrying ? "重试中…" : "重试解析"}
            </Button>
          </div>
        </CardContent>
      </Card>
    );
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

  // 进度条展示条件：报价解析中，或已确认报价探测到活动任务（非阻断）
  const showProgress =
    active ||
    (probingConfirmed &&
      (parseStatus?.status === "PENDING" || parseStatus?.status === "RUNNING"));
  if (!showProgress) return null;

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
