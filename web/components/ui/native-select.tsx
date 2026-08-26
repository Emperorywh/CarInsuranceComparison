"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * 原生 select 的统一样式封装。
 *
 * 选择项全部来自后端字典（/api/dictionaries），本组件只负责呈现，
 * 不内置任何业务选项，避免前后端字典漂移。
 */
export function NativeSelect({
  className,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "border-input bg-background ring-offset-background flex h-9 w-full rounded-xl border px-3 py-1 text-sm shadow-xs",
        "focus:ring-ring focus:outline-none focus:ring-2 focus:ring-offset-2",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    />
  );
}
