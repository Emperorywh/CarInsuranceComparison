import type { Quote, QuoteFile } from "@/lib/api";

/** 证据来源三元组（字段可缺省，与生成的 OpenAPI 类型保持一致） */
export interface EditorEvidenceSource {
  sourceFileId?: number | null;
  sourcePage?: number | null;
  sourceText?: string | null;
}

/**
 * 各 Tab 共享的编辑器上下文。
 *
 * 所有明细层写操作都由页面统一的 run() 执行：成功后以服务端返回的
 * 完整报价刷新状态（价格/净支出已重算），失败时统一展示中文错误。
 *
 * TASK-04 扩展：
 * - files：确认页的关联文件列表（QuoteRead.files），供证据定位组件
 *   把 sourceFileId 映射回文件序号；
 * - openEvidence：点击“来源”时由确认页打开全屏查看器并跳到对应
 *   文件/页码；省略时来源仅展示不可跳转（如详情页轻量场景）。
 */
export interface QuoteEditorContext {
  quote: Quote;
  saving: boolean;
  /** 执行写操作；返回是否成功（成功时页面状态已刷新）。 */
  run: (action: () => Promise<Quote>) => Promise<boolean>;
  files: QuoteFile[];
  openEvidence?: (source: EditorEvidenceSource) => void;
}
