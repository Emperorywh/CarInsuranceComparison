"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, CalendarClock, Car, FilePlus2, Pencil, Scale, Trash2 } from "lucide-react";

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

/** 对比报价数量上限（与后端 COMPARE_TOO_MANY 口径一致） */
const COMPARE_LIMIT = 6;

/**
 * 项目详情：项目信息 + 编辑/删除 + 按“公司+保险员”分组的报价卡。
 * 添加报价入口 `/projects/[id]/quotes/new`（TASK-02 手动录入）。
 * TASK-06：报价勾选（按勾选顺序生成对比 URL）、同公司筛选与“开始对比”。
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
  // TASK-06：对比勾选（数组即勾选顺序）与同公司筛选
  const [selectedIds, setSelectedIds] = React.useState<number[]>([]);
  const [companyFilter, setCompanyFilter] = React.useState<string>("ALL");

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

  /** 勾选/取消：保持勾选顺序（数组顺序即对比页差异基准顺序） */
  function handleToggle(quoteId: number, checked: boolean) {
    setSelectedIds((current) =>
      checked
        ? current.includes(quoteId)
          ? current
          : [...current, quoteId]
        : current.filter((id) => id !== quoteId)
    );
  }

  /** 开始对比：按勾选顺序生成 URL，由对比页读取 quoteIds 查询参数 */
  function startCompare() {
    if (selectedIds.length < 2) return;
    router.push(`/projects/${projectId}/compare?quoteIds=${selectedIds.join(",")}`);
  }

  // 同公司筛选：组按公司码过滤（“全部”不过滤）；报价数据不变
  const visibleGroups = React.useMemo(() => {
    if (!project) return [];
    if (companyFilter === "ALL") return project.quoteGroups;
    return project.quoteGroups.filter((group) => group.insurerCode === companyFilter);
  }, [project, companyFilter]);

  const limitReached = selectedIds.length >= COMPARE_LIMIT;

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

            {project.quoteGroups.length > 1 ? (
              <div className="flex items-center gap-2">
                <label htmlFor="company-filter" className="text-muted-foreground shrink-0 text-sm">
                  只看公司
                </label>
                <select
                  id="company-filter"
                  value={companyFilter}
                  onChange={(event) => setCompanyFilter(event.target.value)}
                  className="border-input bg-background h-9 flex-1 rounded-xl border px-3 text-sm"
                >
                  <option value="ALL">全部公司</option>
                  {/* 同公司筛选口径 = 分组公司码；同名自由输入公司按 OTHER 码归并 */}
                  {Array.from(new Set(project.quoteGroups.map((g) => g.insurerCode))).map((code) => {
                    const name = project.quoteGroups.find((g) => g.insurerCode === code)?.insurerName ?? code;
                    return (
                      <option key={code} value={code}>
                        {name}
                      </option>
                    );
                  })}
                </select>
              </div>
            ) : null}

            {/* 对比操作条：显示已选数量与开始按钮（2–6 个可用，按勾选顺序） */}
            {project.quoteGroups.some((g) =>
              g.quotes.some((q) => q.status === "CONFIRMED" || q.status === "MERGE_REVIEW")
            ) ? (
              <div className="bg-muted/60 sticky bottom-2 z-10 flex items-center justify-between gap-3 rounded-2xl px-4 py-3">
                <span className="text-sm" aria-live="polite">
                  {selectedIds.length >= 2
                    ? `已选 ${selectedIds.length}/${COMPARE_LIMIT} 个报价`
                    : "勾选 2–6 个已确认报价开始对比"}
                  {selectedIds.length >= 1 && selectedIds.length < 2
                    ? `（已选 ${selectedIds.length} 个）`
                    : ""}
                </span>
                <Button size="sm" onClick={startCompare} disabled={selectedIds.length < 2}>
                  <Scale aria-hidden />
                  开始对比
                </Button>
              </div>
            ) : null}

            {visibleGroups.map((group) => (
              <QuoteGroupCard
                key={`${group.insurerCode}-${group.insurerName}-${group.agentName ?? ""}`}
                group={group}
                selection={{
                  selected: selectedIds,
                  onToggle: handleToggle,
                  limitReached,
                }}
              />
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
