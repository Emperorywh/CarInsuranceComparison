"use client";

import * as React from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { StatusBadge } from "@/components/quote/status-badge";
import { ConfidenceBadge } from "@/components/quote/confidence-badge";
import { FieldEvidenceLine } from "@/components/quote/evidence-chip";
import type { QuoteEditorContext } from "@/components/quote/editor-context";
import { quotesApi, type Quote } from "@/lib/api";
import { formatMoney } from "@/lib/format";

/**
 * 价格 Tab：六个价格分项（值 + 状态成对编辑）与汇总。
 *
 * 业务规则（后端强制，前端如实呈现）：
 * - 填写金额 → 分项即“已包含”；金额留空 + 选“不包含/未知” → 按状态参与计算；
 * - 系统绝不把空金额当 0：未知分项会让系统合计不可计算（显示“—”）；
 * - 官方总价与系统总价不一致时两者都保留并给出 MISMATCH 提示；
 * - 净支出为空时按状态显示“总价缺失/优惠超额”，不当 0。
 */

/** 分项定义：(字段前缀, 中文名, 是否有系统计算值) */
const PRICE_ITEMS: Array<{
  valueKey: keyof Quote & string;
  statusKey: keyof Quote & string;
  computedKey?: keyof Quote & string;
  label: string;
}> = [
  {
    valueKey: "commercialPremium",
    statusKey: "commercialStatus",
    computedKey: "computedCommercialPremium",
    label: "商业险合计",
  },
  { valueKey: "compulsoryPremium", statusKey: "compulsoryStatus", label: "交强险" },
  { valueKey: "vehicleTax", statusKey: "vehicleTaxStatus", label: "车船税" },
  {
    valueKey: "packageTotal",
    statusKey: "packageStatus",
    computedKey: "computedPackageTotal",
    label: "独立保障包合计",
  },
  { valueKey: "otherFees", statusKey: "otherFeesStatus", label: "其他费用" },
];

interface ItemDraft {
  value: string;
  status: string;
}

function draftOf(quote: Quote, item: (typeof PRICE_ITEMS)[number]): ItemDraft {
  const value = quote[item.valueKey] as number | null;
  const status = quote[item.statusKey] as string;
  return { value: value === null ? "" : String(value), status };
}

/** 分项证据字段名与值字段同名（后端 field_evidence 命名一致） */
function evidenceOf(quote: Quote, fieldName: string) {
  return quote.evidences.find((item) => item.fieldName === fieldName) ?? null;
}

export function PriceTab({ quote, saving, run, files, openEvidence }: QuoteEditorContext) {
  const buildDrafts = (source: Quote) =>
    Object.fromEntries(PRICE_ITEMS.map((item) => [item.valueKey, draftOf(source, item)]));
  const [drafts, setDrafts] = React.useState<Record<string, ItemDraft>>(() => buildDrafts(quote));
  const [officialTotal, setOfficialTotal] = React.useState(
    quote.officialTotal === null ? "" : String(quote.officialTotal)
  );

  // 报价刷新后（保存成功/服务端重算）重置本地草稿；
  // 采用“渲染期间调整派生状态”模式，避免 effect 级联渲染
  const [syncedQuote, setSyncedQuote] = React.useState(quote);
  if (syncedQuote !== quote) {
    setSyncedQuote(quote);
    setDrafts(buildDrafts(quote));
    setOfficialTotal(quote.officialTotal === null ? "" : String(quote.officialTotal));
  }

  const changed = React.useMemo(() => {
    const keys = new Set<string>();
    for (const item of PRICE_ITEMS) {
      if (JSON.stringify(drafts[item.valueKey]) !== JSON.stringify(draftOf(quote, item))) {
        keys.add(item.valueKey);
      }
    }
    if (officialTotal !== (quote.officialTotal === null ? "" : String(quote.officialTotal))) {
      keys.add("officialTotal");
    }
    return keys;
  }, [drafts, officialTotal, quote]);

  async function handleSave() {
    const payload: Record<string, unknown> = {};
    for (const item of PRICE_ITEMS) {
      if (!changed.has(item.valueKey)) continue;
      const draft = drafts[item.valueKey];
      const status = draft.status as "INCLUDED" | "NOT_INCLUDED" | "UNKNOWN";
      if (status === "INCLUDED") {
        // 已包含：金额为空时依赖系统计算值回退；填了金额则提交金额
        payload[item.valueKey] = draft.value.trim() === "" ? null : draft.value.trim();
        payload[item.statusKey] = "INCLUDED";
      } else {
        // 不包含 / 未知：金额必须清空（值⟺INCLUDED 不变量）
        payload[item.valueKey] = null;
        payload[item.statusKey] = status;
      }
    }
    if (changed.has("officialTotal")) {
      payload.officialTotal = officialTotal.trim() === "" ? null : officialTotal.trim();
    }
    if (Object.keys(payload).length === 0) return;
    await run(() => quotesApi.update(quote.id, payload as never));
  }

  return (
    <div className="flex flex-col gap-4">
      {PRICE_ITEMS.map((item) => {
        const draft = drafts[item.valueKey];
        const computed = item.computedKey ? (quote[item.computedKey] as number | null) : null;
        const evidence = evidenceOf(quote, item.valueKey);
        return (
          <Card key={item.valueKey}>
            <CardContent className="flex flex-col gap-3 pt-4">
              <div className="flex items-center justify-between gap-2">
                <Label className="text-sm font-semibold">{item.label}</Label>
                <div className="flex shrink-0 items-center gap-2">
                  <StatusBadge group="priceItemStatus" value={draft.status} />
                  <ConfidenceBadge
                    level={evidence?.confidenceLevel}
                    editedByUser={evidence?.editedByUser ?? false}
                  />
                </div>
              </div>
              <FieldEvidenceLine
                evidences={quote.evidences}
                fieldName={item.valueKey}
                files={files}
                onOpen={openEvidence}
              />
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1">
                  <Label htmlFor={`${item.valueKey}-value`} className="text-xs text-muted-foreground">
                    金额（元）
                  </Label>
                  <Input
                    id={`${item.valueKey}-value`}
                    aria-label={`${item.label}金额（元）`}
                    inputMode="decimal"
                    placeholder="如 4392.14"
                    value={draft.value}
                    disabled={draft.status !== "INCLUDED"}
                    onChange={(event) =>
                      setDrafts((prev) => ({
                        ...prev,
                        [item.valueKey]: { ...draft, value: event.target.value },
                      }))
                    }
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <Label
                    htmlFor={`${item.valueKey}-status`}
                    className="text-xs text-muted-foreground"
                  >
                    状态
                  </Label>
                  <NativeSelect
                    id={`${item.valueKey}-status`}
                    aria-label={`${item.label}状态`}
                    value={draft.status}
                    onChange={(event) =>
                      setDrafts((prev) => ({
                        ...prev,
                        [item.valueKey]: { ...draft, status: event.target.value },
                      }))
                    }
                  >
                    <option value="INCLUDED">已包含</option>
                    <option value="NOT_INCLUDED">不包含</option>
                    <option value="UNKNOWN">未知</option>
                  </NativeSelect>
                </div>
              </div>
              {item.computedKey ? (
                <p className="text-muted-foreground text-xs">
                  系统计算值：{formatMoney(computed)}
                  {computed === null ? "（明细保费完整且无未识别金额项时自动计算）" : ""}
                </p>
              ) : null}
            </CardContent>
          </Card>
        );
      })}

      {/* 官方总价：状态由是否有值自动决定（INCLUDED / UNKNOWN） */}
      <Card>
        <CardContent className="flex flex-col gap-3 pt-4">
          <div className="flex items-center justify-between gap-2">
            <Label className="text-sm font-semibold">报价单官方总价（元）</Label>
            <div className="flex shrink-0 items-center gap-2">
              <StatusBadge group="officialTotalStatus" value={quote.officialTotalStatus} />
              <ConfidenceBadge
                level={evidenceOf(quote, "officialTotal")?.confidenceLevel}
                editedByUser={evidenceOf(quote, "officialTotal")?.editedByUser ?? false}
              />
            </div>
          </div>
          <FieldEvidenceLine
            evidences={quote.evidences}
            fieldName="officialTotal"
            files={files}
            onOpen={openEvidence}
          />
          <Input
            aria-label="官方总价（元）"
            inputMode="decimal"
            placeholder="如 5785.14"
            value={officialTotal}
            onChange={(event) => setOfficialTotal(event.target.value)}
          />
        </CardContent>
      </Card>

      {/* 汇总：系统合计 / 总额校验 / 净支出 */}
      <Card className="border-primary/30 bg-primary/5">
        <CardHeader>
          <CardTitle className="text-sm">价格汇总</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">系统计算总价</span>
            <span className="font-semibold">{formatMoney(quote.computedTotal)}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">总额校验</span>
            <StatusBadge group="totalCheckStatus" value={quote.totalCheckStatus} />
          </div>
          {quote.totalCheckStatus === "MISMATCH" ? (
            <p role="note" className="text-destructive text-xs">
              官方总价与系统计算总价不一致，请核对分项金额；两者都会保留。
            </p>
          ) : null}
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">实际净支出</span>
            <span className="text-base font-bold">
              {formatMoney(quote.netPayment)}
              {quote.netPayment === null ? (
                <StatusBadge
                  group="netPaymentStatus"
                  value={quote.netPaymentStatus}
                  className="ml-2"
                />
              ) : null}
            </span>
          </div>
          <Button asChild variant="link" size="sm" className="self-start px-0">
            <Link href={`/quotes/${quote.id}#discounts`}>填写优惠 / 调整净支出 →</Link>
          </Button>
        </CardContent>
      </Card>

      <Button
        onClick={() => void handleSave()}
        disabled={saving || changed.size === 0}
        aria-label="保存价格分项"
      >
        {saving ? "保存中…" : changed.size > 0 ? "保存价格" : "无修改"}
      </Button>
    </div>
  );
}
