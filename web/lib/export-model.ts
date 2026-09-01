/**
 * 长图导出专用白名单 view model（TASK-07，SPEC §8「导出长图」）。
 *
 * 隐私边界（本模块是不变量所在，导出链路的其他代码只消费这里的结果）：
 * - 长图内容限定为：方案表头（展示名/公司/净支出/异常标注）、单一总表
 *   全部指标行、免责声明；
 * - 只允许出现 方案展示名、保险公司名、价格/保额与差异标签；
 * - 保险员姓名（agentName）、车辆摘要、evidence 原文、用户备注、
 *   销售标注、访问令牌等任何字段一概不得进入产出结构；
 * - 导出组件（export-canvas）只渲染本文件的输出，绝不克隆/截图页面
 *   本身的 DOM 区域，从结构上杜绝把敏感区域带进待栅格化节点。
 */
import type { CompareResult, CompareRow, DiffTag } from "@/lib/api";

/** 长图中的方案列：白名单字段 + 价格排序视图里的净支出/官方总价 */
export interface ExportPlan {
  quoteId: number;
  displayName: string;
  insurerName: string;
  netPayment: number | null;
  officialTotal: number | null;
  isDiffBaseline: boolean;
  isPriceBaseline: boolean;
  /** 服务端下发的异常/口径标注（官方总价异常、含用户估值、优惠超额等），不得隐藏 */
  annotations: string[];
}

/** 总表单元格：服务端已格式化的文本 + 差异标签 + 行内高亮标记 */
export interface ExportCell {
  text: string;
  tag: DiffTag | null;
  diff: boolean;
}

/** 总表一行（行=指标，与页面表格同源同序） */
export interface ExportRow {
  key: string;
  label: string;
  note: string | null;
  diff: boolean;
  cells: ExportCell[];
}

/** 长图完整内容（栅格化节点的唯一数据来源） */
export interface ExportViewModel {
  title: string;
  /** 生成日期（仅到日，避免泄露精确操作时间） */
  generatedOn: string;
  plans: ExportPlan[];
  rows: ExportRow[];
  disclaimer: string;
}

/** 单元格白名单映射：只保留展示文本、差异标签与高亮标记，丢弃结构化 value 等无关字段 */
function toCell(row: CompareRow, index: number): ExportCell {
  const cell = row.cells[index];
  return { text: cell?.text ?? "—", tag: cell?.tag ?? null, diff: cell?.diff ?? false };
}

/**
 * 从 CompareResult 构建长图白名单 view model。
 *
 * 这是导出链路唯一的取数入口：方案表头 + 单一总表全部指标行
 * （与服务端 rows 同源同序，相同行也保留，长图无折叠交互）。
 */
export function buildExportViewModel(result: CompareResult): ExportViewModel {
  const priceOrderByQuote = new Map(
    result.priceOrder.map((entry) => [entry.quoteId, entry])
  );

  // 方案列白名单：displayName/公司/两种基准身份/异常标注/价格，刻意不含 agentName
  const plans: ExportPlan[] = result.quotes.map((quote) => {
    const price = priceOrderByQuote.get(quote.quoteId);
    return {
      quoteId: quote.quoteId,
      displayName: quote.displayName,
      insurerName: quote.insurerName,
      netPayment: price?.netPayment ?? null,
      officialTotal: price?.officialTotal ?? null,
      isDiffBaseline: quote.isDiffBaseline,
      isPriceBaseline: quote.isPriceBaseline,
      annotations: [...quote.annotations],
    };
  });

  const rows: ExportRow[] = result.rows.map((row) => ({
    key: row.key,
    label: row.label,
    note: row.note ?? null,
    diff: row.diff,
    cells: row.cells.map((_, index) => toCell(row, index)),
  }));

  return {
    title: "车险报价对比",
    generatedOn: new Date().toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "long",
      day: "numeric",
    }),
    plans,
    rows,
    disclaimer: result.disclaimer,
  };
}
