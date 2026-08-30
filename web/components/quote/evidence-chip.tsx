"use client";

import * as React from "react";

import type { QuoteFile } from "@/lib/api";

/**
 * 字段来源定位（决策 #11：文件 + 页码 + 最短原文摘录，无 bbox）。
 *
 * - sourceFileId 能匹配到确认页文件列表时渲染为按钮：点击回调
 *   (fileIndex, page)，由确认页打开全屏查看器并定位到对应文件；
 * - 有摘录但无法定位文件（来源已被清理等）时只展示脱敏摘录文本；
 * - 什么都不存在时渲染 null（手动录入字段没有来源）。
 * 摘录在后端入库前已统一脱敏，这里只做展示截断。
 */
export interface EvidenceSource {
  sourceFileId?: number | null;
  sourcePage?: number | null;
  sourceText?: string | null;
}

export function EvidenceChip({
  files,
  source,
  onOpen,
}: {
  files: QuoteFile[];
  source: EvidenceSource;
  onOpen?: (source: EvidenceSource) => void;
}) {
  const fileIndex =
    source.sourceFileId !== null
      ? files.findIndex((file) => file.id === source.sourceFileId)
      : -1;
  const canLocate = fileIndex >= 0 && source.sourcePage !== null;

  if (!canLocate) {
    if (!source.sourceText) return null;
    return (
      <span className="text-muted-foreground inline-flex max-w-full items-center gap-1 truncate rounded-full bg-muted px-2 py-0.5 text-xs">
        <span className="truncate">摘录：{source.sourceText}</span>
      </span>
    );
  }

  return (
    <button
      type="button"
      className="text-primary inline-flex max-w-full items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs hover:bg-primary/20"
      onClick={() => onOpen?.(source)}
      aria-label={`查看来源：文件${fileIndex + 1} 第${source.sourcePage}页`}
    >
      <span className="shrink-0 font-medium">
        文件{fileIndex + 1}·第{source.sourcePage}页
      </span>
      {source.sourceText ? (
        <span className="hidden truncate sm:inline">“{source.sourceText}”</span>
      ) : null}
      <span aria-hidden>→</span>
    </button>
  );
}

/**
 * 按 field_evidence.fieldName 取标量字段证据的便捷组件。
 * 价格/车辆/公司等标量字段的来源展示统一走这里，避免各 Tab 自查。
 */
export function FieldEvidenceLine({
  evidences,
  fieldName,
  files,
  onOpen,
}: {
  evidences: Array<{
    fieldName: string;
    sourceFileId?: number | null;
    sourcePage?: number | null;
    sourceText?: string | null;
  }>;
  fieldName: string;
  files: QuoteFile[];
  onOpen?: (source: EvidenceSource) => void;
}) {
  const evidence = evidences.find((item) => item.fieldName === fieldName);
  if (!evidence) return null;
  return (
    <EvidenceChip
      files={files}
      source={{
        sourceFileId: evidence.sourceFileId,
        sourcePage: evidence.sourcePage,
        sourceText: evidence.sourceText,
      }}
      onOpen={onOpen}
    />
  );
}
