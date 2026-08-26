"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Keyboard } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { PageError } from "@/components/shared/page-error";
import { quotesApi } from "@/lib/api";
import { useDictionaries } from "@/lib/use-dictionaries";
import { cn } from "@/lib/utils";

/**
 * 添加报价（步骤 1）：保险公司九宫格 + 保险员（选填）+ 手动录入入口。
 *
 * TASK-02 只开放可工作的手动入口；上传报价单自动识别由 TASK-03 接通，
 * 在那之前不展示不可用的上传按钮或假解析入口。
 */
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

  const isOther = insurerCode === "OTHER";
  const canSubmit = insurerCode !== null && (!isOther || otherName.trim().length > 0);

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

          {formError ? (
            <p role="alert" className="text-destructive text-sm">
              {formError}
            </p>
          ) : null}

          <div className="flex flex-col gap-2">
            <Button
              size="lg"
              className="h-12 text-base"
              disabled={!canSubmit || submitting}
              onClick={() => void handleManualCreate()}
            >
              <Keyboard aria-hidden />
              {submitting ? "创建中…" : "跳过上传，手动录入"}
            </Button>
            <p className="text-muted-foreground text-center text-xs">
              手动录入将进入确认页空表单，可完整填写价格、险种、保障包与服务。
              上传报价单自动识别功能即将开放。
            </p>
          </div>
        </>
      )}
    </main>
  );
}
