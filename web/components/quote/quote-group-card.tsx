"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/quote/status-badge";
import type { QuoteGroup } from "@/lib/api";
import { formatCoverageAmount, formatMoney } from "@/lib/format";

/** 可对比状态（与后端 COMPARABLE_STATUSES 同口径）：已确认 / 合并确认中 */
const COMPARABLE_STATUSES = new Set(["CONFIRMED", "MERGE_REVIEW"]);

export interface QuoteSelection {
  /** 已勾选报价 id，按勾选顺序排列（顺序即对比页差异基准顺序） */
  selected: number[];
  /** 勾选/取消勾选回调 */
  onToggle: (quoteId: number, checked: boolean) => void;
  /** 达到上限 6 个后其余勾选禁用（超出提示分批） */
  limitReached: boolean;
}

/**
 * 项目页报价分组卡片：按“保险公司 + 保险员”分组（决策 #9）。
 *
 * 同组多份报价只提示“同来源报价”（平级参与对比，MVP 不建版本链）；
 * 卡片显示净支出（null 时按状态标注“总价缺失/优惠超额”，不当 0）、
 * 官方总价异常、三者与三者医保外摘要。
 * TASK-06：已确认报价卡提供对比勾选；不可对比状态禁用勾选并说明原因。
 */
export function QuoteGroupCard({
  group,
  selection,
}: {
  group: QuoteGroup;
  selection?: QuoteSelection;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle className="truncate text-base">
          {group.insurerName}
          {group.agentName ? (
            <span className="text-muted-foreground font-normal"> · {group.agentName}</span>
          ) : null}
        </CardTitle>
        {group.sameSourceHint ? (
          <span className="bg-muted text-muted-foreground shrink-0 rounded-full px-2 py-0.5 text-xs">
            同来源报价
          </span>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {group.quotes.map((quote) => {
          const comparable = COMPARABLE_STATUSES.has(quote.status);
          const checked = selection?.selected.includes(quote.id) ?? false;
          // 上限禁用只作用于“未勾选”的卡片，已勾选的始终可取消
          const checkboxDisabled =
            !selection || !comparable || (selection.limitReached && !checked);
          const disableReason =
            comparable
              ? selection?.limitReached && !checked
                ? "最多对比 6 个报价"
                : "勾选加入对比"
              : "仅已确认报价可参与对比";
          return (
            <div key={quote.id} className="flex flex-col gap-2 rounded-xl border p-3">
              <div className="flex items-center justify-between gap-2">
                <label
                  className="flex min-w-0 flex-1 items-center gap-2"
                  title={disableReason}
                >
                  {selection ? (
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={checkboxDisabled}
                      onChange={(event) => selection.onToggle(quote.id, event.target.checked)}
                      aria-label={`勾选 ${quote.planLabel ?? `报价 #${quote.id}`} 加入对比`}
                      title={disableReason}
                      className="text-primary accent-current size-4 shrink-0"
                    />
                  ) : null}
                  <span className="min-w-0 truncate text-sm font-medium">
                    {quote.planLabel ?? `报价 #${quote.id}`}
                  </span>
                </label>
                <StatusBadge group="quoteStatus" value={quote.status} />
              </div>
              <div className="flex items-baseline justify-between">
                <span className="text-muted-foreground text-xs">实际净支出</span>
                <span className="text-lg font-bold">
                  {formatMoney(quote.netPayment)}
                  {quote.netPayment === null ? (
                    <StatusBadge
                      group="netPaymentStatus"
                      value={quote.netPaymentStatus}
                      className="ml-2 align-middle"
                    />
                  ) : null}
                </span>
              </div>
              {quote.totalCheckStatus === "MISMATCH" ? (
                <p role="note" className="text-amber-600 text-xs">
                  官方总价与系统计算不一致，请核对（官方 {formatMoney(quote.officialTotal)} / 系统{" "}
                  {formatMoney(quote.computedTotal)}）
                </p>
              ) : null}
              <div className="text-muted-foreground flex items-center justify-between text-xs">
                <span>
                  三者 {quote.thirdPartyAmount !== null ? formatCoverageAmount(quote.thirdPartyAmount) : "—"}
                  {" · "}
                  医保外{" "}
                  {quote.tpNonMedicalAmount !== null
                    ? formatCoverageAmount(quote.tpNonMedicalAmount)
                    : "—"}
                </span>
                <Button asChild variant="ghost" size="sm">
                  <Link href={`/quotes/${quote.id}`}>查看详情</Link>
                </Button>
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
