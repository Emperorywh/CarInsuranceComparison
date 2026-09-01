"use client";

/**
 * 长图导出画布（TASK-07，SPEC §8「导出长图」）。
 *
 * 结构性隐私边界：
 * - 只渲染白名单 view model（web/lib/export-model.ts 的产出），
 *   不接收 CompareResult 原始对象，不克隆/截图页面任何现有区域；
 * - 保险员、车辆摘要、evidence、备注等字段在 view model 阶段已被
 *   白名单过滤，画布内从数据结构上无法出现；
 * - 画布平时挂在屏幕外（fixed 负偏移包裹层 + 正常文档流内容层），
 *   栅格化只针对内层节点：内层计算样式为 position:static，
 *   避免 html-to-image 把外层负偏移带进 foreignObject 导致空白图。
 *
 * 版式与对比页一致：单一总表（方案表头 + 全部指标行），差异行高亮。
 */
import * as React from "react";

import { DiffTagBadge } from "@/components/compare/diff-tag";
import type { ExportViewModel } from "@/lib/export-model";
import { formatMoney } from "@/lib/format";

export function ExportCanvas({
  data,
  containerRef,
}: {
  data: ExportViewModel;
  containerRef: React.RefObject<HTMLDivElement | null>;
}) {
  return (
    /* 包裹层只负责“移出屏幕但保留布局”；栅格化目标是内层白名单节点 */
    <div className="pointer-events-none fixed left-[-10000px] top-0" aria-hidden>
      <div
        ref={containerRef}
        data-testid="export-canvas"
        className="w-[750px] bg-white px-8 py-6 text-neutral-900"
      >
        {/* 页眉：固定标题 + 生成日期（不含项目名等用户数据） */}
        <div className="flex items-baseline justify-between border-b-2 border-neutral-900 pb-3">
          <h1 className="text-2xl font-bold">{data.title}</h1>
          <span className="text-sm text-neutral-500">{data.generatedOn}</span>
        </div>

        {/* 单一对比总表：方案表头 + 全部指标行（与页面表格同源同序） */}
        <table className="mt-4 w-full border-collapse text-sm">
          <thead>
            <tr>
              <th className="w-28 border border-neutral-300 bg-neutral-100 px-2 py-2 text-left text-xs font-medium text-neutral-500">
                指标
              </th>
              {data.plans.map((plan) => (
                <th
                  key={plan.quoteId}
                  className="border border-neutral-300 bg-neutral-100 px-2 py-2 text-left align-top"
                >
                  <p className="text-base font-bold">
                    {plan.displayName}
                    {plan.isDiffBaseline ? (
                      <span className="ml-1 rounded-full bg-indigo-100 px-1.5 py-0.5 align-middle text-[10px] font-medium text-indigo-700">
                        差异基准
                      </span>
                    ) : null}
                    {plan.isPriceBaseline && !plan.isDiffBaseline ? (
                      <span className="ml-1 rounded-full bg-emerald-100 px-1.5 py-0.5 align-middle text-[10px] font-medium text-emerald-700">
                        价格基准
                      </span>
                    ) : null}
                  </p>
                  <p className="text-xs font-normal text-neutral-500">{plan.insurerName}</p>
                  <p className="text-lg font-bold">{formatMoney(plan.netPayment)}</p>
                  {plan.annotations.map((annotation) => (
                    <p key={annotation} className="text-xs font-normal leading-snug text-amber-600">
                      {annotation}
                    </p>
                  ))}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={row.key} className={row.diff ? "bg-amber-50/60" : undefined}>
                <td className="border border-neutral-300 px-2 py-1.5 align-top text-neutral-600">
                  {row.label}
                  {row.note ? (
                    <span className="block text-[10px] leading-tight text-neutral-400">
                      {row.note}
                    </span>
                  ) : null}
                </td>
                {row.cells.map((cell, index) => (
                  <td
                    key={`${row.key}-${index}`}
                    className={`border border-neutral-300 px-2 py-1.5 align-top ${
                      cell.diff ? "font-medium" : ""
                    }`}
                  >
                    <span className="inline-flex items-center gap-1">
                      {cell.text}
                      {cell.tag && cell.tag !== "SAME" ? (
                        <DiffTagBadge tag={cell.tag} />
                      ) : null}
                    </span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>

        {/* 统一免责声明（SPEC §8：页面与导出长图共用同一文案） */}
        <footer className="mt-5 border-t border-neutral-200 pt-3">
          <p className="text-xs leading-relaxed text-neutral-500">{data.disclaimer}</p>
        </footer>
      </div>
    </div>
  );
}
