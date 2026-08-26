"use client";

import { AlertCircle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * 统一错误状态：展示后端中文错误信息与重试按钮。
 * 绝不渲染原始异常堆栈或令牌等敏感内容。
 */
export function PageError({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-4 rounded-2xl border border-destructive/30 bg-destructive/5 px-6 py-10 text-center"
    >
      <AlertCircle className="text-destructive size-8" aria-hidden />
      <div className="text-sm leading-relaxed">{message}</div>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw aria-hidden />
          重试
        </Button>
      ) : null}
    </div>
  );
}
