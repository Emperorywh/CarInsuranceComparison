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

        {/* 方案与价格总览：展示名/公司/净支出/官方总价/异常标注 */}
        <table className="mt-4 w-full border-collapse text-sm">
          <thead>
            <tr>
              <th className="w-28 border border-neutral-300 bg-neutral-100 px-2 py-2 text-left font-medium">
                实际净支出
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
                        对比基准
                      </span>
                    ) : null}
                    {plan.isPriceBaseline && !plan.isDiffBaseline ? (
                      <span className="ml-1 rounded-full bg-emerald-100 px-1.5 py-0.5 align-middle text-[10px] font-medium text-emerald-700">
                        价格基准
                      </span>
                    ) : null}
                  </p>
                  <p className="text-xs font-normal text-neutral-500">{plan.insurerName}</p>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="border border-neutral-300 px-2 py-2 font-medium">净支出</td>
              {data.plans.map((plan) => (
                <td key={plan.quoteId} className="border border-neutral-300 px-2 py-2 align-top">
                  <p className="text-lg font-bold">
                    {formatMoney(plan.netPayment)}
                  </p>
                  <p className="text-xs text-neutral-500">
                    官方总价 {formatMoney(plan.officialTotal)}
                  </p>
                  {plan.annotations.map((annotation) => (
                    <p key={annotation} className="text-xs leading-snug text-amber-600">
                      {annotation}
                    </p>
                  ))}
                </td>
              ))}
            </tr>
            {/* 价格表：商业险/交强/车船税/保障包/官方与系统总价/优惠与净支出 */}
            {data.priceRows.map((row) => (
              <tr key={`price-${row.label}`}>
                <td className="border border-neutral-300 px-2 py-1.5 text-neutral-600">
                  {row.label}
                </td>
                {row.cells.map((cell, index) => (
                  <td
                    key={`${row.label}-${index}`}
                    className="border border-neutral-300 px-2 py-1.5"
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

        {/* 五问总结（与服务端结构化口径一致的纯文本行） */}
        <section className="mt-5">
          <h2 className="text-lg font-bold">五问总结</h2>
          <div className="mt-2 flex flex-col gap-2">
            {data.questions.map((question) => (
              <div key={question.title} className="rounded-xl border border-neutral-200 px-3 py-2">
                <p className="text-sm font-semibold">{question.title}</p>
                <ul className="mt-1 space-y-0.5 text-sm leading-relaxed text-neutral-700">
                  {question.lines.map((line, index) => (
                    <li key={`${question.title}-${index}`}>{line}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>

        {/* 核心差异：各分区中服务端标记 diff 的行 */}
        <section className="mt-5">
          <h2 className="text-lg font-bold">核心差异</h2>
          {data.diffRows.length === 0 ? (
            <p className="mt-2 text-sm text-neutral-500">所选方案在结构化分区中无差异行。</p>
          ) : (
            <table className="mt-2 w-full border-collapse text-sm">
              <thead>
                <tr>
                  <th className="w-40 border border-neutral-300 bg-neutral-100 px-2 py-1.5 text-left font-medium">
                    差异项
                  </th>
                  {data.plans.map((plan) => (
                    <th
                      key={plan.quoteId}
                      className="border border-neutral-300 bg-neutral-100 px-2 py-1.5 text-left font-medium"
                    >
                      {plan.displayName}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.diffRows.map((row) => (
                  <tr key={`${row.sectionTitle}-${row.label}`}>
                    <td className="border border-neutral-300 px-2 py-1.5 text-neutral-600">
                      <span className="block text-xs text-neutral-400">{row.sectionTitle}</span>
                      {row.label}
                    </td>
                    {row.cells.map((cell, index) => (
                      <td
                        key={`${row.sectionTitle}-${row.label}-${index}`}
                        className="border border-neutral-300 px-2 py-1.5"
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
          )}
        </section>

        {/* 统一免责声明（SPEC §8：页面与导出长图共用同一文案） */}
        <footer className="mt-5 border-t border-neutral-200 pt-3">
          <p className="text-xs leading-relaxed text-neutral-500">{data.disclaimer}</p>
        </footer>
      </div>
    </div>
  );
}
