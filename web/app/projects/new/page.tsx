"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ProjectForm, type ProjectFormValues } from "@/components/projects/project-form";
import { projectsApi, ValidationErrorApiError } from "@/lib/api";

/**
 * 新建项目：字段严格为项目名、车辆名称、续保年份、可选到期日与备注；
 * 创建成功后进入项目详情。
 */
export default function NewProjectPage() {
  const router = useRouter();
  const [serverError, setServerError] = React.useState<string | null>(null);

  async function handleSubmit(values: ProjectFormValues) {
    setServerError(null);
    try {
      const created = await projectsApi.create({
        name: values.name,
        vehicleName: values.vehicleName,
        renewalYear: values.renewalYear,
        expireDate: values.expireDate,
        note: values.note,
      });
      router.push(`/projects/${created.id}`);
    } catch (cause) {
      // 后端 422 已给出中文提示，直接展示；其他错误给统一文案
      if (cause instanceof ValidationErrorApiError) {
        setServerError(cause.message);
      } else {
        setServerError(cause instanceof Error ? cause.message : "创建失败，请稍后重试");
      }
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col px-4 pb-16 pt-6">
      <header className="flex items-center gap-3">
        <Button asChild variant="ghost" size="icon" aria-label="返回首页">
          <Link href="/">
            <ArrowLeft aria-hidden />
          </Link>
        </Button>
        <h1 className="text-xl font-bold">新建续保对比</h1>
      </header>

      <Card className="mt-5">
        <CardHeader>
          <CardTitle className="text-base">项目信息</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <p className="text-muted-foreground text-sm">
            车辆详细信息无需手动填写，后续会从报价单自动识别。
          </p>
          {serverError ? (
            <p role="alert" className="text-destructive text-sm">
              {serverError}
            </p>
          ) : null}
          <ProjectForm onSubmit={handleSubmit} submitLabel="创建项目" submittingLabel="创建中…" />
        </CardContent>
      </Card>
    </main>
  );
}
