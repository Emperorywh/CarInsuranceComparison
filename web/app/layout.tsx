import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

import { ApiProvider } from "@/components/providers/api-provider";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

/**
 * 全局元数据：中文产品名与描述（移动优先，视口适配手机）。
 */
export const metadata: Metadata = {
  title: {
    default: "车险报价对比助手",
    template: "%s · 车险报价对比助手",
  },
  description:
    "把不同保险公司的车险报价单转成统一结构，按价格、保障、附加险、增值服务与额外保障包横向对比。",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#eef0ff",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="zh-CN"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="bg-background text-foreground min-h-full flex flex-col">
        {/* API 客户端全局封装：401 时弹出访问令牌输入（仅存 localStorage） */}
        <ApiProvider>{children}</ApiProvider>
      </body>
    </html>
  );
}
