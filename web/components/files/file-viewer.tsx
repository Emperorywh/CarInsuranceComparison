"use client";

/**
 * 受控文件全屏查看器（TASK-03 范围 10）。
 *
 * - 图片：加载 blob 后全屏展示；
 * - PDF：加载整个文件 blob 后交给 <embed> 的浏览器 PDF 引擎渲染，
 *   自带翻页/缩放能力，MVP 不引入前端 PDF 渲染库；
 * - 多文件横向切换（上一个/下一个），只展示文件，不伪造解析证据。
 */

import * as React from "react";
import { ChevronLeft, ChevronRight, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { fetchFileBlobUrl, type QuoteFile } from "@/lib/api";

export function FileViewer({
  files,
  index,
  initialPage,
  onClose,
  onIndexChange,
}: {
  files: QuoteFile[];
  index: number;
  /** 证据定位的目标页码（TASK-04）：图片恒为 1，PDF 交给浏览器 #page= 锚点 */
  initialPage?: number;
  onClose: () => void;
  onIndexChange: (index: number) => void;
}) {
  const file = files[index];

  // Esc 关闭（移动端无键盘时由关闭按钮承担）
  React.useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const hasPrev = index > 0;
  const hasNext = index < files.length - 1;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`文件预览 ${file.fileName}`}
      className="fixed inset-0 z-50 flex flex-col bg-black/90"
    >
      <header className="flex items-center gap-2 px-3 py-2 text-white">
        <span className="min-w-0 flex-1 truncate text-sm">
          {file.fileName}
          <span className="text-white/70">
            （{index + 1}/{files.length}）
          </span>
        </span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="关闭预览"
          className="text-white hover:bg-white/20"
          onClick={onClose}
        >
          <X aria-hidden />
        </Button>
      </header>

      <div className="relative flex min-h-0 flex-1 items-center justify-center px-2">
        {/* 以 rawUrl 为 key：切换文件时重挂载内容组件，加载状态自然重置 */}
        <FileContent key={file.rawUrl} file={file} initialPage={initialPage} />

        {hasPrev ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label="上一个文件"
            className="absolute left-1 text-white hover:bg-white/20"
            onClick={() => onIndexChange(index - 1)}
          >
            <ChevronLeft aria-hidden />
          </Button>
        ) : null}
        {hasNext ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label="下一个文件"
            className="absolute right-1 text-white hover:bg-white/20"
            onClick={() => onIndexChange(index + 1)}
          >
            <ChevronRight aria-hidden />
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function FileContent({ file, initialPage }: { file: QuoteFile; initialPage?: number }) {
  const [blobUrl, setBlobUrl] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let revoked: string | null = null;
    let cancelled = false;
    fetchFileBlobUrl(file.rawUrl)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        revoked = url;
        setBlobUrl(url);
      })
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : "原文件加载失败");
      });
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [file.rawUrl]);

  if (error) {
    return (
      <p role="alert" className="text-sm text-white">
        {error}
      </p>
    );
  }
  if (!blobUrl) {
    return <p className="text-sm text-white/80">加载中…</p>;
  }
  return file.mime === "application/pdf" ? (
    // 浏览器内置 PDF 引擎：自带翻页与缩放；#page= 锚点定位证据页码
    <embed
      src={initialPage ? `${blobUrl}#page=${initialPage}` : blobUrl}
      type="application/pdf"
      className="h-full w-full rounded-lg"
    />
  ) : (
    // eslint-disable-next-line @next/next/no-img-element -- blob 预览地址不经过 Next 图片优化
    <img
      src={blobUrl}
      alt={file.fileName}
      className="max-h-full max-w-full rounded-lg object-contain"
    />
  );
}
