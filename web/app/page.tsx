"use client";

import * as React from "react";
import Link from "next/link";
import { Plus, FolderOpen } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { PageError } from "@/components/shared/page-error";
import { ProjectCard } from "@/components/projects/project-card";
import { projectsApi } from "@/lib/api";

/**
 * 首页：我的续保项目。
 * 客户端组件 + 统一 API 客户端（访问令牌只在浏览器，服务端渲染拿不到）。
 */
export default function HomePage() {
  const [projects, setProjects] = React.useState<Awaited<ReturnType<typeof projectsApi.list>> | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  // 重试令牌：事件处理器里自增以重新触发加载
  const [reloadToken, setReloadToken] = React.useState(0);

  React.useEffect(() => {
    let cancelled = false;
    projectsApi
      .list()
      .then((data) => {
        if (!cancelled) {
          setProjects(data);
          setError(null);
        }
      })
      .catch((cause: unknown) => {
        // 首次加载失败时 projects 保持 null，由错误态接管展示
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "加载失败，请稍后重试");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col px-4 pb-10 pt-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold">我的续保项目</h1>
        <p className="text-muted-foreground text-sm">
          把保险员发来的报价都丢进来，一眼看懂区别
        </p>
      </header>

      <div className="mt-5">
        <Button asChild size="lg" className="w-full">
          <Link href="/projects/new">
            <Plus aria-hidden />
            新建续保对比
          </Link>
        </Button>
      </div>

      <section className="mt-6 flex flex-col gap-3" aria-label="项目列表">
        {error ? (
          <PageError message={error} onRetry={() => setReloadToken((token) => token + 1)} />
        ) : null}

        {!error && projects === null ? (
          <>
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </>
        ) : null}

        {!error && projects !== null && projects.length === 0 ? (
          <EmptyState
            icon={FolderOpen}
            title="还没有续保项目"
            description="创建一个项目开始收集报价：一个项目对应同一辆车的一次续保周期。"
            action={
              <Button asChild>
                <Link href="/projects/new">
                  <Plus aria-hidden />
                  创建第一个项目
                </Link>
              </Button>
            }
          />
        ) : null}

        {projects?.map((project) => <ProjectCard key={project.id} project={project} />)}
      </section>
    </main>
  );
}
