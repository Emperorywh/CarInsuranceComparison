"use client";

import Link from "next/link";
import { Car, ChevronRight, FileText } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import type { ProjectListItem } from "@/lib/api";
import { formatMoney } from "@/lib/format";

/**
 * 首页项目卡片：项目名 + 报价数 + 最低净支出（无有效报价时稳定显示空状态文案）。
 */
export function ProjectCard({ project }: { project: ProjectListItem }) {
  return (
    <Link href={`/projects/${project.id}`} className="block focus-visible:outline-none">
      <Card className="gap-3 py-5 transition-shadow hover:shadow-[0_4px_18px_rgba(17,24,39,0.1)]">
        <CardContent className="flex items-center gap-4">
          <div className="bg-accent text-accent-foreground flex size-12 shrink-0 items-center justify-center rounded-2xl">
            <Car className="size-6" aria-hidden />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-base font-semibold">{project.name}</span>
              <span className="text-muted-foreground shrink-0 text-sm">{project.renewalYear}</span>
            </div>
            <div className="text-muted-foreground mt-1 flex items-center gap-1 text-sm">
              <FileText className="size-3.5" aria-hidden />
              {project.quoteCount > 0 ? (
                <span>
                  {project.quoteCount} 份报价
                  {project.minNetPayment !== null
                    ? ` · 最低 ${formatMoney(project.minNetPayment)}`
                    : " · 暂无有效总价"}
                </span>
              ) : (
                <span>还没有报价</span>
              )}
            </div>
          </div>
          <ChevronRight className="text-muted-foreground size-5 shrink-0" aria-hidden />
        </CardContent>
      </Card>
    </Link>
  );
}
