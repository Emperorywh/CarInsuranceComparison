"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { Project } from "@/lib/api";

/** 表单值：字段严格为项目名、车辆名称、续保年份、可选到期日与备注 */
export interface ProjectFormValues {
  name: string;
  vehicleName: string;
  renewalYear: number;
  expireDate: string | null;
  note: string | null;
}

const currentYear = new Date().getFullYear();

function fromProject(project: Project): ProjectFormValues {
  return {
    name: project.name,
    vehicleName: project.vehicleName,
    renewalYear: project.renewalYear,
    expireDate: project.expireDate ?? null,
    note: project.note ?? null,
  };
}

/**
 * 新建/编辑项目共用表单。
 * 校验只做最小必填与范围提示；最终校验以后端 422 中文错误为准，
 * 避免前后端各维护一套规则导致口径漂移。
 */
export function ProjectForm({
  project,
  submitLabel = "创建",
  submittingLabel = "创建中…",
  onSubmit,
  onCancel,
}: {
  project?: Project;
  submitLabel?: string;
  submittingLabel?: string;
  onSubmit: (values: ProjectFormValues) => Promise<void>;
  onCancel?: () => void;
}) {
  const initial = React.useMemo(() => (project ? fromProject(project) : null), [project]);
  const [name, setName] = React.useState(initial?.name ?? "");
  const [vehicleName, setVehicleName] = React.useState(initial?.vehicleName ?? "");
  const [renewalYear, setRenewalYear] = React.useState(String(initial?.renewalYear ?? currentYear + 1));
  const [expireDate, setExpireDate] = React.useState(initial?.expireDate ?? "");
  const [note, setNote] = React.useState(initial?.note ?? "");
  const [submitting, setSubmitting] = React.useState(false);
  const [formError, setFormError] = React.useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const year = Number(renewalYear);
    if (!name.trim()) {
      setFormError("请填写项目名称");
      return;
    }
    if (!vehicleName.trim()) {
      setFormError("请填写车辆名称");
      return;
    }
    if (!Number.isInteger(year) || year < 2000 || year > 2100) {
      setFormError("续保年份需在 2000–2100 之间");
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit({
        name: name.trim(),
        vehicleName: vehicleName.trim(),
        renewalYear: year,
        expireDate: expireDate || null,
        note: note.trim() || null,
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5" noValidate>
      <div className="flex flex-col gap-2">
        <Label htmlFor="project-name">项目名称 *</Label>
        <Input
          id="project-name"
          placeholder="如：2026 车辆续保"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={100}
          required
        />
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="project-vehicle">车辆名称 *</Label>
        <Input
          id="project-vehicle"
          placeholder="如：Model Y"
          value={vehicleName}
          onChange={(event) => setVehicleName(event.target.value)}
          maxLength={100}
          required
        />
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="project-year">续保年份 *</Label>
        <Input
          id="project-year"
          type="number"
          inputMode="numeric"
          min={2000}
          max={2100}
          step={1}
          value={renewalYear}
          onChange={(event) => setRenewalYear(event.target.value)}
          required
        />
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="project-expire">保险到期时间（选填）</Label>
        <Input
          id="project-expire"
          type="date"
          value={expireDate ?? ""}
          onChange={(event) => setExpireDate(event.target.value)}
        />
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="project-note">备注（选填）</Label>
        <Textarea
          id="project-note"
          placeholder="补充说明，如多家比价注意事项"
          value={note ?? ""}
          onChange={(event) => setNote(event.target.value)}
          maxLength={2000}
          rows={3}
        />
      </div>

      {formError ? (
        <p role="alert" className="text-destructive text-sm">
          {formError}
        </p>
      ) : null}

      <div className="flex gap-3">
        <Button type="submit" className="flex-1" disabled={submitting}>
          {submitting ? submittingLabel : submitLabel}
        </Button>
        {onCancel ? (
          <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
            取消
          </Button>
        ) : null}
      </div>
    </form>
  );
}
