"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { StatusBadge } from "@/components/quote/status-badge";
import { ConfidenceBadge } from "@/components/quote/confidence-badge";
import { EvidenceChip } from "@/components/quote/evidence-chip";
import type { QuoteEditorContext } from "@/components/quote/editor-context";
import { quotesApi, type Dictionaries, type PackageCoverage } from "@/lib/api";
import { formatCoverageAmount, formatMoney } from "@/lib/format";

/**
 * 额外保障 Tab：独立保障包及其内部保障。
 *
 * 铁律（SPEC §2.6/§6.5）：保障包内部保障（DRIVER_ACCIDENT 等）与
 * 商业车上人员责任险是完全不同的保障，内部类型码只来自 §3.3 码表；
 * 包价格不完整时 computedPackageTotal 保持不可计算，不当 0。
 */

interface PackageDraft {
  name: string;
  provider: string;
  premium: string;
  description: string;
}

function draftOfPackage(pkg: {
  name: string;
  provider?: string | null;
  premium?: number | null;
  description?: string | null;
}): PackageDraft {
  return {
    name: pkg.name,
    provider: pkg.provider ?? "",
    premium: pkg.premium == null ? "" : String(pkg.premium),
    description: pkg.description ?? "",
  };
}

interface CoverageDraft {
  type: string;
  name: string;
  coverageAmount: string;
  unit: string;
  multiplier: string;
  condition: string;
}

function draftOfCoverage(row: PackageCoverage): CoverageDraft {
  return {
    type: row.type,
    name: row.name ?? "",
    coverageAmount: row.coverageAmount === null ? "" : String(row.coverageAmount),
    unit: row.unit ?? "",
    multiplier: row.multiplier === null ? "" : String(row.multiplier),
    condition: row.condition ?? "",
  };
}

function PackageCoverageRow({
  row,
  dict,
  saving,
  onSave,
  onDelete,
}: {
  row: PackageCoverage;
  dict: Dictionaries;
  saving: boolean;
  onSave: (draft: CoverageDraft) => void;
  onDelete: () => void;
}) {
  const [draft, setDraft] = React.useState<CoverageDraft>(() => draftOfCoverage(row));
  const [confirmingDelete, setConfirmingDelete] = React.useState(false);
  // 行数据刷新后重置草稿（渲染期间调整派生状态，避免 effect 级联渲染）
  const [syncedRow, setSyncedRow] = React.useState(row);
  if (syncedRow !== row) {
    setSyncedRow(row);
    setDraft(draftOfCoverage(row));
  }
  const dirty = React.useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(draftOfCoverage(row)),
    [draft, row]
  );
  const typeLabel =
    dict.packageCoverageTypes.find((option) => option.code === draft.type)?.label ?? draft.type;

  return (
    <div className="rounded-xl border p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-medium">
          {draft.name || typeLabel}
          {draft.coverageAmount ? ` · ${formatCoverageAmount(Number(draft.coverageAmount))}` : ""}
        </p>
        <StatusBadge group="itemStatus" value={row.status} />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <NativeSelect
          aria-label="内部保障类型"
          value={draft.type}
          onChange={(event) => setDraft({ ...draft, type: event.target.value })}
        >
          {dict.packageCoverageTypes.map((option) => (
            <option key={option.code} value={option.code}>
              {option.label}
            </option>
          ))}
        </NativeSelect>
        <Input
          aria-label="内部保障名称"
          placeholder="保障名称"
          value={draft.name}
          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
        />
        <Input
          aria-label="内部保障保额（元）"
          inputMode="decimal"
          placeholder="保额（元）"
          value={draft.coverageAmount}
          onChange={(event) => setDraft({ ...draft, coverageAmount: event.target.value })}
        />
        <NativeSelect
          aria-label="内部保障单位"
          value={draft.unit}
          onChange={(event) => setDraft({ ...draft, unit: event.target.value })}
        >
          <option value="">单位未设置</option>
          {dict.packageUnits.map((option) => (
            <option key={option.code} value={option.code}>
              {option.label}
            </option>
          ))}
        </NativeSelect>
        <Input
          aria-label="内部保障翻倍系数"
          inputMode="decimal"
          placeholder="翻倍系数，如 2"
          value={draft.multiplier}
          onChange={(event) => setDraft({ ...draft, multiplier: event.target.value })}
        />
        <Input
          aria-label="内部保障生效条件"
          placeholder="条件，如 LEGAL_HOLIDAY"
          value={draft.condition}
          onChange={(event) => setDraft({ ...draft, condition: event.target.value })}
        />
      </div>
      <div className="mt-2 flex justify-end gap-2">
        {dirty ? (
          <Button size="sm" disabled={saving} onClick={() => onSave(draft as CoverageDraft)}>
            保存
          </Button>
        ) : null}
        {confirmingDelete ? (
          <>
            <Button size="sm" variant="destructive" disabled={saving} onClick={onDelete}>
              确认删除
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmingDelete(false)}>
              取消
            </Button>
          </>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => setConfirmingDelete(true)}>
            删除
          </Button>
        )}
      </div>
    </div>
  );
}

export function PackageTab({
  quote,
  saving,
  run,
  files,
  openEvidence,
  dict,
}: QuoteEditorContext & { dict: Dictionaries }) {
  const [newPackage, setNewPackage] = React.useState({ name: "", premium: "", description: "" });
  const [newCoverage, setNewCoverage] = React.useState<Record<number, { type: string; coverageAmount: string }>>(
    {}
  );

  return (
    <div className="flex flex-col gap-4">
      {quote.packages.map((pkg) => {
        const draft = draftOfPackage(pkg);
        return (
          <Card key={pkg.id}>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle className="text-sm">
                {pkg.name}
                <span className="text-muted-foreground ml-2 font-normal">
                  {formatMoney(pkg.premium)}
                </span>
              </CardTitle>
              <PackageEditor
                quoteId={quote.id}
                pkg={pkg}
                initialDraft={draft}
                saving={saving}
                run={run}
              />
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <ConfidenceBadge level={pkg.confidenceLevel} editedByUser={pkg.editedByUser} />
                <EvidenceChip
                  files={files}
                  source={{
                    sourceFileId: pkg.sourceFileId,
                    sourcePage: pkg.sourcePage,
                    sourceText: pkg.sourceText,
                  }}
                  onOpen={openEvidence}
                />
              </div>
              {pkg.coverages.map((row) => (
                <PackageCoverageRow
                  key={row.id}
                  row={row}
                  dict={dict}
                  saving={saving}
                  onSave={(payload) =>
                    void run(() =>
                      quotesApi.updatePackageCoverage(quote.id, pkg.id, row.id, {
                        type: payload.type,
                        name: payload.name || null,
                        coverageAmount:
                          String(payload.coverageAmount).trim() === ""
                            ? null
                            : String(payload.coverageAmount).trim(),
                        unit: payload.unit === "" ? null : (payload.unit as never),
                        multiplier:
                          String(payload.multiplier).trim() === ""
                            ? null
                            : String(payload.multiplier).trim(),
                        condition:
                          String(payload.condition).trim() === ""
                            ? null
                            : String(payload.condition).trim(),
                      } as never)
                    )
                  }
                  onDelete={() =>
                    void run(() =>
                      quotesApi.deletePackageCoverage(quote.id, pkg.id, row.id)
                    )
                  }
                />
              ))}
              <AddPackageCoverageForm
                dict={dict}
                saving={saving}
                value={newCoverage[pkg.id] ?? { type: "DRIVER_ACCIDENT", coverageAmount: "" }}
                onChange={(value) => setNewCoverage((prev) => ({ ...prev, [pkg.id]: value }))}
                onAdd={(payload) =>
                  void run(() =>
                    quotesApi.createPackageCoverage(quote.id, pkg.id, payload as never)
                  )
                }
              />
            </CardContent>
          </Card>
        );
      })}
      {quote.packages.length === 0 ? (
        <p className="text-muted-foreground text-sm">还没有保障包，如“车主尊享保障”，从下方添加。</p>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">新增保障包</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-col gap-3"
            aria-label="新增保障包"
            onSubmit={(event) => {
              event.preventDefault();
              if (!newPackage.name.trim()) return;
              void run(() =>
                quotesApi.createPackage(quote.id, {
                  name: newPackage.name.trim(),
                  premium: newPackage.premium.trim() === "" ? null : newPackage.premium.trim(),
                  description: newPackage.description.trim() || null,
                } as never)
              ).then((ok) => {
                if (ok) setNewPackage({ name: "", premium: "", description: "" });
              });
            }}
          >
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1">
                <Label htmlFor="new-package-name" className="text-xs text-muted-foreground">
                  名称
                </Label>
                <Input
                  id="new-package-name"
                  placeholder="如 车主尊享保障"
                  value={newPackage.name}
                  onChange={(event) =>
                    setNewPackage({ ...newPackage, name: event.target.value })
                  }
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="new-package-premium" className="text-xs text-muted-foreground">
                  价格（元）
                </Label>
                <Input
                  id="new-package-premium"
                  inputMode="decimal"
                  placeholder="如 348"
                  value={newPackage.premium}
                  onChange={(event) =>
                    setNewPackage({ ...newPackage, premium: event.target.value })
                  }
                />
              </div>
            </div>
            <Input
              aria-label="保障包说明"
              placeholder="说明（选填）"
              value={newPackage.description}
              onChange={(event) =>
                setNewPackage({ ...newPackage, description: event.target.value })
              }
            />
            <Button
              type="submit"
              variant="outline"
              size="sm"
              className="self-start"
              disabled={saving || !newPackage.name.trim()}
            >
              + 添加保障包
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

/** 保障包头部字段（名称/提供方/价格/说明）的展开编辑。 */
function PackageEditor({
  quoteId,
  pkg,
  initialDraft,
  saving,
  run,
}: {
  quoteId: number;
  pkg: {
    id: number;
    name: string;
    provider?: string | null;
    premium?: number | null;
    description?: string | null;
  };
  initialDraft: PackageDraft;
  saving: boolean;
  run: QuoteEditorContext["run"];
}) {
  const [open, setOpen] = React.useState(false);
  const [draft, setDraft] = React.useState(initialDraft);
  const [confirmingDelete, setConfirmingDelete] = React.useState(false);
  // 包数据刷新后重置草稿（渲染期间调整派生状态，避免 effect 级联渲染）
  const [syncedPkg, setSyncedPkg] = React.useState(pkg);
  if (syncedPkg !== pkg) {
    setSyncedPkg(pkg);
    setDraft(draftOfPackage(pkg));
  }

  if (!open) {
    return (
      <div className="flex shrink-0 gap-1">
        <Button size="sm" variant="ghost" onClick={() => setOpen(true)} aria-label="编辑保障包">
          编辑
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="text-muted-foreground"
          onClick={() => setConfirmingDelete(true)}
          aria-label="删除保障包"
        >
          删除
        </Button>
      </div>
    );
  }
  return (
    <div className="flex w-full flex-col gap-2 border-t pt-2">
      <div className="grid grid-cols-2 gap-2">
        <Input
          aria-label="保障包名称"
          value={draft.name}
          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
        />
        <Input
          aria-label="保障包价格（元）"
          inputMode="decimal"
          placeholder="价格（元）"
          value={draft.premium}
          onChange={(event) => setDraft({ ...draft, premium: event.target.value })}
        />
        <Input
          aria-label="保障包提供方"
          placeholder="提供方（选填）"
          value={draft.provider}
          onChange={(event) => setDraft({ ...draft, provider: event.target.value })}
        />
        <Input
          aria-label="保障包说明"
          placeholder="说明（选填）"
          value={draft.description}
          onChange={(event) => setDraft({ ...draft, description: event.target.value })}
        />
      </div>
      <div className="flex justify-end gap-2">
        {confirmingDelete ? (
          <>
            <Button
              size="sm"
              variant="destructive"
              disabled={saving}
              onClick={() =>
                void run(() => quotesApi.deletePackage(quoteId, pkg.id)).then((ok) => {
                  if (ok) setOpen(false);
                })
              }
            >
              确认删除
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmingDelete(false)}>
              取消
            </Button>
          </>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="text-muted-foreground"
            onClick={() => setConfirmingDelete(true)}
          >
            删除保障包
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          收起
        </Button>
        <Button
          size="sm"
          disabled={saving}
          onClick={() =>
            void run(() =>
              quotesApi.updatePackage(quoteId, pkg.id, {
                name: draft.name.trim() || pkg.name,
                provider: draft.provider.trim() || null,
                premium: draft.premium.trim() === "" ? null : draft.premium.trim(),
                description: draft.description.trim() || null,
              } as never)
            )
          }
        >
          保存
        </Button>
      </div>
    </div>
  );
}

function AddPackageCoverageForm({
  dict,
  saving,
  value,
  onChange,
  onAdd,
}: {
  dict: Dictionaries;
  saving: boolean;
  value: { type: string; coverageAmount: string };
  onChange: (value: { type: string; coverageAmount: string }) => void;
  onAdd: (payload: Record<string, unknown>) => void;
}) {
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      aria-label="添加保障包内部保障"
      onSubmit={(event) => {
        event.preventDefault();
        onAdd({
          type: value.type,
          coverageAmount: value.coverageAmount.trim() === "" ? null : value.coverageAmount.trim(),
          status: "INCLUDED",
        });
        onChange({ type: value.type, coverageAmount: "" });
      }}
    >
      <div className="flex min-w-36 flex-1 flex-col gap-1">
        <Label className="text-xs text-muted-foreground">内部保障类型</Label>
        <NativeSelect
          aria-label="新增内部保障类型"
          value={value.type}
          onChange={(event) => onChange({ ...value, type: event.target.value })}
        >
          {dict.packageCoverageTypes.map((option) => (
            <option key={option.code} value={option.code}>
              {option.label}
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="flex min-w-28 flex-1 flex-col gap-1">
        <Label className="text-xs text-muted-foreground">保额（元）</Label>
        <Input
          aria-label="新增内部保障保额（元）"
          inputMode="decimal"
          placeholder="如 300000"
          value={value.coverageAmount}
          onChange={(event) => onChange({ ...value, coverageAmount: event.target.value })}
        />
      </div>
      <Button type="submit" size="sm" variant="outline" disabled={saving}>
        + 添加内部保障
      </Button>
    </form>
  );
}
