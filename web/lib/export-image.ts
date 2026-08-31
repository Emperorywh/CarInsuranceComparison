/**
 * 长图栅格化与分发（TASK-07）：把白名单导出节点渲染为 PNG 并按环境分发。
 *
 * 实现口径：
 * - 使用 html-to-image（DOM → SVG foreignObject → canvas）的 toBlob；
 *   只栅格化专门构建的白名单节点，绝不克隆页面本身区域；
 * - 超长画布：移动浏览器（iOS Safari）对单块画布有总像素预算限制，
 *   按「宽×高×pixelRatio² ≤ 预算」自适应下调清晰度（上限 2x，下限 1x），
 *   避免导出静默得到空白/裁切图；
 * - skipFonts: 主题只使用系统字体栈（无 @font-face），跳过字体嵌入
 *   既加快导出，也避免跨域字体规则导致的栅格化失败；
 * - 分发：navigator.canShare 支持文件时优先 Web Share（移动端可直接
 *   转发到微信等），否则退回 a[download] 下载 PNG。
 */
import { toBlob } from "html-to-image";

/** 画布总像素预算：约等于 iOS Safari 可靠渲染上限（16.7M）留余量 */
const MAX_CANVAS_PIXELS = 16_000_000;

/** 栅格化结果：blob 供分享/下载，dataUrl 供测试断言尺寸与内容检查 */
export interface RasterizedExport {
  blob: Blob;
  dataUrl: string;
  width: number;
  height: number;
}

/** 读取 PNG IHDR 的宽高（rasterize 内部用，测试也复用） */
export function readPngSize(bytes: Uint8Array): { width: number; height: number } {
  // PNG 签名 8 字节 + IHDR 长度/类型 8 字节，宽高位于第 16–24 字节（大端）
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return { width: view.getUint32(16), height: view.getUint32(20) };
}

/** 把白名单导出节点栅格化为 PNG；节点必须已挂在 DOM 中。 */
export async function rasterizeExportNode(
  node: HTMLElement,
  fileName: string
): Promise<RasterizedExport> {
  const rect = node.getBoundingClientRect();
  // 自适应高清倍率：常规内容 2x 高清；内容超高时压回像素预算内
  const pixels = Math.max(rect.width * rect.height, 1);
  const pixelRatio = Math.min(2, Math.max(1, Math.sqrt(MAX_CANVAS_PIXELS / pixels)));

  const blob = await toBlob(node, {
    type: "image/png",
    backgroundColor: "#ffffff",
    pixelRatio,
    skipFonts: true,
  });
  if (!blob) throw new Error("导出失败：无法生成图片");

  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("导出失败：图片编码失败"));
    reader.readAsDataURL(blob);
  });
  const size = readPngSize(new Uint8Array(await blob.arrayBuffer()));
  if (size.width === 0 || size.height === 0) {
    throw new Error("导出失败：画布尺寸异常，请缩小对比方案数量后重试");
  }
  void fileName; // 文件名由调用方在分发时使用
  return { blob, dataUrl, width: size.width, height: size.height };
}

export type ShareOutcome = "shared" | "downloaded";

/**
 * 分发导出图：支持文件分享的环境优先 Web Share，否则触发下载。
 * 返回实际走了哪条路径（测试断言用）；用户取消分享面板视为已完成。
 */
export async function shareOrDownload(
  blob: Blob,
  fileName: string,
  title: string
): Promise<ShareOutcome> {
  const file = new File([blob], fileName, { type: "image/png" });
  const canShare =
    typeof navigator !== "undefined" &&
    typeof navigator.canShare === "function" &&
    navigator.canShare({ files: [file] });
  if (canShare) {
    try {
      await navigator.share({ files: [file], title });
      return "shared";
    } catch (cause) {
      // 用户主动关闭分享面板（AbortError）不算失败；其余错误退回下载
      if (cause instanceof DOMException && cause.name === "AbortError") {
        return "shared";
      }
    }
  }
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // 立即回收 objectURL：下载已由浏览器接管，不依赖该 URL 存活
  URL.revokeObjectURL(url);
  return "downloaded";
}

/** 导出文件名：只含日期与固定词，不携带任何项目名/公司名等用户数据 */
export function exportFileName(date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `车险报价对比-${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}.png`;
}
