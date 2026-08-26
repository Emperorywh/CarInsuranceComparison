"use client";

import * as React from "react";

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  addUnauthorizedListener,
  setAccessToken,
  clearAccessToken,
  getAccessToken,
} from "@/lib/api";

/**
 * API 全局提供者：订阅 401 事件并弹出访问令牌输入框。
 *
 * 隐私边界：令牌只写入 localStorage；保存后刷新页面以带新令牌重放当前视图；
 * 令牌绝不进入 URL 或服务端渲染流程。
 */
export function ApiProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = React.useState(false);
  const [token, setToken] = React.useState("");

  React.useEffect(() => {
    const unsubscribe = addUnauthorizedListener(() => {
      // 收到 401：清掉失效令牌并提示重新输入
      clearAccessToken();
      setToken("");
      setOpen(true);
    });
    return unsubscribe;
  }, []);

  return (
    <>
      {children}
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>需要访问令牌</AlertDialogTitle>
            <AlertDialogDescription>
              当前后端启用了访问控制。请输入 LOCAL_ACCESS_TOKEN 对应的令牌；
              令牌只保存在本机浏览器中，不会出现在链接里。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <Input
            type="password"
            autoComplete="off"
            placeholder="请输入访问令牌"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && token.trim()) {
                setAccessToken(token.trim());
                window.location.reload();
              }
            }}
          />
          <AlertDialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              稍后再说
            </Button>
            <Button
              disabled={!token.trim()}
              onClick={() => {
                setAccessToken(token.trim());
                // 刷新以带新令牌重新加载当前页面数据
                window.location.reload();
              }}
            >
              保存并重试
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

/** 供测试/调试确认当前是否已配置令牌（不含令牌值本身） */
export function hasStoredToken(): boolean {
  return getAccessToken() !== null;
}
