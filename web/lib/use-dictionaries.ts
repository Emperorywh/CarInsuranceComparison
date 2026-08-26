"use client";

import * as React from "react";

import { loadDictionaries, setDictionariesSnapshot, type Dictionaries } from "@/lib/api";

/**
 * 字典加载 Hook：页面级使用，加载成功后同步一份快照到 api 模块，
 * 供 StatusBadge 等纯展示组件同步查中文标签（字典会话内不变）。
 */
export function useDictionaries() {
  const [dict, setDict] = React.useState<Dictionaries | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  // 重试令牌：失败后允许用户手动重试
  const [reloadToken, setReloadToken] = React.useState(0);

  React.useEffect(() => {
    let cancelled = false;
    loadDictionaries()
      .then((data) => {
        if (cancelled) return;
        setDict(data);
        setDictionariesSnapshot(data);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : "字典加载失败");
      });
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  return { dict, error, retry: () => setReloadToken((token) => token + 1) };
}
