"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, FileUp, Keyboard, Trash2, UploadCloud } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { PageError } from "@/components/shared/page-error";
import { projectsApi, quotesApi, uploadQuoteFiles } from "@/lib/api";
import { useDictionaries } from "@/lib/use-dictionaries";
import { cn } from "@/lib/utils";

/**
 * 添加报价：保险公司九宫格 + 保险员（选填）。
 *
 * 两条路径（SPEC §8 / 决策 #7“先建报价再传文件”）：
 * - 上传解析：选择文件 →（项目首次）模型传输同意 → 创建 UPLOADED 容器
 *   → 上传（202 + taskId）→ 进入报价详情轮询解析状态；
 * - 手动录入：跳过上传直接进确认页空表单（决策 #16）。
 * 拒绝模型传输不影响手动录入路径（隐私边界，SPEC §9.1）。
 */

// 与后端 validation.py 白名单一致：只接受 JPEG/PNG/PDF
const ACCEPTED_MIME = "image/jpeg,image/png,application/pdf";
const ACCEPTED_EXTENSIONS = [".jpg", ".jpeg", ".png", ".pdf"];
const MAX_FILES = 12;

export default function NewQuotePage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const router = useRouter();
  const { dict, error, retry } = useDictionaries();

  const [insurerCode, setInsurerCode] = React.useState<string | null>(null);
  const [otherName, setOtherName] = React.useState("");
  const [agentName, setAgentName] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [formError, setFormError] = React.useState<string | null>(null);

  // 上传相关状态
  const [files, setFiles] = React.useState<File[]>([]);
  const [uploading, setUploading] = React.useState(false);
  const [progress, setProgress] = React.useState(0);
  // 项目 modelConsentAt 为空时上传前必须弹出模型传输同意框
  const [consentOpen, setConsentOpen] = React.useState(false);
  const [consentLoading, setConsentLoading] = React.useState(false);

  const isOther = insurerCode === "OTHER";
  const canSubmit = insurerCode !== null && (!isOther || otherName.trim().length > 0);

  function addFiles(incoming: FileList | null) {
    if (!incoming) return;
    setFormError(null);
    // 客户端先行过滤扩展名白名单；真正的类型校验以后端签名校验为准
    const accepted: File[] = [];
    const rejectedNames: string[] = [];
    for (const file of Array.from(incoming)) {
      const lower = file.name.toLowerCase();
      const ok = ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
      if (ok) accepted.push(file);
      else rejectedNames.push(file.name);
    }
    if (rejectedNames.length > 0) {
      setFormError(
        `以下文件不是支持的格式（仅支持 JPEG、PNG、PDF）：${rejectedNames.join("、")}`
      );
    }
    setFiles((prev) => {
      const merged = [...prev, ...accepted];
      // 单报价文件数上限与后端一致；超出的部分静默忽略并在提示中说明
      if (merged.length > MAX_FILES) {
        setFormError(`单份报价最多上传 ${MAX_FILES} 个文件`);
        return merged.slice(0, MAX_FILES);
      }
      return merged;
    });
  }

  function removeFile(index: number) {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }

  /** 主按钮入口：有文件走上传解析，无文件走手动录入。 */
  function handleSubmit() {
    if (!canSubmit || submitting) return;
    if (files.length === 0) {
      void handleManualCreate();
      return;
    }
    // 首次解析必须先取得模型传输同意（后端仍会二次校验）
    setConsentLoading(true);
    projectsApi
      .get(projectId)
      .then((project) => {
        if (project.modelConsentAt) {
          void doUpload(true);
        } else {
          setConsentOpen(true);
        }
      })
      .catch((cause: unknown) => {
        setFormError(cause instanceof Error ? cause.message : "加载项目信息失败");
      })
      .finally(() => setConsentLoading(false));
  }

  async function doUpload(modelProcessingConsent: boolean) {
    if (!insurerCode) return;
    setConsentOpen(false);
    setFormError(null);
    setUploading(true);
    setProgress(0);
    try {
      // 决策 #7：先建报价（UPLOADED 只建 DRAFT 容器），再上传全部文件
      const quote = await quotesApi.create(projectId, {
        insurerCode,
        insurerName: isOther ? otherName.trim() : null,
        agentName: agentName.trim() || null,
        source: "UPLOADED",
      });
      await uploadQuoteFiles(quote.id, files, {
        modelProcessingConsent,
        onProgress: setProgress,
      });
      // 上传受理（202）后进入详情页轮询解析状态
      router.push(`/quotes/${quote.id}`);
    } catch (cause) {
      setFormError(cause instanceof Error ? cause.message : "上传失败，请稍后重试");
    } finally {
      setUploading(false);
    }
  }

  async function handleManualCreate() {
    if (!insurerCode) return;
    setFormError(null);
    setSubmitting(true);
    try {
      const quote = await quotesApi.create(projectId, {
        insurerCode,
        // 预置公司不带自定义名（后端取标准显示名）；OTHER 必须带自由输入名
        insurerName: isOther ? otherName.trim() : null,
        agentName: agentName.trim() || null,
        source: "MANUAL",
      });
      // 手动报价创建即进入待确认：直达确认页空表单（决策 #16）
      router.push(`/quotes/${quote.id}/confirm`);
    } catch (cause) {
      setFormError(cause instanceof Error ? cause.message : "创建失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col gap-5 px-4 pb-10 pt-6">
      <header className="flex items-center gap-3">
        <Button asChild variant="ghost" size="icon" aria-label="返回项目详情">
          <Link href={`/projects/${projectId}`}>
            <ArrowLeft aria-hidden />
          </Link>
        </Button>
        <h1 className="text-xl font-bold">添加报价</h1>
      </header>

      {error ? <PageError message={error} onRetry={retry} /> : null}

      {!dict ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">选择保险公司</CardTitle>
            </CardHeader>
            <CardContent>
              {/* 九宫格：预置 8 家 + 其他，选择项由后端字典驱动 */}
              <div className="grid grid-cols-3 gap-3" role="radiogroup" aria-label="保险公司">
                {dict.insurers.map((insurer) => {
                  const active = insurerCode === insurer.code;
                  return (
                    <button
                      key={insurer.code}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      onClick={() => setInsurerCode(insurer.code)}
                      className={cn(
                        "flex h-20 flex-col items-center justify-center gap-1 rounded-2xl border text-sm font-medium transition-colors",
                        active
                          ? "border-primary bg-primary/10 text-primary"
                          : "hover:bg-muted/60 border-border bg-background"
                      )}
                    >
                      {insurer.label}
                    </button>
                  );
                })}
              </div>
              {isOther ? (
                <div className="mt-3 flex flex-col gap-1">
                  <Label htmlFor="other-insurer-name">公司名称 *</Label>
                  <Input
                    id="other-insurer-name"
                    placeholder="输入保险公司名称"
                    value={otherName}
                    onChange={(event) => setOtherName(event.target.value)}
                    maxLength={100}
                  />
                </div>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">保险员（选填）</CardTitle>
            </CardHeader>
            <CardContent>
              <Input
                aria-label="保险员称呼"
                placeholder="如 小王；用于项目页按“公司+保险员”分组展示"
                value={agentName}
                onChange={(event) => setAgentName(event.target.value)}
                maxLength={50}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">上传报价单（可选）</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {/* 拖拽 + 点击选择；移动端 accept 会拉起相机/相册选项 */}
              <label
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault();
                  addFiles(event.dataTransfer.files);
                }}
                className={cn(
                  "flex cursor-pointer flex-col items-center gap-2 rounded-2xl border-2 border-dashed",
                  "border-border bg-muted/30 px-4 py-8 text-center transition-colors hover:bg-muted/50"
                )}
              >
                <UploadCloud className="text-primary" aria-hidden />
                <span className="text-sm font-medium">拖拽文件到此处，或点击选择</span>
                <span className="text-muted-foreground text-xs">
                  支持 JPEG、PNG、PDF；单文件不超过 20MB，最多 {MAX_FILES} 个；可调用相机拍摄
                </span>
                <input
                  type="file"
                  className="sr-only"
                  multiple
                  accept={ACCEPTED_MIME}
                  aria-label="选择报价单文件"
                  onChange={(event) => {
                    addFiles(event.target.files);
                    // 允许重复选择同一文件
                    event.target.value = "";
                  }}
                />
              </label>

              {files.length > 0 ? (
                <ul className="flex flex-col gap-2" aria-label="待上传文件列表">
                  {files.map((file, index) => (
                    <li
                      key={`${file.name}-${index}`}
                      className="flex items-center gap-2 rounded-xl bg-muted/40 px-3 py-2 text-sm"
                    >
                      <FileUp className="text-muted-foreground shrink-0" aria-hidden />
                      <span className="min-w-0 flex-1 truncate">{file.name}</span>
                      <span className="text-muted-foreground text-xs">
                        {(file.size / 1024 / 1024).toFixed(1)}MB
                      </span>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label={`移除 ${file.name}`}
                        onClick={() => removeFile(index)}
                      >
                        <Trash2 aria-hidden />
                      </Button>
                    </li>
                  ))}
                </ul>
              ) : null}

              {uploading ? (
                <div
                  role="progressbar"
                  aria-valuenow={progress}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label="上传进度"
                  className="h-2 w-full overflow-hidden rounded-full bg-muted"
                >
                  <div
                    className="bg-primary h-full transition-all"
                    style={{ width: `${progress}%` }}
                  />
                </div>
              ) : null}
            </CardContent>
          </Card>

          {formError ? (
            <p role="alert" className="text-destructive text-sm">
              {formError}
            </p>
          ) : null}

          <div className="flex flex-col gap-2">
            <Button
              size="lg"
              className="h-12 text-base"
              disabled={!canSubmit || submitting || uploading || consentLoading}
              onClick={handleSubmit}
            >
              {files.length > 0 ? <UploadCloud aria-hidden /> : <Keyboard aria-hidden />}
              {uploading
                ? `上传中… ${progress}%`
                : consentLoading
                  ? "加载中…"
                  : files.length > 0
                    ? "上传并自动识别"
                    : "跳过上传，手动录入"}
            </Button>
            <p className="text-muted-foreground text-center text-xs">
              {files.length > 0
                ? "上传后自动解析为结构化报价，解析期间可随时转手动录入。"
                : "手动录入将进入确认页空表单，可完整填写价格、险种、保障包与服务。"}
            </p>
          </div>

          {/* 首次模型传输同意（SPEC §9.1）：明确数据流，不同意可手动录入 */}
          <AlertDialog
            open={consentOpen}
            onOpenChange={(open) => {
              if (!open) setConsentOpen(false);
            }}
          >
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>将报价单发送至视觉模型解析？</AlertDialogTitle>
                <AlertDialogDescription asChild>
                  <div className="flex flex-col gap-2">
                    <span>
                      上传的报价单图片/PDF 原文件将发送至您所配置的视觉模型服务商用于识别报价内容，
                      不会发送到第三方对象存储。原文件可能包含个人信息，请确认您已同意此处理方式。
                    </span>
                    <span>
                      同意一次后本项目后续解析不再询问；拒绝仍可使用“手动录入”完成报价。
                    </span>
                  </div>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>暂不同意的，稍后可手动录入</AlertDialogCancel>
                <AlertDialogAction
                  disabled={uploading}
                  onClick={(event) => {
                    event.preventDefault();
                    void doUpload(true);
                  }}
                >
                  同意并开始上传
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </>
      )}
    </main>
  );
}
