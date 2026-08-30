"use client";

import { cn } from "@/lib/utils";

/**
 * 置信度三档徽标（SPEC §4.2 / §5.3）。
 *
 * 展示口径：
 * - 用户编辑过的字段一律显示“用户已确认”（绿色）——它的优先级高于模型
 *   置信度，不再把用户录入解释成“模型高置信”；
 * - LOW → 红色「请核对」（总额校验失败/证据非法/自报过低等）；
 * - MEDIUM → 黄色「置信度中」（无证据/未识别/触发提示等）；
 * - HIGH → 不渲染任何标记（SPEC 明确“无标记”）。
 */
export function ConfidenceBadge({
  level,
  editedByUser,
  className,
}: {
  level: string | null | undefined;
  editedByUser: boolean;
  className?: string;
}) {
  if (editedByUser) {
    return (
      <span
        className={cn(
          "shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-700",
          className
        )}
      >
        用户已确认
      </span>
    );
  }
  if (level === "LOW") {
    return (
      <span
        className={cn(
          "shrink-0 rounded-full bg-red-100 px-2 py-0.5 text-xs text-red-700",
          className
        )}
      >
        请核对
      </span>
    );
  }
  if (level === "MEDIUM") {
    return (
      <span
        className={cn(
          "shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-700",
          className
        )}
      >
        置信度中
      </span>
    );
  }
  // HIGH：无标记
  return null;
}
