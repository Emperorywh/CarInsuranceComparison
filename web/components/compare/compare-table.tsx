"use client";

/**
 * 单一对比总表（对比页）：直接渲染服务端下发的全部指标行。
 *
 * - 首列指标名冻结 + 方案列横向滑动，方案列宽移动端约 44vw（桌面收窄）；
 * - 行 = 指标，行顺序保持服务端下发顺序（各分组内差异行已置顶）；
 * - 服务端已标 diff：差异行高亮，相同行默认折叠、一键展开；
 * - 两种基准在表头分别标注身份（差异基准=勾选顺序第一，价格基准=最低净支出）；
 * - 异常标注（官方总价异常/含用户估值/总价缺失/优惠超额/合并确认中）不得隐藏。
 */
import * as React from "react";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { DiffTagBadge } from "@/components/compare/diff-tag";
import type { CompareCell, CompareResult } from "@/lib/api";
import { formatMoney } from "@/lib/format";

/** 单个方案的表头卡片：名称 + 净支出 + 基准徽标 + 异常标注 */
function QuoteColumnHeader({
  result,
  quoteId,
}: {
  result: CompareResult;
  quoteId: number;
}) {
  const meta = result.quotes.find((quote) => quote.quoteId === quoteId);
  if (!meta) return null;
  const priceOrder = result.priceOrder.find((entry) => entry.quoteId === quoteId);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1">
        <span className="truncate text-sm font-bold">{meta.displayName}</span>
        {meta.isDiffBaseline ? (
          <span className="bg-primary/10 text-primary shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium">
            差异基准
          </span>
        ) : null}
        {meta.isPriceBaseline ? (
          <span className="bg-emerald-100 text-emerald-700 shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium">
            价格基准
          </span>
        ) : null}
      </div>
      <p className="text-muted-foreground truncate text-xs">
        {meta.insurerName}
        {meta.agentName ? ` · ${meta.agentName}` : ""}
        {priceOrder?.netPayment != null
          ? ` · 第 ${priceOrder.rank + 1} 低`
          : ""}
      </p>
      <p className="text-base font-bold">
        {formatMoney(priceOrder?.netPayment ?? null)}
      </p>
      {/* 异常/口径标注：官方总价异常、含用户估值、总价缺失等，服务端下发 */}
      {meta.annotations.length > 0 ? (
        <ul className="space-y-0.5">
          {meta.annotations.map((annotation) => (
            <li key={annotation} className="text-[10px] leading-tight text-amber-600">
              {annotation}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Cell({ cell }: { cell: CompareCell }) {
  return (
    <div
      className={`flex items-baseline justify-between gap-1 ${
        cell.diff ? "bg-amber-50 font-medium" : ""
      }`}
    >
      <span className="truncate">{cell.text}</span>
      {cell.tag && cell.tag !== "SAME" ? <DiffTagBadge tag={cell.tag} /> : null}
    </div>
  );
}

const SECTION_WIDTH = "min-w-[36px] w-24 shrink-0"; // 首列指标名

/** 单一对比总表：全部指标行合并展示，相同行默认折叠 */
export function CompareTables({ result }: { result: CompareResult }) {
  const rows = result.rows;
  const [showSameRows, setShowSameRows] = React.useState(false);
  const diffRows = rows.filter((row) => row.diff);
  const sameRows = rows.filter((row) => !row.diff);
  const visibleRows = showSameRows ? rows : diffRows;

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-end gap-2">
        {sameRows.length > 0 ? (
          <button
            type="button"
            onClick={() => setShowSameRows((value) => !value)}
            className="text-muted-foreground shrink-0 text-xs underline underline-offset-2"
            aria-expanded={showSameRows}
          >
            {showSameRows ? "收起相同项" : `展开相同项（${sameRows.length}）`}
          </button>
        ) : null}
      </CardHeader>
      <CardContent className="p-0">
        {/* 横向滚动容器：首列 sticky 冻结指标名，方案列宽 ~44vw */}
        <div className="overflow-x-auto" data-testid="compare-table">
          <div className="flex min-w-max flex-col gap-px pb-2">
            {/* 表头行（随列横滑） */}
            <div className="flex">
              <div className={`bg-background sticky left-0 z-10 px-2 py-2 ${SECTION_WIDTH}`}>
                <span className="text-muted-foreground text-xs">指标</span>
              </div>
              {result.quotes.map((quote) => (
                <div
                  key={quote.quoteId}
                  className="w-[44vw] shrink-0 px-2 py-2 sm:w-56"
                >
                  <QuoteColumnHeader result={result} quoteId={quote.quoteId} />
                </div>
              ))}
            </div>
            {/* 指标行 */}
            {visibleRows.map((row) => (
              <div
                key={row.key}
                className={`flex border-t ${row.diff ? "bg-amber-50/60" : ""}`}
                data-diff={row.diff}
              >
                <div
                  className={`sticky left-0 z-10 px-2 py-1.5 ${SECTION_WIDTH} ${
                    row.diff ? "bg-amber-50" : "bg-background"
                  }`}
                >
                  <span className="text-xs leading-snug">{row.label}</span>
                  {row.note ? (
                    <span className="text-muted-foreground block text-[10px] leading-tight">
                      {row.note}
                    </span>
                  ) : null}
                </div>
                {row.cells.map((cell, index) => (
                  <div
                    key={`${row.key}-${result.quotes[index]?.quoteId ?? index}`}
                    className="w-[44vw] shrink-0 px-2 py-1.5 sm:w-56"
                  >
                    <Cell cell={cell} />
                  </div>
                ))}
              </div>
            ))}
            {visibleRows.length === 0 ? (
              <p className="text-muted-foreground px-3 py-3 text-xs">
                各方案无差异行，可展开相同项查看全部指标。
              </p>
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
