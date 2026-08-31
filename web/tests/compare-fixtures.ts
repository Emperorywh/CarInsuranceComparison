/**
 * TASK-06 对比测试公共 fixture：构造最小但结构完整的 CompareResult。
 * 字段命名与 openapi-typescript 生成类型（camelCase 契约）一致。
 */
import type { CompareResult, CompareSection } from "@/lib/api";

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

function priceSection(): CompareSection {
  return {
    key: "price",
    title: "价格",
    rows: [
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
    ],
  };
}

function coreSection(): CompareSection {
  return {
    key: "core",
    title: "核心保障",
    rows: [
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
    ],
  };
}

function simpleSection(
  key: CompareSection["key"],
  title: string,
  rows: CompareSection["rows"]
): CompareSection {
  return { key, title, rows };
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
    fiveQuestions: {
      cheapest: {
        kind: "MIN",
        quoteIds: [1],
        netPayment: 5000,
        text: "「方案A」实际净支出最低：¥5,000.00",
      },
      strongest: [
        {
          key: "third_party",
          label: "三者险保额",
          maxAmount: 5000000,
          maxQuoteIds: [2],
          missingQuoteIds: [1],
          insufficient: false,
        },
        {
          key: "vehicle_loss",
          label: "车损保额",
          maxAmount: null,
          maxQuoteIds: [],
          missingQuoteIds: [],
          insufficient: true,
        },
      ],
      incomplete: [
        { quoteId: 1, displayName: "方案A", missing: [], complete: true },
        {
          quoteId: 2,
          displayName: "方案B",
          missing: ["司机险", "乘客险"],
          complete: false,
        },
      ],
      attribution: {
        priceBaselineQuoteId: 1,
        unavailableReason: null,
        pairs: [
          {
            otherQuoteId: 2,
            deltaNet: 300,
            parts: [
              {
                key: "commercial",
                label: "商业险",
                baselineValue: 3000,
                otherValue: 3300,
                delta: 300,
                comparable: true,
              },
              {
                key: "compulsory",
                label: "交强险",
                baselineValue: 950,
                otherValue: null,
                delta: null,
                comparable: false,
              },
            ],
            detailComplete: false,
            topChanges: [],
            note: "明细保费不完整，无法继续拆分",
          },
        ],
      },
      incomparable: {
        scopeDiffers: true,
        differences: [
          {
            code: "THIRD_PARTY_LIABILITY",
            label: "三者险",
            dimension: "保额",
            detail: "三者险保额不同：「方案A」300 万、「方案B」500 万",
          },
        ],
        unknownItems: [],
        unrecognizedCount: 2,
        messages: [
          "同口径提示：核心保障口径不同，不能仅按总价判断",
          "2 项未识别保障未参与结构化对比",
        ],
      },
    },
    sections: [
      priceSection(),
      coreSection(),
      simpleSection("additional", "附加险", []),
      simpleSection("packages", "额外保障", []),
      simpleSection("services", "增值服务", [
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
      ]),
      simpleSection("net", "优惠/净支出", [
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
      ]),
    ],
    disclaimer:
      "本工具用于整理报价差异，不替代正式保险条款与投保决定，请以保险公司最终保单为准。",
    ...overrides,
  };
}
