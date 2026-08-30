"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, BadgeCheck, FileSearch, PencilLine, Trash2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { PageError } from "@/components/shared/page-error";
import { StatusBadge } from "@/components/quote/status-badge";
import { DiscountEditor } from "@/components/quote/discount-editor";
import { ParseStatusPanel } from "@/components/files/parse-status-panel";
import { QuoteFileStrip } from "@/components/files/quote-file-strip";
import { quotesApi, type Quote } from "@/lib/api";
import { formatCoverageAmount, formatMoney } from "@/lib/format";
import { useDictionaries } from "@/lib/use-dictionaries";

/**
 * 报价详情：价格摘要 + 优惠编辑（净支出）+ 编辑确认内容入口。
 * TASK-03 起支持上传路径：解析任务状态（轮询/重试/转手动）与受控
 * 文件预览条；evidence 定位展示由 TASK-04 扩展。
 */
export default function QuoteDetailPage() {
  const params = useParams<{ id: string }>();
  const quoteId = Number(params.id);
  const router = useRouter();
  const { dict, error: dictError, retry } = useDictionaries();

  const [quote, setQuote] = React.useState<Quote | null>(null);
  const [notFound, setNotFound] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [deleting, setDeleting] = React.useState(false);
  const [reloadToken, setReloadToken] = React.useState(0);

  const invalidId = !Number.isInteger(quoteId);

  React.useEffect(() => {
    if (invalidId) return;
    let cancelled = false;
    quotesApi
      .get(quoteId)
      .then((data) => {
        if (cancelled) return;
        setQuote(data);
        setNotFound(false);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        if (cause instanceof Error && cause.message.includes("不存在")) {
          setNotFound(true);
        } else {
          setError(cause instanceof Error ? cause.message : "加载失败，请稍后重试");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [quoteId, invalidId, reloadToken]);

  const run = React.useCallback(async (action: () => Promise<Quote>) => {
    setSaving(true);
    setActionError(null);
    try {
      const next = await action();
      setQuote(next);
      return true;
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "保存失败，请稍后重试");
      return false;
    } finally {
      setSaving(false);
    }
  }, []);

  async function handleDelete() {
    setDeleting(true);
    try {
      await quotesApi.remove(quoteId);
      if (quote) router.push(`/projects/${quote.projectId}`);
    } finally {
      setDeleting(false);
    }
  }

  if (invalidId || notFound) {
    return (
      <main className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col gap-5 px-4 pb-10 pt-6">
        <header className="flex items-center gap-3">
          <Button asChild variant="ghost" size="icon" aria-label="返回项目列表">
            <Link href="/">
              <ArrowLeft aria-hidden />
            </Link>
          </Button>
          <h1 className="text-xl font-bold">报价详情</h1>
        </header>
        <EmptyState
          icon={FileSearch}
          title="报价不存在或已被删除"
          description="可能已被删除，或链接不正确。"
          action={
            <Button asChild variant="outline">
              <Link href="/">返回项目列表</Link>
            </Button>
          }
        />
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col gap-5 px-4 pb-10 pt-6">
      <header className="flex items-center gap-3">
        <Button
          asChild
          variant="ghost"
          size="icon"
          aria-label="返回项目详情"
        >
          <Link href={quote ? `/projects/${quote.projectId}` : "/"}>
            <ArrowLeft aria-hidden />
          </Link>
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-bold">
            {quote ? `${quote.insurerName}${quote.agentName ? ` · ${quote.agentName}` : ""}` : "报价详情"}
          </h1>
        </div>
        {quote ? <StatusBadge group="quoteStatus" value={quote.status} /> : null}
      </header>

      {(error || dictError) ? (
        <PageError
          message={error ?? dictError ?? "加载失败"}
          onRetry={() => (error ? setReloadToken((token) => token + 1) : retry())}
        />
      ) : null}

      {!quote || !dict ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-56 w-full" />
        </div>
      ) : null}

      {quote && dict ? (
        <>
          {/* 解析任务状态：排队/解析轮询（3s）、失败重试与转手动入口 */}
          <ParseStatusPanel
            quoteId={quote.id}
            status={quote.status}
            onQuoteChange={setQuote}
          />

          {/* 已上传文件：受控缩略图横滑 + 全屏预览（带访问令牌） */}
          <QuoteFileStrip files={quote.files} />

          {/* 价格摘要：显示值优先、计算值回退；null 一律显示“—” */}
          <Card>
            <CardContent className="flex flex-col gap-2 pt-4 text-sm">
              {(
                [
                  ["商业险", quote.commercialPremium, quote.computedCommercialPremium, "元"],
                  ["交强险", quote.compulsoryPremium, null, "元"],
                  ["车船税", quote.vehicleTax, null, "元"],
                  ["独立保障包", quote.packageTotal, quote.computedPackageTotal, "元"],
                  ["其他费用", quote.otherFees, null, "元"],
                ] as const
              ).map(([label, value, computed]) => (
                <div key={label} className="flex items-center justify-between">
                  <span className="text-muted-foreground">{label}</span>
                  <span className="font-medium">
                    {formatMoney(value ?? computed)}
                  </span>
                </div>
              ))}
              <div className="flex items-center justify-between border-t pt-2">
                <span className="text-muted-foreground">系统计算总价</span>
                <span className="font-semibold">{formatMoney(quote.computedTotal)}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">报价单官方总价</span>
                <span className="flex items-center gap-2 font-semibold">
                  {formatMoney(quote.officialTotal)}
                  <StatusBadge group="totalCheckStatus" value={quote.totalCheckStatus} />
                </span>
              </div>
              {quote.totalCheckStatus === "MISMATCH" ? (
                <p role="note" className="text-destructive text-xs">
                  官方总价与系统计算总价不一致，请核对；两者都已保留。
                </p>
              ) : null}
            </CardContent>
          </Card>

          {/* 保障摘要：三者与三者医保外（卡片口径同项目页） */}
          <Card>
            <CardContent className="flex flex-col gap-2 pt-4 text-sm">
              {(
                [
                  ["三者险", "THIRD_PARTY_LIABILITY"],
                  ["三者医保外", "TP_NON_MEDICAL"],
                ] as const
              ).map(([label, code]) => {
                const rows = quote.coverages.filter(
                  (row) => row.code === code && row.status === "INCLUDED"
                );
                const amounts = rows
                  .map((row) => row.coverageAmount)
                  .filter((amount): amount is number => amount !== null);
                return (
                  <div key={code} className="flex items-center justify-between">
                    <span className="text-muted-foreground">{label}</span>
                    <span className="font-medium">
                      {amounts.length > 0 ? formatCoverageAmount(Math.max(...amounts)) : "—"}
                    </span>
                  </div>
                );
              })}
            </CardContent>
          </Card>

          {/* 优惠编辑（含净支出实时展示） */}
          <DiscountEditor quote={quote} saving={saving} run={run} dict={dict} />

          {actionError ? (
            <p role="alert" className="text-destructive text-sm">
              {actionError}
            </p>
          ) : null}

          {/* 操作区：编辑确认内容 / 删除 */}
          <div className="flex flex-wrap gap-3">
            <Button asChild className="flex-1">
              <Link href={`/quotes/${quote.id}/confirm`}>
                {quote.status === "PENDING_CONFIRM" ? (
                  <>
                    <BadgeCheck aria-hidden />
                    去确认报价
                  </>
                ) : (
                  <>
                    <PencilLine aria-hidden />
                    编辑确认内容
                  </>
                )}
              </Link>
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="destructive" className="flex-1" aria-label="删除报价">
                  <Trash2 aria-hidden />
                  删除报价
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>确定删除这份报价？</AlertDialogTitle>
                  <AlertDialogDescription>
                    将删除该报价的全部价格、险种、服务、保障包与优惠记录，删除后不可恢复。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction
                    className="bg-destructive hover:bg-destructive/90"
                    disabled={deleting}
                    onClick={(event) => {
                      // 阻止弹层自动关闭，等删除完成后再由路由跳转离开
                      event.preventDefault();
                      void handleDelete();
                    }}
                  >
                    {deleting ? "删除中…" : "确认删除"}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </>
      ) : null}
    </main>
  );
}
