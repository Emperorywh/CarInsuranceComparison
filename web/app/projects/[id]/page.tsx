"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, CalendarClock, Car, FilePlus2, Pencil, Trash2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { PageError } from "@/components/shared/page-error";
import { ProjectForm, type ProjectFormValues } from "@/components/projects/project-form";
import { QuoteGroupCard } from "@/components/quote/quote-group-card";
import { projectsApi, type ProjectDetail } from "@/lib/api";
import { formatDate } from "@/lib/format";

/**
 * 项目详情：项目信息 + 编辑/删除 + 按“公司+保险员”分组的报价卡。
 * 添加报价入口 `/projects/[id]/quotes/new`（TASK-02 手动录入）。
 */
export default function ProjectDetailPage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const router = useRouter();

  const [project, setProject] = React.useState<ProjectDetail | null>(null);
  const [notFound, setNotFound] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [editing, setEditing] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [updateError, setUpdateError] = React.useState<string | null>(null);
  // 重试令牌：事件处理器里自增以重新触发加载
  const [reloadToken, setReloadToken] = React.useState(0);

  // 非法 id 直接按“不存在”渲染，不发请求
  const invalidId = !Number.isInteger(projectId);
  React.useEffect(() => {
    if (invalidId) return;
    let cancelled = false;
    projectsApi
      .get(projectId)
      .then((data) => {
        if (!cancelled) {
          setProject(data);
          setNotFound(false);
          setError(null);
        }
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        if (cause instanceof Error && cause.message.includes("不存在")) {
          setNotFound(true);
        } else {
          setError(cause instanceof Error ? cause.message : "加载失败，请稍后重试");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, invalidId, reloadToken]);

  async function handleUpdate(values: ProjectFormValues) {
    setUpdateError(null);
    try {
      const updated = await projectsApi.update(projectId, {
        name: values.name,
        vehicleName: values.vehicleName,
        renewalYear: values.renewalYear,
        expireDate: values.expireDate,
        note: values.note,
      });
      setProject(updated);
      setEditing(false);
    } catch (cause) {
      // 展示后端中文错误（422 校验 / 网络），保留编辑态供用户修正
      setUpdateError(cause instanceof Error ? cause.message : "保存失败，请稍后重试");
    }
  }

  async function handleDelete() {
    setDeleting(true);
    try {
      await projectsApi.remove(projectId);
      router.push("/");
    } finally {
      setDeleting(false);
    }
  }

  const header = (
    <header className="flex items-center gap-3">
      <Button asChild variant="ghost" size="icon" aria-label="返回项目列表">
        <Link href="/">
          <ArrowLeft aria-hidden />
        </Link>
      </Button>
      <h1 className="truncate text-xl font-bold">{project?.name ?? "项目详情"}</h1>
    </header>
  );

  if (notFound || invalidId) {
    return (
      <main className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col gap-5 px-4 pb-10 pt-6">
        {header}
        <EmptyState
          icon={Car}
          title="项目不存在或已被删除"
          description="可能已被删除，或链接不正确。"
          action={
            <Button asChild variant="outline">
              <Link href="/">返回项目列表</Link>
            </Button>
          }
        />
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col gap-5 px-4 pb-10 pt-6">
      {header}

      {error ? (
        <PageError message={error} onRetry={() => setReloadToken((token) => token + 1)} />
      ) : null}

      {!error && project === null ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : null}

      {project ? (
        <>
          {!editing ? (
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle className="text-base">项目信息</CardTitle>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setEditing(true)}
                    aria-label="编辑项目"
                  >
                    <Pencil aria-hidden />
                    编辑
                  </Button>
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button variant="destructive" size="sm" aria-label="删除项目">
                        <Trash2 aria-hidden />
                        删除
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>确定删除这个项目？</AlertDialogTitle>
                        <AlertDialogDescription>
                          将同时删除项目下的全部报价与文件记录，删除后不可恢复。
                          此操作无法撤销，请确认。
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>取消</AlertDialogCancel>
                        <AlertDialogAction
                          className="bg-destructive hover:bg-destructive/90"
                          disabled={deleting}
                          onClick={(event) => {
                            // 阻止弹层自动关闭，等删除完成后再由路由跳转离开
                            event.preventDefault();
                            void handleDelete();
                          }}
                        >
                          {deleting ? "删除中…" : "确认删除"}
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
                  <div className="flex justify-between gap-2 sm:block">
                    <dt className="text-muted-foreground">车辆名称</dt>
                    <dd className="font-medium">{project.vehicleName}</dd>
                  </div>
                  <div className="flex justify-between gap-2 sm:block">
                    <dt className="text-muted-foreground">续保年份</dt>
                    <dd className="font-medium">{project.renewalYear}</dd>
                  </div>
                  <div className="flex justify-between gap-2 sm:block">
                    <dt className="text-muted-foreground">保险到期</dt>
                    <dd className="flex items-center gap-1 font-medium">
                      <CalendarClock className="size-3.5 opacity-60" aria-hidden />
                      {formatDate(project.expireDate)}
                    </dd>
                  </div>
                </dl>
                {project.note ? (
                  <div className="bg-muted/60 rounded-xl px-4 py-3 text-sm leading-relaxed">
                    {project.note}
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">编辑项目信息</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                {updateError ? (
                  <p role="alert" className="text-destructive text-sm">
                    {updateError}
                  </p>
                ) : null}
                <ProjectForm
                  project={project}
                  submitLabel="保存修改"
                  submittingLabel="保存中…"
                  onSubmit={handleUpdate}
                  onCancel={() => setEditing(false)}
                />
              </CardContent>
            </Card>
          )}

          <section aria-label="报价列表" className="flex flex-col gap-3">
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-base font-semibold">
                报价{project.quoteGroups.length > 0 ? `（${project.quoteGroups.reduce((total, group) => total + group.quotes.length, 0)} 份）` : ""}
              </h2>
              <Button asChild size="sm" aria-label="添加报价">
                <Link href={`/projects/${projectId}/quotes/new`}>
                  <FilePlus2 aria-hidden />
                  添加报价
                </Link>
              </Button>
            </div>
            {project.quoteGroups.map((group) => (
              <QuoteGroupCard key={`${group.insurerCode}-${group.insurerName}-${group.agentName ?? ""}`} group={group} />
            ))}
            {project.quoteGroups.length === 0 ? (
              <EmptyState
                icon={FilePlus2}
                title="还没有报价"
                description="添加一份报价：选择保险公司后可手动录入全部信息，上传自动识别即将开放。"
                action={
                  <Button asChild>
                    <Link href={`/projects/${projectId}/quotes/new`}>添加报价</Link>
                  </Button>
                }
              />
            ) : null}
          </section>
        </>
      ) : null}
    </main>
  );
}
