import { cn } from "@/lib/utils"

/**
 * 骨架屏占位：加载状态统一用它，避免页面跳动。
 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("bg-muted animate-pulse rounded-xl", className)}
      {...props}
    />
  )
}

export { Skeleton }
