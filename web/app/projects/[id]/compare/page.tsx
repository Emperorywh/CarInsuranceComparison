"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ArrowLeft, Scale } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { PageError } from "@/components/shared/page-error";
import { CompareTables } from "@/components/compare/compare-table";
import { ExportCompareButton } from "@/components/compare/export-image-button";
import { projectsApi, type CompareResult } from "@/lib/api";

/**
 * 解析 quoteIds 查询参数（项目页按勾选顺序生成）：逗号分隔整数，
 * 数量必须 2–6。非法输入渲染可操作的错误态，不发对比请求。
 */
function parseQuoteIds(raw: string | null): number[] | null {
  if (!raw) return null;
  const parts = raw.split(",").map((part) => part.trim());
  const ids: number[] = [];
  for (const part of parts) {
    if (!/^\d+$/.test(part)) return null;
    ids.push(Number(part));
  }
  if (ids.length < 2 || ids.length > 6) return null;
  return ids;
}

function CompareSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

/** 对比页主体：使用 useSearchParams 读取勾选顺序（Next 16 要求 Suspense 包裹） */
function ComparePageInner() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const projectId = Number(params.id);
  // quoteIdsParam 作为 effect 依赖：URL 变化时重新请求
  const quoteIdsParam = searchParams.get("quoteIds");
  const quoteIds = React.useMemo(() => parseQuoteIds(quoteIdsParam), [quoteIdsParam]);

  const [result, setResult] = React.useState<CompareResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadToken, setReloadToken] = React.useState(0);

  React.useEffect(() => {
    if (quoteIds === null || !Number.isInteger(projectId)) return;
    let cancelled = false;
    projectsApi
      .compare(projectId, quoteIds)
      .then((data) => {
        if (!cancelled) {
          setResult(data);
          setError(null);
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "加载失败，请稍后重试");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, quoteIds, reloadToken]);

  // 参数非法：给出可操作引导（回项目页重新勾选）
  if (quoteIds === null || !Number.isInteger(projectId)) {
    return (
      <>
        <header className="flex items-center gap-3">
          <Button asChild variant="ghost" size="icon" aria-label="返回项目列表">
            <Link href="/">
              <ArrowLeft aria-hidden />
            </Link>
          </Button>
          <h1 className="truncate text-xl font-bold">报价对比</h1>
        </header>
        <EmptyState
          icon={Scale}
          title="对比参数不正确"
          description="请从项目页勾选 2–6 个已确认报价后点击“开始对比”。"
          action={
            Number.isInteger(projectId) ? (
              <Button asChild variant="outline">
                <Link href={`/projects/${projectId}`}>返回项目页</Link>
              </Button>
            ) : (
              <Button asChild variant="outline">
                <Link href="/">返回项目列表</Link>
              </Button>
            )
          }
        />
      </>
    );
  }

  const diffBaseline = result?.quotes.find((quote) => quote.isDiffBaseline);
  const priceBaseline = result?.quotes.find((quote) => quote.isPriceBaseline);

  return (
    <div className="flex flex-col gap-5">
      {/* 页头：返回 + 标题 + 右上角「导出长图」（SPEC §8；仅在结果就绪时可用） */}
      <header className="flex items-center gap-3">
        <Button asChild variant="ghost" size="icon" aria-label="返回项目">
          <Link href={`/projects/${projectId}`}>
            <ArrowLeft aria-hidden />
          </Link>
        </Button>
        <h1 className="truncate text-xl font-bold">报价对比</h1>
        {result ? (
          <div className="ml-auto shrink-0">
            <ExportCompareButton result={result} />
          </div>
        ) : null}
      </header>

      {error ? (
        <PageError message={error} onRetry={() => setReloadToken((token) => token + 1)} />
      ) : null}

      {!error && result === null ? <CompareSkeleton /> : null}

      {result ? (
        <>
          {/* 基准说明：两种基准身份分别标注，互不改写（SPEC §7.1） */}
          <div className="bg-muted/60 rounded-2xl px-4 py-3 text-xs leading-relaxed">
            <p>
              差异基准：
              <span className="font-medium">{diffBaseline?.displayName}</span>
              （勾选顺序第一个；各行的 ↑/↓/+/- 相对该方案）
            </p>
            {priceBaseline && diffBaseline && priceBaseline.quoteId !== diffBaseline.quoteId ? (
              <p className="mt-0.5">
                价格基准：
                <span className="font-medium">{priceBaseline.displayName}</span>
                （净支出最低）
              </p>
            ) : null}
          </div>

          <CompareTables result={result} />

          {/* 统一免责声明（SPEC §8：页面与导出长图共用同一文案） */}
          <p className="text-muted-foreground px-2 text-center text-xs leading-relaxed">
            {result.disclaimer}
          </p>
        </>
      ) : null}
    </div>
  );
}

export default function ProjectComparePage() {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-6xl flex-col gap-5 px-4 pb-10 pt-6">
      {/* useSearchParams 触发客户端渲染分支：按 Next 16 指南包裹 Suspense */}
      <React.Suspense fallback={<CompareSkeleton />}>
        <ComparePageInner />
      </React.Suspense>
    </main>
  );
}
