"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import type { EditorEvidenceSource, QuoteEditorContext } from "@/components/quote/editor-context";
import { ConfidenceBadge } from "@/components/quote/confidence-badge";
import { EvidenceChip } from "@/components/quote/evidence-chip";
import { quotesApi, type Coverage, type Dictionaries, type QuoteFile } from "@/lib/api";
import { formatCoverageAmount } from "@/lib/format";

/**
 * 险种 Tab：同一组件服务“基础车险”（category=CORE）与“附加险”（ADDITIONAL，
 * 底部附带“未识别保障”区）两个 Tab。
 *
 * 业务规则：
 * - 行类别由标准码推导，前端只提交 code，不提交 category；
 * - 未识别项处理三选一：映射（PATCH 补标准码）、丢弃（DELETE）、保留（不动，
 *   含金额的未识别项会阻断商业险合计计算，页面如实展示“系统计算值不可用”）；
 * - 座位总额规则：填了单座保额与座位数时，总保额为空则自动推导、
 *   不一致则后端 422（前端不做第二套校验，直接展示中文错误）。
 */

const ROW_STATUS_OPTIONS = [
  { value: "INCLUDED", label: "已包含" },
  { value: "NOT_INCLUDED", label: "不包含" },
  { value: "NOT_APPLICABLE", label: "不适用" },
  { value: "UNKNOWN", label: "未知" },
];

interface RowDraft {
  status: string;
  coverageAmount: string;
  perSeatAmount: string;
  seatCount: string;
  sharedCoverage: string; // "" 未设置 / "yes" / "no"
  premium: string;
  multiplier: string;
  condition: string;
  description: string;
}

function draftOfRow(row: Coverage): RowDraft {
  return {
    status: row.status,
    coverageAmount: row.coverageAmount === null ? "" : String(row.coverageAmount),
    perSeatAmount: row.perSeatAmount === null ? "" : String(row.perSeatAmount),
    seatCount: row.seatCount === null ? "" : String(row.seatCount),
    sharedCoverage: row.sharedCoverage === null ? "" : row.sharedCoverage ? "yes" : "no",
    premium: row.premium === null ? "" : String(row.premium),
    multiplier: row.multiplier === null ? "" : String(row.multiplier),
    condition: row.condition ?? "",
    description: row.description ?? "",
  };
}

function payloadOf(draft: RowDraft): Record<string, unknown> {
  // 空串统一转 null；单座/座位仅在填写时提交（缺失不得当 0）
  const optionalAmount = (text: string) => (text.trim() === "" ? null : text.trim());
  return {
    status: draft.status,
    coverageAmount: optionalAmount(draft.coverageAmount),
    perSeatAmount: optionalAmount(draft.perSeatAmount),
    seatCount: draft.seatCount.trim() === "" ? null : Number(draft.seatCount),
    sharedCoverage: draft.sharedCoverage === "" ? null : draft.sharedCoverage === "yes",
    premium: optionalAmount(draft.premium),
    multiplier: optionalAmount(draft.multiplier),
    condition: draft.condition.trim() === "" ? null : draft.condition.trim(),
    description: draft.description.trim() === "" ? null : draft.description.trim(),
  };
}

function CoverageRowCard({
  row,
  saving,
  files,
  openEvidence,
  onSave,
  onDelete,
}: {
  row: Coverage;
  saving: boolean;
  files: QuoteFile[];
  openEvidence?: (source: EditorEvidenceSource) => void;
  onSave: (payload: Record<string, unknown>) => void;
  onDelete: () => void;
}) {
  const [draft, setDraft] = React.useState<RowDraft>(() => draftOfRow(row));
  const [confirmingDelete, setConfirmingDelete] = React.useState(false);
  // 行数据刷新后重置草稿（渲染期间调整派生状态，避免 effect 级联渲染）
  const [syncedRow, setSyncedRow] = React.useState(row);
  if (syncedRow !== row) {
    setSyncedRow(row);
    setDraft(draftOfRow(row));
  }
  const dirty = React.useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(draftOfRow(row)),
    [draft, row]
  );

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <CardTitle className="truncate text-sm">{row.name}</CardTitle>
          <ConfidenceBadge level={row.confidenceLevel} editedByUser={row.editedByUser} />
        </div>
        {confirmingDelete ? (
          <div className="flex shrink-0 gap-1">
            <Button
              size="sm"
              variant="destructive"
              disabled={saving}
              onClick={onDelete}
              aria-label={`确认删除${row.name}`}
            >
              确认删除
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmingDelete(false)}>
              取消
            </Button>
          </div>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="text-muted-foreground shrink-0"
            aria-label={`删除${row.name}`}
            onClick={() => setConfirmingDelete(true)}
          >
            删除
          </Button>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {/* 来源定位（决策 #11）：点击切换到对应文件页并展示最短摘录 */}
        <EvidenceChip
          files={files}
          source={{
            sourceFileId: row.sourceFileId,
            sourcePage: row.sourcePage,
            sourceText: row.sourceText,
          }}
          onOpen={
            openEvidence
              ? (source) =>
                  openEvidence({
                    sourceFileId: source.sourceFileId,
                    sourcePage: source.sourcePage,
                  })
              : undefined
          }
        />
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-muted-foreground">状态</Label>
            <NativeSelect
              aria-label={`${row.name}状态`}
              value={draft.status}
              onChange={(event) => setDraft({ ...draft, status: event.target.value })}
            >
              {ROW_STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-muted-foreground">
              保额（元）{draft.coverageAmount ? `= ${formatCoverageAmount(Number(draft.coverageAmount))}` : ""}
            </Label>
            <Input
              aria-label={`${row.name}保额（元）`}
              inputMode="decimal"
              placeholder="如 3000000"
              value={draft.coverageAmount}
              onChange={(event) => setDraft({ ...draft, coverageAmount: event.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-muted-foreground">保费（元）</Label>
            <Input
              aria-label={`${row.name}保费（元）`}
              inputMode="decimal"
              placeholder="如 1237.41"
              value={draft.premium}
              onChange={(event) => setDraft({ ...draft, premium: event.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-muted-foreground">共享保额</Label>
            <NativeSelect
              aria-label={`${row.name}共享保额`}
              value={draft.sharedCoverage}
              onChange={(event) => setDraft({ ...draft, sharedCoverage: event.target.value })}
            >
              <option value="">未设置</option>
              <option value="yes">共享</option>
              <option value="no">不共享</option>
            </NativeSelect>
          </div>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-muted-foreground">单座保额（元）</Label>
            <Input
              aria-label={`${row.name}单座保额（元）`}
              inputMode="decimal"
              placeholder="如 10000"
              value={draft.perSeatAmount}
              onChange={(event) => setDraft({ ...draft, perSeatAmount: event.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-muted-foreground">座位数</Label>
            <Input
              aria-label={`${row.name}座位数`}
              inputMode="numeric"
              placeholder="如 4"
              value={draft.seatCount}
              onChange={(event) => setDraft({ ...draft, seatCount: event.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-muted-foreground">翻倍系数</Label>
            <Input
              aria-label={`${row.name}翻倍系数`}
              inputMode="decimal"
              placeholder="如 2（节假日翻倍）"
              value={draft.multiplier}
              onChange={(event) => setDraft({ ...draft, multiplier: event.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-muted-foreground">生效条件</Label>
            <Input
              aria-label={`${row.name}生效条件`}
              placeholder="如 LEGAL_HOLIDAY"
              value={draft.condition}
              onChange={(event) => setDraft({ ...draft, condition: event.target.value })}
            />
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-xs text-muted-foreground">说明</Label>
          <Input
            aria-label={`${row.name}说明`}
            value={draft.description}
            onChange={(event) => setDraft({ ...draft, description: event.target.value })}
          />
        </div>
        {row.amountRangeHint ? (
          <p role="note" className="text-amber-600 text-xs">
            {row.amountRangeHint}
          </p>
        ) : null}
        {dirty ? (
          <Button
            size="sm"
            className="self-end"
            disabled={saving}
            onClick={() => onSave(payloadOf(draft))}
            aria-label={`保存${row.name}`}
          >
            保存本行
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** 未识别保障：映射到标准险种 / 丢弃 / 保留（不动）三选一。 */
function UnrecognizedRow({
  row,
  dict,
  saving,
  onMap,
  onDiscard,
}: {
  row: Coverage;
  dict: Dictionaries;
  saving: boolean;
  onMap: (code: string) => void;
  onDiscard: () => void;
}) {
  const [code, setCode] = React.useState("");
  const [confirming, setConfirming] = React.useState(false);
  const selectable = dict.coverageCodes.filter((option) => option.rowSelectable);
  return (
    <Card className="border-amber-300 bg-amber-50/60">
      <CardContent className="flex flex-col gap-3 pt-4">
        <div className="flex items-center justify-between gap-2">
          <p className="min-w-0 truncate text-sm font-semibold">{row.rawName}</p>
          <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-700">
            未识别
          </span>
        </div>
        <p className="text-muted-foreground text-xs">
          {row.premium !== null ? `保费 ¥${row.premium}；` : ""}
          {row.coverageAmount !== null
            ? `保额 ${formatCoverageAmount(row.coverageAmount)}；`
            : ""}
          含金额的未识别项会阻断商业险合计计算，请处理
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <NativeSelect
            aria-label={`映射${row.rawName}到标准险种`}
            className="max-w-56"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          >
            <option value="">映射到标准险种…</option>
            {selectable.map((option) => (
              <option key={option.code} value={option.code}>
                {option.label}
              </option>
            ))}
          </NativeSelect>
          <Button
            size="sm"
            disabled={saving || code === ""}
            onClick={() => onMap(code)}
            aria-label={`确认映射${row.rawName}`}
          >
            映射
          </Button>
          {confirming ? (
            <>
              <Button size="sm" variant="destructive" disabled={saving} onClick={onDiscard}>
                确认丢弃
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                取消
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setConfirming(true)}
              aria-label={`丢弃${row.rawName}`}
            >
              丢弃
            </Button>
          )}
        </div>
        <p className="text-muted-foreground text-xs">暂不处理则保留为未识别项（不参与计算）。</p>
      </CardContent>
    </Card>
  );
}

function AddCoverageForm({
  dict,
  category,
  saving,
  onAdd,
}: {
  dict: Dictionaries;
  category: "CORE" | "ADDITIONAL";
  saving: boolean;
  onAdd: (payload: Record<string, unknown>) => void;
}) {
  const options = dict.coverageCodes.filter(
    (option) => option.rowSelectable && option.category === category
  );
  const [code, setCode] = React.useState("");
  const [coverageAmount, setCoverageAmount] = React.useState("");
  const [premium, setPremium] = React.useState("");
  const selected = options.find((option) => option.code === code);

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!code) return;
    onAdd({
      code,
      rawName: selected?.label ?? code,
      coverageAmount: coverageAmount.trim() === "" ? null : coverageAmount.trim(),
      premium: premium.trim() === "" ? null : premium.trim(),
      status: "INCLUDED",
    });
    setCode("");
    setCoverageAmount("");
    setPremium("");
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3" aria-label="新增险种行">
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor="add-coverage-code" className="text-xs text-muted-foreground">
            险种
          </Label>
          <NativeSelect
            id="add-coverage-code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          >
            <option value="">选择险种…</option>
            {options.map((option) => (
              <option key={option.code} value={option.code}>
                {option.label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="add-coverage-amount" className="text-xs text-muted-foreground">
            保额（元）
          </Label>
          <Input
            id="add-coverage-amount"
            inputMode="decimal"
            placeholder="如 3000000"
            value={coverageAmount}
            onChange={(event) => setCoverageAmount(event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="add-coverage-premium" className="text-xs text-muted-foreground">
            保费（元）
          </Label>
          <Input
            id="add-coverage-premium"
            inputMode="decimal"
            placeholder="如 1237.41"
            value={premium}
            onChange={(event) => setPremium(event.target.value)}
          />
        </div>
      </div>
      <Button type="submit" variant="outline" size="sm" className="self-start" disabled={saving || !code}>
        + 添加{category === "CORE" ? "基础车险" : "附加险"}
      </Button>
    </form>
  );
}

export function CoverageTab({
  quote,
  saving,
  run,
  files,
  openEvidence,
  dict,
  category,
}: QuoteEditorContext & { dict: Dictionaries; category: "CORE" | "ADDITIONAL" }) {
  const formalRows = quote.coverages.filter((row) => row.category === category);
  const unrecognizedRows =
    category === "ADDITIONAL"
      ? quote.coverages.filter((row) => row.category === "UNRECOGNIZED")
      : [];

  return (
    <div className="flex flex-col gap-4">
      {formalRows.map((row) => (
        <CoverageRowCard
          key={row.id}
          row={row}
          saving={saving}
          files={files}
          openEvidence={openEvidence}
          onSave={(payload) =>
            void run(() => quotesApi.updateCoverage(quote.id, row.id, payload as never))
          }
          onDelete={() => void run(() => quotesApi.deleteCoverage(quote.id, row.id))}
        />
      ))}
      {formalRows.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          还没有{category === "CORE" ? "基础车险" : "附加险"}记录，从下方添加。
        </p>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">
            新增{category === "CORE" ? "基础车险" : "附加险"}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <AddCoverageForm
            dict={dict}
            category={category}
            saving={saving}
            onAdd={(payload) =>
              void run(() => quotesApi.createCoverage(quote.id, payload as never))
            }
          />
        </CardContent>
      </Card>

      {category === "ADDITIONAL" && unrecognizedRows.length > 0 ? (
        <section aria-label="未识别保障" className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold">未识别保障</h3>
          <p className="text-muted-foreground text-xs">
            无法自动归类的保障条目；映射到标准险种后参与对比，或丢弃。
          </p>
          {unrecognizedRows.map((row) => (
            <UnrecognizedRow
              key={row.id}
              row={row}
              dict={dict}
              saving={saving}
              onMap={(code) =>
                void run(() =>
                  quotesApi.updateCoverage(quote.id, row.id, { code } as never)
                )
              }
              onDiscard={() => void run(() => quotesApi.deleteCoverage(quote.id, row.id))}
            />
          ))}
        </section>
      ) : null}
    </div>
  );
}
