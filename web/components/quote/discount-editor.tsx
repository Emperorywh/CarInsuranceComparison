"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { StatusBadge } from "@/components/quote/status-badge";
import type { QuoteEditorContext } from "@/components/quote/editor-context";
import { quotesApi, type DiscountRow } from "@/lib/api";
import { formatMoney } from "@/lib/format";

/**
 * 优惠编辑与净支出展示（报价详情页“填写优惠”区）。
 *
 * 净支出公式（后端确定性计算，前端如实展示）：
 * (官方总价 ?? 系统计算总价) − Σ(勾选计入且填了折现值的优惠)
 * - 名义金额只展示、不减钱；
 * - SERVICE 类（洗车/保养等）默认无折现值：勾选计入也不会自动折现；
 * - 折现合计大于基准总价时净支出显示“优惠超额，请修正”，不当 0。
 */

interface DiscountDraft {
  discountType: string;
  description: string;
  amount: string;
  cashEquivalent: string;
  includeInNet: boolean;
}

function draftOf(row: DiscountRow): DiscountDraft {
  return {
    discountType: row.discountType,
    description: row.description ?? "",
    amount: row.amount === null ? "" : String(row.amount),
    cashEquivalent: row.cashEquivalent === null ? "" : String(row.cashEquivalent),
    includeInNet: row.includeInNet,
  };
}

export function DiscountEditor({
  quote,
  saving,
  run,
  dict,
}: QuoteEditorContext & { dict: { discountTypes: Array<{ code: string; label: string }> } }) {
  const [drafts, setDrafts] = React.useState<Record<number, DiscountDraft>>({});
  const [adding, setAdding] = React.useState<DiscountDraft>({
    // 默认现金返现；SERVICE 类由用户主动选择，且不预填折现值
    discountType: "CASH",
    description: "",
    amount: "",
    cashEquivalent: "",
    includeInNet: true,
  });

  return (
    <Card id="discounts">
      <CardHeader>
        <CardTitle className="text-base">优惠与实际净支出</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex items-center justify-between rounded-xl bg-muted/60 px-4 py-3">
          <div className="flex flex-col">
            <span className="text-muted-foreground text-xs">实际净支出</span>
            <span className="text-lg font-bold">{formatMoney(quote.netPayment)}</span>
          </div>
          {quote.netPayment === null ? (
            <StatusBadge group="netPaymentStatus" value={quote.netPaymentStatus} />
          ) : null}
        </div>

        {quote.discounts.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            还没有优惠记录；返现、红包、购物卡等可折现优惠会计入净支出。
          </p>
        ) : null}

        {quote.discounts.map((row) => {
          const initial = draftOf(row);
          const draft = drafts[row.id] ?? initial;
          const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
          const typeLabel =
            dict.discountTypes.find((option) => option.code === draft.discountType)?.label ??
            draft.discountType;
          return (
            <div key={row.id} className="flex flex-col gap-2 rounded-xl border p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-medium">{draft.description || typeLabel}</p>
                <span className="text-muted-foreground text-xs">
                  {row.includeInNet && row.cashEquivalent !== null
                    ? `计入 −${formatMoney(row.cashEquivalent)}`
                    : "不计入净支出"}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <NativeSelect
                  aria-label="优惠类型"
                  value={draft.discountType}
                  onChange={(event) =>
                    setDrafts((prev) => ({
                      ...prev,
                      [row.id]: { ...draft, discountType: event.target.value },
                    }))
                  }
                >
                  {dict.discountTypes.map((option) => (
                    <option key={option.code} value={option.code}>
                      {option.label}
                    </option>
                  ))}
                </NativeSelect>
                <Input
                  aria-label="优惠说明"
                  placeholder="说明，如 微信红包"
                  value={draft.description}
                  onChange={(event) =>
                    setDrafts((prev) => ({
                      ...prev,
                      [row.id]: { ...draft, description: event.target.value },
                    }))
                  }
                />
                <Input
                  aria-label="名义金额（元）"
                  inputMode="decimal"
                  placeholder="名义金额（仅展示）"
                  value={draft.amount}
                  onChange={(event) =>
                    setDrafts((prev) => ({
                      ...prev,
                      [row.id]: { ...draft, amount: event.target.value },
                    }))
                  }
                />
                <Input
                  aria-label="折现估值（元）"
                  inputMode="decimal"
                  placeholder="折现估值（空则不减钱）"
                  value={draft.cashEquivalent}
                  onChange={(event) =>
                    setDrafts((prev) => ({
                      ...prev,
                      [row.id]: { ...draft, cashEquivalent: event.target.value },
                    }))
                  }
                />
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={draft.includeInNet}
                  onChange={(event) =>
                    setDrafts((prev) => ({
                      ...prev,
                      [row.id]: { ...draft, includeInNet: event.target.checked },
                    }))
                  }
                />
                计入净支出
              </label>
              <div className="flex justify-end gap-2">
                {dirty ? (
                  <Button
                    size="sm"
                    disabled={saving}
                    onClick={() =>
                      void run(() =>
                        quotesApi.updateDiscount(quote.id, row.id, {
                          discountType: draft.discountType,
                          description: draft.description.trim() || null,
                          amount: draft.amount.trim() === "" ? null : draft.amount.trim(),
                          cashEquivalent:
                            draft.cashEquivalent.trim() === ""
                              ? null
                              : draft.cashEquivalent.trim(),
                          includeInNet: draft.includeInNet,
                        } as never)
                      ).then((ok) => {
                        if (ok)
                          setDrafts((prev) => {
                            const next = { ...prev };
                            delete next[row.id];
                            return next;
                          });
                      })
                    }
                  >
                    保存
                  </Button>
                ) : null}
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-muted-foreground"
                  disabled={saving}
                  onClick={() => void run(() => quotesApi.deleteDiscount(quote.id, row.id))}
                >
                  删除
                </Button>
              </div>
            </div>
          );
        })}

        <form
          className="flex flex-col gap-2"
          aria-label="新增优惠"
          onSubmit={(event) => {
            event.preventDefault();
            void run(() =>
              quotesApi.createDiscount(quote.id, {
                discountType: adding.discountType,
                description: adding.description.trim() || null,
                amount: adding.amount.trim() === "" ? null : adding.amount.trim(),
                cashEquivalent:
                  adding.cashEquivalent.trim() === "" ? null : adding.cashEquivalent.trim(),
                includeInNet: adding.includeInNet,
              } as never)
            ).then((ok) => {
              if (ok) setAdding({ ...adding, description: "", amount: "", cashEquivalent: "" });
            });
          }}
        >
          <div className="grid grid-cols-2 gap-2">
            <NativeSelect
              aria-label="新增优惠类型"
              value={adding.discountType}
              onChange={(event) => setAdding({ ...adding, discountType: event.target.value })}
            >
              {dict.discountTypes.map((option) => (
                <option key={option.code} value={option.code}>
                  {option.label}
                </option>
              ))}
            </NativeSelect>
            <Input
              aria-label="新增优惠说明"
              placeholder="说明，如 微信红包"
              value={adding.description}
              onChange={(event) => setAdding({ ...adding, description: event.target.value })}
            />
            <Input
              aria-label="新增优惠名义金额（元）"
              inputMode="decimal"
              placeholder="名义金额（仅展示）"
              value={adding.amount}
              onChange={(event) => setAdding({ ...adding, amount: event.target.value })}
            />
            <Input
              aria-label="新增优惠折现估值（元）"
              inputMode="decimal"
              placeholder="折现估值（SERVICE 默认为空）"
              value={adding.cashEquivalent}
              onChange={(event) =>
                setAdding({ ...adding, cashEquivalent: event.target.value })
              }
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={adding.includeInNet}
              onChange={(event) =>
                setAdding({ ...adding, includeInNet: event.target.checked })
              }
            />
            计入净支出
          </label>
          <Button type="submit" variant="outline" size="sm" className="self-start" disabled={saving}>
            + 添加优惠
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
