"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { PageError } from "@/components/shared/page-error";
import { StatusBadge } from "@/components/quote/status-badge";
import { PriceTab } from "@/components/quote/price-tab";
import { CoverageTab } from "@/components/quote/coverage-tab";
import { PackageTab } from "@/components/quote/package-tab";
import { AnnotationTab, ServiceTab } from "@/components/quote/service-annotation-tabs";
import { VehicleTab, type ConflictResolution } from "@/components/quote/vehicle-tab";
import { quotesApi, type Quote } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { useDictionaries } from "@/lib/use-dictionaries";
import { cn } from "@/lib/utils";
import { FileSearch } from "lucide-react";

/**
 * 报价确认页（SPEC §8）：固定 7 个 Tab——价格 / 基础车险 / 附加险 /
 * 额外保障 / 增值服务 / 销售说明 / 车辆信息。
 *
 * TASK-02 为手动模式：无文件缩略图与置信度定位（由 TASK-04 扩展）；
 * 底部吸底“确认无误，加入对比”，价格分项或车辆冲突未处理时给出明确阻断提示。
 */

const TABS = [
  { key: "price", label: "价格" },
  { key: "core", label: "基础车险" },
  { key: "additional", label: "附加险" },
  { key: "packages", label: "额外保障" },
  { key: "services", label: "增值服务" },
  { key: "annotations", label: "销售说明" },
  { key: "vehicle", label: "车辆信息" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export default function QuoteConfirmPage() {
  const params = useParams<{ id: string }>();
  const quoteId = Number(params.id);
  const router = useRouter();
  const { dict, error: dictError, retry } = useDictionaries();

  const [quote, setQuote] = React.useState<Quote | null>(null);
  const [notFound, setNotFound] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [activeTab, setActiveTab] = React.useState<TabKey>("price");
  const [saving, setSaving] = React.useState(false);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [confirming, setConfirming] = React.useState(false);
  const [resolution, setResolution] = React.useState<ConflictResolution | null>(null);
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

  /** 统一执行写操作：成功以服务端返回的完整报价刷新，失败展示中文错误。 */
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

  async function handleConfirm() {
    if (!quote) return;
    setConfirming(true);
    setActionError(null);
    try {
      const confirmed = await quotesApi.confirm(quote.id, {
        vehicleConflictResolution: resolution,
      });
      setQuote(confirmed);
      // 确认成功回到项目页查看分组卡片
      router.push(`/projects/${confirmed.projectId}`);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "确认失败，请稍后重试");
    } finally {
      setConfirming(false);
    }
  }

  if (invalidId || notFound) {
    return (
      <main className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col gap-5 px-4 pb-10 pt-6">
        <header className="flex items-center gap-3">
          <Button asChild variant="ghost" size="icon" aria-label="返回">
            <Link href="/">
              <ArrowLeft aria-hidden />
            </Link>
          </Button>
          <h1 className="text-xl font-bold">报价确认</h1>
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

  const editor = quote
    ? { quote, saving, run }
    : null;

  const conflictUnresolved =
    (quote?.vehicleConflict?.resolutionRequired ?? false) === true && !resolution;

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col gap-4 px-4 pb-32 pt-6">
      <header className="flex items-center gap-3">
        <Button asChild variant="ghost" size="icon" aria-label="返回报价详情">
          <Link href={`/quotes/${quoteId}`}>
            <ArrowLeft aria-hidden />
          </Link>
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-bold">
            {quote ? `${quote.insurerName}${quote.agentName ? ` · ${quote.agentName}` : ""}` : "报价确认"}
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
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-72 w-full" />
        </div>
      ) : null}

      {quote && dict && editor ? (
        <>
          {quote.status === "CONFIRMED" ? (
            <Card className="border-emerald-300 bg-emerald-50/70">
              <CardContent className="pt-4 text-sm text-emerald-800">
                该报价已确认，此页可继续编辑（已确认数据的修改会保留并标记“用户已确认”）。
              </CardContent>
            </Card>
          ) : null}

          {/* 7 个固定 Tab */}
          <nav
            role="tablist"
            aria-label="报价确认分区"
            className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1"
          >
            {TABS.map((tab) => (
              <button
                key={tab.key}
                role="tab"
                aria-selected={activeTab === tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={cn(
                  "shrink-0 rounded-full px-4 py-1.5 text-sm font-medium transition-colors",
                  activeTab === tab.key
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground hover:bg-muted/70"
                )}
              >
                {tab.label}
              </button>
            ))}
          </nav>

          <div role="tabpanel" aria-label={TABS.find((tab) => tab.key === activeTab)?.label}>
            {activeTab === "price" ? <PriceTab {...editor} /> : null}
            {activeTab === "core" ? <CoverageTab {...editor} dict={dict} category="CORE" /> : null}
            {activeTab === "additional" ? (
              <CoverageTab {...editor} dict={dict} category="ADDITIONAL" />
            ) : null}
            {activeTab === "packages" ? <PackageTab {...editor} dict={dict} /> : null}
            {activeTab === "services" ? <ServiceTab {...editor} dict={dict} /> : null}
            {activeTab === "annotations" ? <AnnotationTab {...editor} dict={dict} /> : null}
            {activeTab === "vehicle" ? (
              <VehicleTab
                {...editor}
                resolution={resolution}
                onResolutionChange={setResolution}
              />
            ) : null}
          </div>

          {actionError ? (
            <p role="alert" className="text-destructive px-1 text-sm">
              {actionError}
            </p>
          ) : null}

          {/* 底部吸底确认条 */}
          <div className="fixed inset-x-0 bottom-0 z-10 border-t bg-background/95 px-4 py-3 backdrop-blur">
            <div className="mx-auto flex w-full max-w-2xl items-center gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-muted-foreground text-xs">实际净支出</p>
                <p className="truncate text-base font-bold">
                  {formatMoney(quote.netPayment)}
                  {quote.netPayment === null ? (
                    <StatusBadge
                      group="netPaymentStatus"
                      value={quote.netPaymentStatus}
                      className="ml-2"
                    />
                  ) : null}
                </p>
              </div>
              {quote.status === "PENDING_CONFIRM" ? (
                <Button
                  className="h-11 px-5"
                  disabled={saving || confirming || conflictUnresolved}
                  onClick={() => void handleConfirm()}
                >
                  {confirming ? "确认中…" : "确认无误，加入对比"}
                </Button>
              ) : (
                <Button asChild variant="outline" className="h-11">
                  <Link href={`/projects/${quote.projectId}`}>返回项目</Link>
                </Button>
              )}
            </div>
            {conflictUnresolved ? (
              <p role="note" className="text-destructive mx-auto mt-1 max-w-2xl text-xs">
                车辆信息与项目摘要不一致，请先在“车辆信息”Tab 选择处理方式。
              </p>
            ) : null}
          </div>
        </>
      ) : null}
    </main>
  );
}
