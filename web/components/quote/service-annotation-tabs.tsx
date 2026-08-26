"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { StatusBadge } from "@/components/quote/status-badge";
import type { QuoteEditorContext } from "@/components/quote/editor-context";
import { quotesApi, type Dictionaries } from "@/lib/api";

/**
 * 增值服务 Tab 与销售说明 Tab。
 *
 * 状态语义（SPEC §6.6）：只有明确 0 元费用才选“免费”；费用未知的
 * 已包含服务仍算 INCLUDED。销售说明默认不参与结构化对比与金额计算，
 * 页面固定展示隔离提示文案。
 */

const SERVICE_STATUS_OPTIONS = [
  { value: "INCLUDED", label: "已包含" },
  { value: "FREE", label: "免费" },
  { value: "NOT_INCLUDED", label: "不包含" },
  { value: "UNKNOWN", label: "未知" },
];

interface ServiceDraft {
  serviceType: string;
  status: string;
  count: string;
  cost: string;
  description: string;
}

export function ServiceTab({
  quote,
  saving,
  run,
  dict,
}: QuoteEditorContext & { dict: Dictionaries }) {
  const [drafts, setDrafts] = React.useState<Record<number, ServiceDraft>>({});
  const [adding, setAdding] = React.useState<ServiceDraft>({
    serviceType: "ROAD_RESCUE",
    status: "FREE",
    count: "",
    cost: "",
    description: "",
  });

  return (
    <div className="flex flex-col gap-4">
      {quote.services.map((row) => {
        const initial: ServiceDraft = {
          serviceType: row.serviceType,
          status: row.status,
          count: row.count === null ? "" : String(row.count),
          cost: row.cost === null ? "" : String(row.cost),
          description: row.description ?? "",
        };
        const draft = drafts[row.id] ?? initial;
        const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
        return (
          <Card key={row.id}>
            <CardContent className="flex flex-col gap-3 pt-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1">
                  <Label className="text-xs text-muted-foreground">服务类型</Label>
                  <NativeSelect
                    aria-label="服务类型"
                    value={draft.serviceType}
                    onChange={(event) =>
                      setDrafts((prev) => ({
                        ...prev,
                        [row.id]: { ...draft, serviceType: event.target.value },
                      }))
                    }
                  >
                    {dict.serviceTypes.map((option) => (
                      <option key={option.code} value={option.code}>
                        {option.label}
                      </option>
                    ))}
                  </NativeSelect>
                </div>
                <div className="flex flex-col gap-1">
                  <Label className="text-xs text-muted-foreground">状态</Label>
                  <div className="flex items-center gap-2">
                    <NativeSelect
                      aria-label="服务状态"
                      value={draft.status}
                      onChange={(event) =>
                        setDrafts((prev) => ({
                          ...prev,
                          [row.id]: { ...draft, status: event.target.value },
                        }))
                      }
                    >
                      {SERVICE_STATUS_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </NativeSelect>
                    <StatusBadge group="itemStatus" value={row.status} />
                  </div>
                </div>
                <div className="flex flex-col gap-1">
                  <Label className="text-xs text-muted-foreground">次数</Label>
                  <Input
                    aria-label="服务次数"
                    inputMode="numeric"
                    placeholder="如 2"
                    value={draft.count}
                    onChange={(event) =>
                      setDrafts((prev) => ({
                        ...prev,
                        [row.id]: { ...draft, count: event.target.value },
                      }))
                    }
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <Label className="text-xs text-muted-foreground">费用（元）</Label>
                  <Input
                    aria-label="服务费用（元）"
                    inputMode="decimal"
                    placeholder="0 元选“免费”"
                    value={draft.cost}
                    onChange={(event) =>
                      setDrafts((prev) => ({
                        ...prev,
                        [row.id]: { ...draft, cost: event.target.value },
                      }))
                    }
                  />
                </div>
              </div>
              <Input
                aria-label="服务说明"
                placeholder="原始描述（选填）"
                value={draft.description}
                onChange={(event) =>
                  setDrafts((prev) => ({
                    ...prev,
                    [row.id]: { ...draft, description: event.target.value },
                  }))
                }
              />
              <div className="flex justify-end gap-2">
                {dirty ? (
                  <Button
                    size="sm"
                    disabled={saving}
                    onClick={() =>
                      void run(() =>
                        quotesApi.updateService(quote.id, row.id, {
                          serviceType: draft.serviceType,
                          status: draft.status,
                          count: draft.count.trim() === "" ? null : Number(draft.count),
                          cost: draft.cost.trim() === "" ? null : draft.cost.trim(),
                          description: draft.description.trim() || null,
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
                  onClick={() => void run(() => quotesApi.deleteService(quote.id, row.id))}
                >
                  删除
                </Button>
              </div>
            </CardContent>
          </Card>
        );
      })}
      {quote.services.length === 0 ? (
        <p className="text-muted-foreground text-sm">还没有增值服务，如道路救援、代驾，从下方添加。</p>
      ) : null}

      <Card>
        <CardContent className="pt-4">
          <form
            className="flex flex-col gap-3"
            aria-label="新增增值服务"
            onSubmit={(event) => {
              event.preventDefault();
              void run(() =>
                quotesApi.createService(quote.id, {
                  serviceType: adding.serviceType,
                  status: adding.status,
                  count: adding.count.trim() === "" ? null : Number(adding.count),
                  cost: adding.cost.trim() === "" ? null : adding.cost.trim(),
                  description: adding.description.trim() || null,
                } as never)
              ).then((ok) => {
                if (ok) setAdding({ ...adding, count: "", cost: "", description: "" });
              });
            }}
          >
            <div className="grid grid-cols-2 gap-3">
              <NativeSelect
                aria-label="新增服务类型"
                value={adding.serviceType}
                onChange={(event) => setAdding({ ...adding, serviceType: event.target.value })}
              >
                {dict.serviceTypes.map((option) => (
                  <option key={option.code} value={option.code}>
                    {option.label}
                  </option>
                ))}
              </NativeSelect>
              <NativeSelect
                aria-label="新增服务状态"
                value={adding.status}
                onChange={(event) => setAdding({ ...adding, status: event.target.value })}
              >
                {SERVICE_STATUS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </NativeSelect>
              <Input
                aria-label="新增服务次数"
                inputMode="numeric"
                placeholder="次数，如 2"
                value={adding.count}
                onChange={(event) => setAdding({ ...adding, count: event.target.value })}
              />
              <Input
                aria-label="新增服务费用（元）"
                inputMode="decimal"
                placeholder="费用（元），0 元选“免费”"
                value={adding.cost}
                onChange={(event) => setAdding({ ...adding, cost: event.target.value })}
              />
            </div>
            <Button
              type="submit"
              variant="outline"
              size="sm"
              className="self-start"
              disabled={saving}
            >
              + 添加增值服务
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

export function AnnotationTab({
  quote,
  saving,
  run,
  dict,
}: QuoteEditorContext & { dict: Dictionaries }) {
  const [content, setContent] = React.useState("");
  const [kind, setKind] = React.useState("HANDWRITTEN");

  return (
    <div className="flex flex-col gap-4">
      {/* 隔离提示：销售标注默认不参与结构化对比与金额计算（SPEC §2.7） */}
      <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        此处的销售说明/补充标注不参与正式保障对比与金额计算，仅作参考；请以正式表格内容为准。
      </div>
      {quote.annotations.map((row) => (
        <Card key={row.id}>
          <CardContent className="flex flex-col gap-2 pt-4">
            <div className="flex items-center justify-between gap-2">
              <StatusBadge group="annotationKind" value={row.kind} />
              <Button
                size="sm"
                variant="ghost"
                className="text-muted-foreground"
                disabled={saving}
                onClick={() =>
                  void run(() => quotesApi.deleteAnnotation(quote.id, row.id))
                }
              >
                删除
              </Button>
            </div>
            <p className="text-sm leading-relaxed">{row.content}</p>
          </CardContent>
        </Card>
      ))}
      {quote.annotations.length === 0 ? (
        <p className="text-muted-foreground text-sm">还没有销售说明或补充标注。</p>
      ) : null}
      <Card>
        <CardContent className="pt-4">
          <form
            className="flex flex-col gap-3"
            aria-label="新增销售说明"
            onSubmit={(event) => {
              event.preventDefault();
              if (!content.trim()) return;
              void run(() =>
                quotesApi.createAnnotation(quote.id, {
                  content: content.trim(),
                  kind: kind as never,
                  sourceType: "USER_ANNOTATION",
                })
              ).then((ok) => {
                if (ok) setContent("");
              });
            }}
          >
            <div className="flex flex-col gap-1">
              <Label htmlFor="annotation-content" className="text-xs text-muted-foreground">
                内容
              </Label>
              <Input
                id="annotation-content"
                placeholder="如 节假日90万 100%赔付"
                value={content}
                onChange={(event) => setContent(event.target.value)}
              />
            </div>
            <NativeSelect
              aria-label="标注形式"
              value={kind}
              onChange={(event) => setKind(event.target.value)}
            >
              {dict.annotationKinds.map((option) => (
                <option key={option.code} value={option.code}>
                  {option.label}
                </option>
              ))}
            </NativeSelect>
            <Button
              type="submit"
              variant="outline"
              size="sm"
              className="self-start"
              disabled={saving || !content.trim()}
            >
              + 添加销售说明
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
