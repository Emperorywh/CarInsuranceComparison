"use client";

/**
 * 「导出长图」按钮（TASK-07，SPEC §8 对比页右上角）。
 *
 * 流程：构建白名单 view model → 点击时挂载屏幕外画布 → effect 中栅格化
 * 为 PNG → 支持文件分享的环境走 Web Share，否则下载。
 *
 * 隐私边界：view model（export-model.ts）是唯一取数入口；画布只渲染该
 * 白名单结构，栅格化只针对画布节点，绝不克隆页面区域。生成期间按钮
 * 禁用防重复点击；失败显示可操作的中文错误，不影响页面其余功能。
 */
import * as React from "react";
import { ImageDown, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ExportCanvas } from "@/components/compare/export-canvas";
import type { CompareResult } from "@/lib/api";
import {
  exportFileName,
  rasterizeExportNode,
  shareOrDownload,
} from "@/lib/export-image";
import { buildExportViewModel } from "@/lib/export-model";

export function ExportCompareButton({ result }: { result: CompareResult }) {
  // view model 随对比结果重建：白名单过滤只发生在这里，画布与栅格化
  // 都不再接触 CompareResult 原始对象
  const data = React.useMemo(() => buildExportViewModel(result), [result]);
  const canvasRef = React.useRef<HTMLDivElement>(null);
  const [working, setWorking] = React.useState(false);
  // 画布按需挂载：避免每次进入对比页都在 DOM 里保留一份隐藏长表
  const [canvasMounted, setCanvasMounted] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const handleExport = () => {
    if (working) return;
    setError(null);
    setWorking(true);
    setCanvasMounted(true); // 挂载后由下方 effect 执行栅格化
  };

  React.useEffect(() => {
    if (!canvasMounted) return;
    const node = canvasRef.current;
    if (!node) return;
    let cancelled = false;
    (async () => {
      try {
        const fileName = exportFileName();
        const rasterized = await rasterizeExportNode(node, fileName);
        if (rasterized.dataUrl.length < 1000) {
          // 防御：过小的 dataUrl 基本是空白画布，按失败处理而不是产出废图
          throw new Error("导出失败：生成内容为空，请稍后重试");
        }
        await shareOrDownload(rasterized.blob, fileName, data.title);
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "导出失败，请稍后重试");
        }
      } finally {
        if (!cancelled) {
          setWorking(false);
          setCanvasMounted(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [canvasMounted, data.title]);

  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={handleExport}
        disabled={working}
        data-testid="export-image-button"
      >
        {working ? (
          <Loader2 className="size-4 animate-spin" aria-hidden />
        ) : (
          <ImageDown className="size-4" aria-hidden />
        )}
        {working ? "生成中…" : "导出长图"}
      </Button>
      {error ? (
        <p className="text-xs text-rose-600" role="alert">
          {error}
        </p>
      ) : null}
      {/* 点击后临时挂载的屏幕外白名单画布；栅格化节点与页面区域完全隔离 */}
      {canvasMounted ? <ExportCanvas data={data} containerRef={canvasRef} /> : null}
    </div>
  );
}
