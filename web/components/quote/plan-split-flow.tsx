"use client";

/**
 * 多方案拆分确认卡片流（TASK-05；SPEC §2.8、§8）。
 *
 * - 同公司 planCount>1 的成功解析只落脱敏 rawResult（TASK-04）；本组件
 *   从拆分预览接口回放各方案摘要，允许用户改写方案标签并丢弃无效方案；
 * - 确认拆分在服务端单个事务内创建平级子报价并删除容器报价；成功后由
 *   父页面导航离开（容器已不存在，不可停留本页）；
 * - 至少保留一个方案；标签入库前由后端统一脱敏。
 */

import * as React from "react";
import { useRouter } from "next/navigation";
import { GitBranch, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageError } from "@/components/shared/page-error";
import {
  quotesApi,
  type PlanSplitPlanPreview,
  type PlanSplitPreview,
  type Quote,
} from "@/lib/api";
import { formatMoney } from "@/lib/format";

// 预览卡片中展示的价格分项（顺序即展示顺序；null 显示 “—”）
const PRICE_ROWS: { key: string; label: string }[] = [
  { key: "commercialPremium", label: "商业险" },
  { key: "compulsoryPremium", label: "交强险" },
  { key: "vehicleTax", label: "车船税" },
  { key: "packageTotal", label: "独立保障包" },
  { key: "otherFees", label: "其他费用" },
  { key: "officialTotal", label: "官方总价" },
];

/** 单方案卡片：标签可编辑、价格与关键保障摘要、保留/丢弃开关。 */
function PlanCard({
  plan,
  kept,
  label,
  onKeptChange,
  onLabelChange,
}: {
  plan: PlanSplitPlanPreview;
  kept: boolean;
  label: string;
  onKeptChange: (kept: boolean) => void;
  onLabelChange: (label: string) => void;
}) {
  return (
    <Card className={kept ? "" : "opacity-60"}>
      <CardContent className="flex flex-col gap-3 pt-4">
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm font-semibold">
            <input
              type="checkbox"
              checked={kept}
              onChange={(event) => onKeptChange(event.target.checked)}
              aria-label={`保留 ${label}`}
            />
            保留此方案
          </label>
          <Input
            value={label}
            onChange={(event) => onLabelChange(event.target.value)}
            disabled={!kept}
            aria-label="方案标签"
            placeholder="方案标签"
            className="h-9 flex-1"
          />
        </div>

        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          {PRICE_ROWS.map(({ key, label: priceLabel }) => {
            const item = plan.prices[key];
            return (
              <div key={key} className="flex items-center justify-between">
                <span className="text-muted-foreground">{priceLabel}</span>
                <span className="font-medium">
                  {item?.value != null ? formatMoney(item.value) : "—"}
                </span>
              </div>
            );
          })}
        </div>

        {(() => {
          // 生成类型把带默认值的数组标为可选：渲染前统一兜底为空数组
          const coreRows = plan.coreCoverages ?? [];
          const additionalRows = plan.additionalCoverages ?? [];
          return coreRows.length + additionalRows.length > 0 ? (
            <div className="flex flex-col gap-1 text-xs text-muted-foreground">
              {[...coreRows, ...additionalRows].map((row, index) => (
                <div key={`${row.name}-${index}`} className="flex items-center justify-between">
                  <span className="truncate">{row.name}</span>
                  <span>
                    {row.premium != null ? `保费 ${formatMoney(row.premium)}` : ""}
                  </span>
                </div>
              ))}
            </div>
          ) : null;
        })()}

        {(plan.packageSummaries?.length ?? 0) > 0 ? (
          <p className="text-xs text-muted-foreground">
            保障包：{(plan.packageSummaries ?? []).join("；")}
          </p>
        ) : null}
        {(plan.serviceSummaries?.length ?? 0) > 0 ? (
          <p className="text-xs text-muted-foreground">
            增值服务：{(plan.serviceSummaries ?? []).join("；")}
          </p>
        ) : null}
        {plan.unmatchedCount > 0 ? (
          <p className="text-xs text-amber-700">{plan.unmatchedCount} 项未识别内容将进入待确认区</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function PlanSplitFlow({ quote }: { quote: Quote }) {
  const router = useRouter();
  const [preview, setPreview] = React.useState<PlanSplitPreview | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [labels, setLabels] = React.useState<Record<number, string>>({});
  const [kept, setKept] = React.useState<Record<number, boolean>>({});
  const [submitting, setSubmitting] = React.useState(false);
  const [actionError, setActionError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    quotesApi
      .getPlanSplit(quote.id)
      .then((data) => {
        if (cancelled) return;
        setPreview(data);
        setLabels(
          Object.fromEntries(
            data.plans.map((plan) => [plan.index, plan.planLabel ?? `方案 ${plan.index + 1}`])
          )
        );
        // 默认全部保留：丢弃是用户对无效方案的显式动作
        setKept(Object.fromEntries(data.plans.map((plan) => [plan.index, true])));
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "拆分预览加载失败");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [quote.id]);

  if (error) {
    return <PageError message={error} onRetry={() => window.location.reload()} />;
  }
  if (!preview) {
    return (
      <Card aria-label="拆分预览加载中">
        <CardContent className="flex items-center gap-2 pt-4 text-sm text-muted-foreground">
          <Loader2 className="animate-spin" aria-hidden /> 正在加载方案预览…
        </CardContent>
      </Card>
    );
  }

  const keptCount = preview.plans.filter((plan) => kept[plan.index]).length;

  async function handleSplit() {
    if (!preview) return;
    setSubmitting(true);
    setActionError(null);
    try {
      await quotesApi.confirmPlanSplit(quote.id, {
        plans: preview.plans
          .filter((plan) => kept[plan.index])
          .map((plan) => ({ index: plan.index, planLabel: labels[plan.index] || null })),
      });
      // 容器报价已删除：回到项目页查看平级子报价
      router.push(`/projects/${quote.projectId}`);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "拆分失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-label="多方案拆分确认" className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <GitBranch className="text-primary" aria-hidden />
        <h2 className="text-base font-bold">
          识别到 {preview.planCount} 个方案，请逐个确认后拆分
        </h2>
      </div>
      <p className="text-muted-foreground text-xs">
        同一批文件识别到同一保险公司的多个报价方案。确认拆分会为每个保留的方案创建独立的待确认报价，
        并共享这批原文件；无效方案可在确认前丢弃。
      </p>

      {preview.plans.map((plan) => (
        <PlanCard
          key={plan.index}
          plan={plan}
          kept={kept[plan.index] ?? true}
          label={labels[plan.index] ?? ""}
          onKeptChange={(value) => setKept((state) => ({ ...state, [plan.index]: value }))}
          onLabelChange={(value) => setLabels((state) => ({ ...state, [plan.index]: value }))}
        />
      ))}

      {actionError ? (
        <p role="alert" className="text-destructive text-sm">
          {actionError}
        </p>
      ) : null}

      <Button
        className="h-11"
        disabled={submitting || keptCount === 0}
        onClick={() => void handleSplit()}
      >
        {submitting ? (
          <Loader2 className="animate-spin" aria-hidden />
        ) : (
          <GitBranch aria-hidden />
        )}
        {keptCount === 0
          ? "请至少保留一个方案"
          : `确认拆分，创建 ${keptCount} 份报价`}
      </Button>
    </section>
  );
}
