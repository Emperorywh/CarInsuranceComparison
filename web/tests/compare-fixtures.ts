/**
 * TASK-06 对比测试公共 fixture：构造最小但结构完整的 CompareResult。
 * 字段命名与 openapi-typescript 生成类型（camelCase 契约）一致。
 */
import type { CompareResult } from "@/lib/api";

export function makeQuoteMeta(overrides: Partial<CompareResult["quotes"][number]> = {}) {
  return {
    quoteId: 1,
    displayName: "方案A",
    insurerCode: "PICC",
    insurerName: "人保",
    agentName: null,
    planLabel: "方案A",
    statusLabel: "已确认",
    isDiffBaseline: true,
    isPriceBaseline: true,
    priceRank: 0,
    annotations: [] as string[],
    ...overrides,
  };
}

/** 单一总表行：价格 → 核心保障 → 增值服务 → 优惠（各组内差异行置顶） */
function makeRows(): CompareResult["rows"] {
  return [
    {
      key: "net",
      label: "实际净支出",
      kind: "money",
      diff: true,
      note: "净支出 = (官方总价 ?? 系统总价) − 计入折现的优惠",
      cells: [
        { text: "¥5,000.00", value: 5000, tag: null, diff: false },
        { text: "¥5,300.00", value: 5300, tag: "UP", diff: true },
      ],
    },
    {
      key: "official_total",
      label: "官方总价",
      kind: "money",
      diff: false,
      note: null,
      cells: [
        { text: "¥5,500.00", value: 5500, tag: null, diff: false },
        { text: "¥5,500.00", value: 5500, tag: "SAME", diff: false },
      ],
    },
    {
      key: "commercial",
      label: "商业险",
      kind: "money",
      diff: true,
      note: null,
      cells: [
        { text: "¥3,000.00", value: 3000, tag: null, diff: false },
        { text: "¥3,300.00", value: 3300, tag: "UP", diff: true },
      ],
    },
    {
      key: "THIRD_PARTY_LIABILITY:amount",
      label: "三者险·保额",
      kind: "amount",
      diff: true,
      note: null,
      cells: [
        { text: "300 万", value: 3000000, tag: null, diff: false },
        { text: "500 万", value: 5000000, tag: "UP", diff: true },
      ],
    },
    {
      key: "VEHICLE_LOSS:amount",
      label: "车损险·保额",
      kind: "amount",
      diff: false,
      note: null,
      cells: [
        { text: "14.77 万", value: 147719.12, tag: null, diff: false },
        { text: "14.77 万", value: 147719.12, tag: "SAME", diff: false },
      ],
    },
    {
      key: "svc:ROAD_RESCUE",
      label: "道路救援",
      kind: "text",
      diff: false,
      note: null,
      cells: [
        { text: "免费 · 2 次 · ¥0.00", value: null, tag: null, diff: false },
        { text: "免费 · 2 次 · ¥0.00", value: null, tag: "SAME", diff: false },
      ],
    },
    {
      key: "deduction_total",
      label: "计入折现合计",
      kind: "money",
      diff: true,
      note: null,
      cells: [
        { text: "¥0.00", value: 0, tag: null, diff: false },
        { text: "¥200.00", value: 200, tag: "UP", diff: true },
      ],
    },
  ];
}

/** 2 方案（A 基准且最低价 / B 贵 300 且含用户估值）的最小对比结果 */
export function makeCompareResult(overrides: Partial<CompareResult> = {}): CompareResult {
  return {
    projectId: 10,
    quotes: [
      makeQuoteMeta(),
      makeQuoteMeta({
        quoteId: 2,
        displayName: "方案B",
        insurerCode: "PINGAN",
        insurerName: "平安",
        agentName: "小王",
        planLabel: "方案B",
        isDiffBaseline: false,
        isPriceBaseline: false,
        priceRank: 1,
        annotations: ["含用户估值"],
      }),
    ],
    priceOrder: [
      {
        quoteId: 1,
        netPayment: 5000,
        netPaymentStatus: "OK",
        officialTotal: 5500,
        totalCheckStatus: "PASSED",
        hasUserValuation: false,
        rank: 0,
      },
      {
        quoteId: 2,
        netPayment: 5300,
        netPaymentStatus: "OK",
        officialTotal: 5500,
        totalCheckStatus: "NOT_CHECKABLE",
        hasUserValuation: true,
        rank: 1,
      },
    ],
    diffBaselineQuoteId: 1,
    priceBaselineQuoteId: 1,
    rows: makeRows(),
    disclaimer:
      "本工具用于整理报价差异，不替代正式保险条款与投保决定，请以保险公司最终保单为准。",
    ...overrides,
  };
}
