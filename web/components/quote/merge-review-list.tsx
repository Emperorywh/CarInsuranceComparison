"use client";

/**
 * MERGE_REVIEW 变更清单（TASK-05；SPEC §2.9、§8「补传合并预览」）。
 *
 * - 逐条展示旧值、新值、来源（文件序号·页码·摘录）与“用户已编辑”标识；
 * - 用户编辑项默认“保留旧值”（已确认数据永不静默覆盖），其余默认
 *   “采纳新值”，预选均可改；
 * - 全部裁决后一次性提交：服务端在单事务内合并、重算并回到 CONFIRMED；
 *   任何失败（含未全部裁决的 422）都不改变报价数据。
 */

import * as React from "react";
import { CheckCircle2, GitMerge, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PageError } from "@/components/shared/page-error";
import {
  quotesApi,
  type MergeChange,
  type MergePreview,
  type MergeResolutionChoice,
  type Quote,
} from "@/lib/api";
import { formatCoverageAmount, formatMoney } from "@/lib/format";

// 字段名 → 中文展示（与后端稳定业务键约定一致）
const FIELD_LABELS: Record<string, string> = {
  status: "状态",
  coverageAmount: "保额",
  perSeatAmount: "单座保额",
  seatCount: "座位数",
  sharedCoverage: "共享保额",
  premium: "保费",
  multiplier: "倍数",
  condition: "生效条件",
  description: "说明",
  count: "次数",
  cost: "费用",
  __row__: "整行",
  __rows__: "整组",
  __package__: "保障内容",
};

const ITEM_STATUS_LABELS: Record<string, string> = {
  INCLUDED: "已包含",
  NOT_INCLUDED: "不包含",
  FREE: "免费",
  NOT_APPLICABLE: "不适用",
  UNKNOWN: "未知",
};

const PRICE_STATUS_LABELS: Record<string, string> = {
  INCLUDED: "已包含",
  NOT_INCLUDED: "不包含",
  UNKNOWN: "未知",
};

// 保额类字段按“万”展示（3000000 → 500 万），其余数值按金额格式化
const AMOUNT_FIELDS = new Set(["coverageAmount", "perSeatAmount"]);

function formatScalar(value: unknown, fieldName?: string): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") {
    return fieldName != null && AMOUNT_FIELDS.has(fieldName)
      ? formatCoverageAmount(value)
      : formatMoney(value);
  }
  const text = String(value);
  return text === "" ? "—" : text;
}

/** 从快照值提取展示文本：字段级新值是 {value,...} 包装，其余原样。 */
function formatValue(change: MergeChange, side: "old" | "new"): string {
  const raw = side === "old" ? change.oldValue : change.newValue;
  if (raw === null || raw === undefined) return "—";
  if (typeof raw === "object" && "value" in (raw as Record<string, unknown>)) {
    const record = raw as Record<string, unknown>;
    const value = formatScalar(record.value, change.fieldName);
    const status =
      record.status != null
        ? `（${
            PRICE_STATUS_LABELS[String(record.status)] ?? String(record.status)
          }）`
        : "";
    return `${value}${status}`;
  }
  return formatScalar(raw, change.fieldName);
}

/** 行/包快照的摘要文本：名称 + 保额/保费 + 状态（保障包附内部行数）。 */
function summarizeRow(row: Record<string, unknown>): string {
  const name = (row.name ?? row.rawName ?? row.rawText ?? "") as string;
  const parts: string[] = [];
  if (row.coverageAmount != null) {
    parts.push(`保额 ${formatCoverageAmount(Number(row.coverageAmount))}`);
  }
  if (row.premium != null) parts.push(`保费 ${formatMoney(Number(row.premium))}`);
  if (row.cost != null) parts.push(`费用 ${formatMoney(Number(row.cost))}`);
  if (row.status != null) {
    parts.push(ITEM_STATUS_LABELS[String(row.status)] ?? String(row.status));
  }
  if (Array.isArray(row.coverages)) {
    parts.push(`含 ${(row.coverages as unknown[]).length} 项保障`);
  }
  return `${name}（${parts.join("，") || "无金额信息"}）`;
}

/** 变更一方的快照渲染：整组逐行列出，整行给摘要，标量走 formatValue。 */
function RowSummary({ change, side }: { change: MergeChange; side: "old" | "new" }) {
  const raw = side === "old" ? change.oldValue : change.newValue;
  if (raw === null || raw === undefined) return <span>—</span>;
  if (typeof raw !== "object") {
    return <span>{formatScalar(raw, change.fieldName)}</span>;
  }
  const record = raw as Record<string, unknown>;

  // 整组：{rows:[...]}（行/包组）或 {coverages:[...]}（保障包内部整组替换）
  const groupRows = (
    record.rows ?? (change.fieldName === "__package__" ? record.coverages : undefined)
  ) as Record<string, unknown>[] | undefined;
  if (Array.isArray(groupRows)) {
    return (
      <span>
        {groupRows.length} 行：
        {groupRows.map((row, index) => (
          <span key={index} className="block pl-2">
            · {summarizeRow(row)}
          </span>
        ))}
      </span>
    );
  }
  // 整行 ADD：快照本身就是行/包字典
  if (change.fieldName === "__row__") {
    return <span>{summarizeRow(record)}</span>;
  }
  return <span>{formatValue(change, side)}</span>;
}

/** 单条变更卡片：旧值 → 新值 + 来源 + 用户编辑标识 + 采纳/保留裁决。 */
function ChangeCard({
  change,
  resolution,
  fileOrdinal,
  onResolve,
}: {
  change: MergeChange;
  resolution: MergeResolutionChoice;
  fileOrdinal: number | null;
  onResolve: (resolution: MergeResolutionChoice) => void;
}) {
  const fieldLabel =
    FIELD_LABELS[change.fieldName] ??
    (change.fieldName === change.entityKey ? "" : change.fieldName);
  return (
    <Card>
      <CardContent className="flex flex-col gap-2 pt-4 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={
              change.kind === "ADD"
                ? "rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700"
                : "rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700"
            }
          >
            {change.kind === "ADD" ? "新增" : "冲突"}
          </span>
          <span className="font-semibold">
            {change.entityLabel}
            {fieldLabel ? ` · ${fieldLabel}` : ""}
          </span>
          {change.userEdited ? (
            <span className="rounded-full bg-sky-100 px-2 py-0.5 text-xs text-sky-700">
              用户已编辑
            </span>
          ) : null}
        </div>

        <div className="grid grid-cols-[auto_1fr] items-start gap-x-3 gap-y-1 text-xs">
          <span className="text-muted-foreground">旧值</span>
          <span>
            <RowSummary change={change} side="old" />
          </span>
          <span className="text-muted-foreground">新值</span>
          <span>
            <RowSummary change={change} side="new" />
          </span>
        </div>

        {change.sourceFileId != null ? (
          <p className="text-muted-foreground text-xs">
            来源：文件 {fileOrdinal ?? change.sourceFileId} · 第 {change.sourcePage ?? 1} 页
            {change.sourceText ? `「${change.sourceText}」` : ""}
          </p>
        ) : null}

        <div className="flex gap-4" role="radiogroup" aria-label={`裁决：${change.entityLabel}`}>
          {(
            [
              { value: "ACCEPT", label: "采纳新值" },
              { value: "KEEP", label: "保留旧值" },
            ] as const
          ).map((option) => (
            <label key={option.value} className="flex items-center gap-1.5 text-sm">
              <input
                type="radio"
                name={`change-${change.id}`}
                value={option.value}
                checked={resolution === option.value}
                onChange={() => onResolve(option.value)}
              />
              {option.label}
            </label>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

export function MergeReviewList({
  quote,
  files,
  onResolved,
}: {
  quote: Quote;
  files: Quote["files"];
  onResolved: (quote: Quote) => void;
}) {
  const [preview, setPreview] = React.useState<MergePreview | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [resolutions, setResolutions] = React.useState<
    Record<number, MergeResolutionChoice>
  >({});
  const [submitting, setSubmitting] = React.useState(false);
  const [actionError, setActionError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    quotesApi
      .getMergePreview(quote.id)
      .then((data) => {
        if (cancelled) return;
        setPreview(data);
        // 预选默认裁决：用户编辑 → KEEP；其余 → ACCEPT（均可改）
        setResolutions(
          Object.fromEntries(
            data.changes.map((change) => [change.id, change.defaultResolution])
          )
        );
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "合并预览加载失败");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [quote.id]);

  if (error) {
    return <PageError message={error} onRetry={() => window.location.reload()} />;
  }
  if (!preview) {
    return (
      <Card aria-label="合并预览加载中">
        <CardContent className="flex items-center gap-2 pt-4 text-sm text-muted-foreground">
          <Loader2 className="animate-spin" aria-hidden /> 正在加载合并变更…
        </CardContent>
      </Card>
    );
  }

  async function handleResolve() {
    if (!preview) return;
    setSubmitting(true);
    setActionError(null);
    try {
      const merged = await quotesApi.resolveMerge(
        quote.id,
        preview.changes.map((change) => ({
          changeId: change.id,
          resolution: resolutions[change.id] ?? change.defaultResolution,
        }))
      );
      onResolved(merged);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "合并失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  }

  const acceptCount = Object.values(resolutions).filter((v) => v === "ACCEPT").length;

  return (
    <section aria-label="合并变更确认" className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <GitMerge className="text-primary" aria-hidden />
        <h2 className="text-base font-bold">
          补传解析完成，共 {preview.changes.length} 项待确认变更
        </h2>
      </div>
      <p className="text-muted-foreground text-xs">
        逐项选择「采纳新值」或「保留旧值」；全部处理完成后报价才会回到已确认状态。
        您编辑过的内容默认保留，不会被自动覆盖。
      </p>

      {preview.changes.map((change) => (
        <ChangeCard
          key={change.id}
          change={change}
          resolution={resolutions[change.id] ?? change.defaultResolution}
          fileOrdinal={
            files.findIndex((file) => file.id === change.sourceFileId) >= 0
              ? files.findIndex((file) => file.id === change.sourceFileId) + 1
              : null
          }
          onResolve={(value) =>
            setResolutions((state) => ({ ...state, [change.id]: value }))
          }
        />
      ))}

      {actionError ? (
        <p role="alert" className="text-destructive text-sm">
          {actionError}
        </p>
      ) : null}

      <Button className="h-11" disabled={submitting} onClick={() => void handleResolve()}>
        {submitting ? (
          <Loader2 className="animate-spin" aria-hidden />
        ) : (
          <CheckCircle2 aria-hidden />
        )}
        完成合并（采纳 {acceptCount} / 保留{" "}
        {preview.changes.length - acceptCount}）
      </Button>
    </section>
  );
}
