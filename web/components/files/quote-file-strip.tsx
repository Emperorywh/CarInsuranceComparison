"use client";

/**
 * 受控文件缩略图横滑条（TASK-03 范围 10）。
 *
 * - 图片经 fetchFileBlobUrl 加载（带访问令牌，401 复用全局令牌输入流程），
 *   绝不把原文件地址直接塞进 <img>；
 * - PDF 不做前端渲染，显示文件卡片（名称 + 页数徽标），点击进入查看器，
 *   翻页交给查看器内的浏览器 PDF 引擎；
 * - 仅展示文件本身，不伪造任何解析证据（evidence 由 TASK-04 提供）。
 */

import * as React from "react";
import { FileText } from "lucide-react";

import { fetchFileBlobUrl, type QuoteFile } from "@/lib/api";
import { cn } from "@/lib/utils";
import { FileViewer } from "@/components/files/file-viewer";

export function QuoteFileStrip({ files }: { files: QuoteFile[] }) {
  const [viewerIndex, setViewerIndex] = React.useState<number | null>(null);

  if (files.length === 0) return null;

  return (
    <section aria-label="已上传文件" className="flex flex-col gap-2">
      <h2 className="text-muted-foreground text-sm font-medium">
        报价单文件（{files.length}）
      </h2>
      {/* 横滑：移动端手势滚动查看全部文件 */}
      <div className="flex gap-3 overflow-x-auto pb-1">
        {files.map((file, index) => (
          <FileThumb key={file.id} file={file} onOpen={() => setViewerIndex(index)} />
        ))}
      </div>
      {viewerIndex !== null ? (
        <FileViewer
          files={files}
          index={viewerIndex}
          onClose={() => setViewerIndex(null)}
          onIndexChange={setViewerIndex}
        />
      ) : null}
    </section>
  );
}

function FileThumb({ file, onOpen }: { file: QuoteFile; onOpen: () => void }) {
  const isPdf = file.mime === "application/pdf";
  const [blobUrl, setBlobUrl] = React.useState<string | null>(null);
  const [loadFailed, setLoadFailed] = React.useState(false);

  React.useEffect(() => {
    if (isPdf || loadFailed) return;
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
      .catch(() => {
        // 缩略图加载失败不阻塞页面；点击查看器会再次尝试并给出错误提示
        setLoadFailed(true);
      });
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [file.rawUrl, isPdf, loadFailed]);

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`查看文件 ${file.fileName}`}
      className={cn(
        "relative h-28 w-24 shrink-0 overflow-hidden rounded-2xl border bg-muted/40",
        "flex flex-col items-center justify-center gap-1 text-xs transition-colors hover:bg-muted/70"
      )}
    >
      {isPdf ? (
        <>
          <FileText className="text-muted-foreground" aria-hidden />
          <span className="px-1 text-center leading-tight">{file.fileName}</span>
          <span className="bg-primary/10 text-primary rounded-full px-2 py-0.5 text-[10px]">
            {file.pageCount} 页
          </span>
        </>
      ) : blobUrl ? (
        // eslint-disable-next-line @next/next/no-img-element -- blob 预览地址不经过 Next 图片优化
        <img src={blobUrl} alt={file.fileName} className="h-full w-full object-cover" />
      ) : (
        <FileText className="text-muted-foreground" aria-hidden />
      )}
    </button>
  );
}
