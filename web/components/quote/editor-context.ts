import type { Quote } from "@/lib/api";

/**
 * 各 Tab 共享的编辑器上下文。
 *
 * 所有明细层写操作都由页面统一的 run() 执行：成功后以服务端返回的
 * 完整报价刷新状态（价格/净支出已重算），失败时统一展示中文错误。
 */
export interface QuoteEditorContext {
  quote: Quote;
  saving: boolean;
  /** 执行写操作；返回是否成功（成功时页面状态已刷新）。 */
  run: (action: () => Promise<Quote>) => Promise<boolean>;
}
