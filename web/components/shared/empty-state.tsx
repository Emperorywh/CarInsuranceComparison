import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * 统一空状态：图标 + 主文案 + 引导文案（可选操作区）。
 * 移动优先，居中留白。
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border bg-card/60 px-6 py-12 text-center",
        className
      )}
    >
      <div className="bg-accent text-accent-foreground flex size-14 items-center justify-center rounded-2xl">
        <Icon className="size-7" aria-hidden />
      </div>
      <div className="text-base font-semibold">{title}</div>
      {description ? (
        <p className="text-muted-foreground max-w-xs text-sm leading-relaxed">{description}</p>
      ) : null}
      {action ? <div className="pt-2">{action}</div> : null}
    </div>
  );
}
