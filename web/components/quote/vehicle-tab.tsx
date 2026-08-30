"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import type { QuoteEditorContext } from "@/components/quote/editor-context";
import { FieldEvidenceLine } from "@/components/quote/evidence-chip";
import { quotesApi } from "@/lib/api";

/**
 * 车辆信息 Tab：报价自己的车辆快照 + 与项目摘要的冲突处理。
 *
 * 冲突规则（SPEC §6.10）：车型/座位数/新能源属性任一冲突时，确认前必须
 * 显式二选一（以报价为准 / 以项目为准）；初登日期差异只提示、不阻断。
 * 冲突选择状态提升到确认页（底部确认按钮共用）。
 */

export type ConflictResolution = "USE_QUOTE" | "KEEP_PROJECT";

interface VehicleDraft {
  vehicleModel: string;
  vehicleSeats: string;
  firstRegDate: string;
  isNev: string; // "" 未填 / "yes" / "no"
}

export function VehicleTab({
  quote,
  saving,
  run,
  files,
  openEvidence,
  resolution,
  onResolutionChange,
}: QuoteEditorContext & {
  resolution: ConflictResolution | null;
  onResolutionChange: (value: ConflictResolution | null) => void;
}) {
  const buildDraft = (source: typeof quote): VehicleDraft => ({
    vehicleModel: source.vehicleModel ?? "",
    vehicleSeats: source.vehicleSeats === null ? "" : String(source.vehicleSeats),
    firstRegDate: source.firstRegDate ?? "",
    isNev: source.isNev === null ? "" : source.isNev ? "yes" : "no",
  });
  const [draft, setDraft] = React.useState<VehicleDraft>(() => buildDraft(quote));
  // 报价刷新后重置草稿（渲染期间调整派生状态，避免 effect 级联渲染）
  const [syncedQuote, setSyncedQuote] = React.useState(quote);
  if (syncedQuote !== quote) {
    setSyncedQuote(quote);
    setDraft(buildDraft(quote));
  }

  const initial: VehicleDraft = {
    vehicleModel: quote.vehicleModel ?? "",
    vehicleSeats: quote.vehicleSeats === null ? "" : String(quote.vehicleSeats),
    firstRegDate: quote.firstRegDate ?? "",
    isNev: quote.isNev === null ? "" : quote.isNev ? "yes" : "no",
  };
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  // vehicleConflict 为可空字段：组装时总是提供，类型上仍按可空防御
  const conflict =
    quote.vehicleConflict ?? { fields: [], firstRegDateDiffers: false, resolutionRequired: false };

  const fieldLabels: Record<string, string> = {
    vehicleModel: "车型",
    vehicleSeats: "座位数",
    isNev: "新能源属性",
  };

  async function handleSave() {
    await run(() =>
      quotesApi.update(quote.id, {
        vehicleModel: draft.vehicleModel.trim() || null,
        vehicleSeats: draft.vehicleSeats.trim() === "" ? null : Number(draft.vehicleSeats),
        firstRegDate: draft.firstRegDate.trim() || null,
        isNev: draft.isNev === "" ? null : draft.isNev === "yes",
      } as never)
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardContent className="flex flex-col gap-3 pt-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="vehicle-model" className="text-xs text-muted-foreground">
                车型
              </Label>
              <Input
                id="vehicle-model"
                placeholder="如 Model Y"
                value={draft.vehicleModel}
                onChange={(event) => setDraft({ ...draft, vehicleModel: event.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="vehicle-seats" className="text-xs text-muted-foreground">
                座位数
              </Label>
              <Input
                id="vehicle-seats"
                inputMode="numeric"
                placeholder="如 5"
                value={draft.vehicleSeats}
                onChange={(event) => setDraft({ ...draft, vehicleSeats: event.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="first-reg-date" className="text-xs text-muted-foreground">
                初登月份
              </Label>
              <Input
                id="first-reg-date"
                type="month"
                value={draft.firstRegDate}
                onChange={(event) => setDraft({ ...draft, firstRegDate: event.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="is-nev" className="text-xs text-muted-foreground">
                是否新能源
              </Label>
              <NativeSelect
                id="is-nev"
                value={draft.isNev}
                onChange={(event) => setDraft({ ...draft, isNev: event.target.value })}
              >
                <option value="">未填写</option>
                <option value="yes">是</option>
                <option value="no">否</option>
              </NativeSelect>
            </div>
          </div>
          {dirty ? (
            <Button size="sm" className="self-end" disabled={saving} onClick={() => void handleSave()}>
              保存车辆信息
            </Button>
          ) : null}
          {/* 各车辆字段的来源定位（解析候选才有；用户录入字段无来源） */}
          <div className="flex flex-wrap gap-2">
            {(["vehicleModel", "vehicleSeats", "firstRegDate", "isNev"] as const).map(
              (fieldName) => (
                <FieldEvidenceLine
                  key={fieldName}
                  evidences={quote.evidences}
                  fieldName={fieldName}
                  files={files}
                  onOpen={openEvidence}
                />
              )
            )}
          </div>
        </CardContent>
      </Card>

      {conflict.resolutionRequired ? (
        <div
          role="alert"
          className="flex flex-col gap-3 rounded-xl border border-red-300 bg-red-50 px-4 py-3"
        >
          <p className="text-sm font-semibold text-red-700">
            车辆信息与项目摘要不一致，确认前请选择处理方式
          </p>
          <ul className="text-xs text-red-700">
            {conflict.fields.map((field) => (
              <li key={field}>· {fieldLabels[field] ?? field} 与项目摘要不同</li>
            ))}
          </ul>
          <div className="flex flex-col gap-2">
            {(
              [
                { value: "USE_QUOTE", label: "以本报价为准（更新项目车辆摘要）" },
                { value: "KEEP_PROJECT", label: "以项目摘要为准（保留，报价快照不变）" },
              ] as const
            ).map((option) => (
              <label key={option.value} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="vehicle-conflict-resolution"
                  value={option.value}
                  checked={resolution === option.value}
                  onChange={() => onResolutionChange(option.value)}
                />
                {option.label}
              </label>
            ))}
          </div>
        </div>
      ) : null}

      {conflict.firstRegDateDiffers && !conflict.resolutionRequired ? (
        <p role="note" className="text-amber-600 text-xs">
          初登月份与项目摘要不同（仅提示，不阻断确认）。
        </p>
      ) : null}
    </div>
  );
}
