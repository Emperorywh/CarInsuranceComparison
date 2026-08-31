/**
 * 长图导出专用白名单 view model（TASK-07，SPEC §8「导出长图」）。
 *
 * 隐私边界（本模块是不变量所在，导出链路的其他代码只消费这里的结果）：
 * - 长图内容限定为：五问结论、价格表、核心保障差异、免责声明；
 * - 只允许出现 方案展示名、保险公司名、价格/保额与差异标签；
 * - 保险员姓名（agentName）、车辆摘要、evidence 原文、用户备注、
 *   销售标注、访问令牌等任何字段一概不得进入产出结构；
 * - 导出组件（export-canvas）只渲染本文件的输出，绝不克隆/截图页面
 *   本身的 DOM 区域，从结构上杜绝把敏感区域带进待栅格化节点。
 *
 * 文案口径与对比页 FiveQuestions 组件一致：同样消费服务端 CompareResult
 * 的结构化字段（kind/文本/金额），前端不自行推导业务结论。
 */
import type {
  CompareResult,
  CompareRow,
  CompareSection,
  DiffTag,
} from "@/lib/api";
import { formatCoverageAmount, formatMoney } from "@/lib/format";

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

/** 价格表/差异表的单元格：服务端已格式化的文本 + 差异标签 */
export interface ExportCell {
  text: string;
  tag: DiffTag | null;
}

/** 价格表一行 */
export interface ExportPriceRow {
  label: string;
  cells: ExportCell[];
}

/** 核心差异一行（附分区名，便于在长图中独立阅读） */
export interface ExportDiffRow {
  sectionTitle: string;
  label: string;
  cells: ExportCell[];
}

/** 五问在长图中的呈现：问题 + 若干纯文本行 */
export interface ExportQuestion {
  title: string;
  lines: string[];
}

/** 长图完整内容（栅格化节点的唯一数据来源） */
export interface ExportViewModel {
  title: string;
  /** 生成日期（仅到日，避免泄露精确操作时间） */
  generatedOn: string;
  plans: ExportPlan[];
  priceRows: ExportPriceRow[];
  diffRows: ExportDiffRow[];
  questions: ExportQuestion[];
  disclaimer: string;
}

const QUESTION_TITLES = [
  "① 哪个最便宜？",
  "② 哪些关键保障额度最高？",
  "③ 哪些方案保障不完整？",
  "④ 贵的钱贵在哪里？",
  "⑤ 哪些不能直接比？",
] as const;

/** 按 quoteId 查方案展示名（与对比页 FiveQuestions 同口径） */
function nameOf(result: CompareResult, quoteId: number): string {
  return (
    result.quotes.find((quote) => quote.quoteId === quoteId)?.displayName ??
    `#${quoteId}`
  );
}

/** 单元格白名单映射：只保留展示文本与差异标签，丢弃结构化 value 等无关字段 */
function toCell(row: CompareRow, index: number): ExportCell {
  const cell = row.cells[index];
  return { text: cell?.text ?? "—", tag: cell?.tag ?? null };
}

/** 五问 → 纯文本行（文案与 FiveQuestions 组件渲染口径一致） */
function buildQuestions(result: CompareResult): ExportQuestion[] {
  const { fiveQuestions: fq } = result;
  const lines: string[][] = [];

  // ① 最便宜：服务端 text 已按 MIN/TENTATIVE/价格不足 三种口径生成
  lines.push([fq.cheapest.text]);

  // ② 关键保障额度：逐项比较，不把不同保障对象求和
  lines.push(
    fq.strongest.map((metric) => {
      if (metric.insufficient || metric.maxAmount === null) {
        return `${metric.label}：各方案均无可用保额，信息不足`;
      }
      const names = metric.maxQuoteIds.map((id) => `「${nameOf(result, id)}」`).join("、");
      const missing =
        metric.missingQuoteIds.length > 0
          ? `（${metric.missingQuoteIds.map((id) => nameOf(result, id)).join("、")} 缺保额，无法参与比较）`
          : "";
      return `${metric.label}：${names} ${formatCoverageAmount(metric.maxAmount)}${missing}`;
    })
  );

  // ③ 保障完整性：商业四大主险（交强险不计入）
  lines.push(
    fq.incomplete.every((item) => item.complete)
      ? ["所选方案商业四大主险（车损/三者/司机/乘客）均完整。"]
      : fq.incomplete
          .filter((item) => !item.complete)
          .map((item) => `「${item.displayName}」缺少：${item.missing.join("、")}`)
  );

  // ④ 价格归因：归因基准身份 + 逐方案 Δ分项（明细不完整时明确说明）
  const attributionLines: string[] = [];
  const priceBaselineName = result.priceBaselineQuoteId
    ? nameOf(result, result.priceBaselineQuoteId)
    : null;
  if (priceBaselineName) {
    attributionLines.push(`归因基准：「${priceBaselineName}」（最低净支出）`);
  }
  if (fq.attribution.unavailableReason) {
    attributionLines.push(fq.attribution.unavailableReason);
  } else {
    for (const pair of fq.attribution.pairs) {
      const deltaText =
        pair.deltaNet == null
          ? "净支出不可比"
          : pair.deltaNet > 0
            ? `比基准贵 ${formatMoney(pair.deltaNet)}`
            : pair.deltaNet < 0
              ? `比基准便宜 ${formatMoney(Math.abs(pair.deltaNet))}`
              : "与基准持平";
      attributionLines.push(`「${nameOf(result, pair.otherQuoteId)}」${deltaText}`);
      for (const part of pair.parts) {
        const delta =
          part.comparable && part.delta != null
            ? `${part.delta > 0 ? "+" : ""}¥${part.delta.toLocaleString("zh-CN")}`
            : "数据不足";
        attributionLines.push(`Δ${part.label}：${delta}`);
      }
      if (pair.topChanges.length > 0) {
        attributionLines.push(
          `险种变化：${pair.topChanges
            .map(
              (change) =>
                `${change.label} ${formatMoney(change.baselinePremium)}→${formatMoney(change.otherPremium)}`
            )
            .join("；")}`
        );
      }
      if (pair.note) attributionLines.push(pair.note);
    }
  }
  lines.push(attributionLines);

  // ⑤ 同口径/信息不足/未识别项提示
  lines.push(
    fq.incomparable.messages.length === 0
      ? ["所选方案核心保障口径一致，可以直接比较。"]
      : [...fq.incomparable.messages, ...fq.incomparable.differences.map((d) => d.detail)]
  );

  return QUESTION_TITLES.map((title, index) => ({ title, lines: lines[index] ?? [] }));
}

/**
 * 从 CompareResult 构建长图白名单 view model。
 *
 * 这是导出链路唯一的取数入口：价格表取 price + net 两个分区的全部行
 * （净支出/优惠属于价格信息），核心差异取其余分区中服务端标记 diff 的行。
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

  const sectionBykey = new Map(result.sections.map((s) => [s.key, s]));
  const toRow = (section: CompareSection, row: CompareRow) => ({
    label: row.label,
    cells: row.cells.map((_, index) => toCell(row, index)),
  });
  // 价格表：price 分区（商业险/交强/车船税/保障包/其他费用/官方与系统总价）
  // + net 分区（优惠与净支出，属于价格信息）；相同行也保留，长图无折叠交互
  const priceRows: ExportPriceRow[] = [];
  for (const key of ["price", "net"] as const) {
    const section = sectionBykey.get(key);
    if (section) {
      for (const row of section.rows) priceRows.push(toRow(section, row));
    }
  }
  // 核心差异：核心保障/附加险/额外保障/增值服务中服务端标记 diff 的行
  const diffRows: ExportDiffRow[] = [];
  for (const key of ["core", "additional", "packages", "services"] as const) {
    const section = sectionBykey.get(key);
    if (!section) continue;
    for (const row of section.rows) {
      if (row.diff) {
        diffRows.push({
          sectionTitle: section.title,
          label: row.label,
          cells: row.cells.map((_, index) => toCell(row, index)),
        });
      }
    }
  }

  return {
    title: "车险报价对比",
    generatedOn: new Date().toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "long",
      day: "numeric",
    }),
    plans,
    priceRows,
    diffRows,
    questions: buildQuestions(result),
    disclaimer: result.disclaimer,
  };
}
