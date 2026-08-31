/**
 * TASK-06 组件测试：五问总结卡片与六区对比表。
 * 逐项断言前端正确渲染服务端结构化数据（不自行推导业务结论）。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FiveQuestions } from "@/components/compare/five-questions";
import { CompareTables } from "@/components/compare/compare-table";
import { makeCompareResult } from "./compare-fixtures";

afterEach(() => cleanup());

describe("五问总结卡片", () => {
  it("第一问 MIN：展示最低价结论与服务端文案", () => {
    render(<FiveQuestions result={makeCompareResult()} />);
    expect(screen.getByText(/「方案A」实际净支出最低/)).toBeInTheDocument();
  });

  it("第一问 TENTATIVE：含估值或校验异常时使用「暂为最低」口径", () => {
    const result = makeCompareResult({
      fiveQuestions: {
        cheapest: {
          kind: "TENTATIVE",
          quoteIds: [2],
          netPayment: 5300,
          text:
            "按当前已确认金额，「方案B」暂为最低（¥5,300.00）；最低价包含用户估值或总额校验异常，请核对后采信",
        },
        strongest: [],
        incomplete: [],
        attribution: {
          priceBaselineQuoteId: null,
          unavailableReason: null,
          pairs: [],
        },
        incomparable: {
          scopeDiffers: false,
          differences: [],
          unknownItems: [],
          unrecognizedCount: 0,
          messages: [],
        },
      },
    });
    render(<FiveQuestions result={result} />);
    expect(screen.getByText(/暂为最低/)).toBeInTheDocument();
  });

  it("第一问 价格信息不足：全部报价无总价时明确说明", () => {
    const result = makeCompareResult();
    result.fiveQuestions.cheapest = {
      kind: "INSUFFICIENT_PRICE",
      quoteIds: [],
      netPayment: null,
      text: "价格信息不足：所选报价均缺少可用总价，无法比较价格",
    };
    render(<FiveQuestions result={result} />);
    expect(screen.getByText(/价格信息不足/)).toBeInTheDocument();
  });

  it("第二问：关键保障分别比较，车损信息不足单独说明", () => {
    render(<FiveQuestions result={makeCompareResult()} />);
    // 三者：B 最高、A 缺保额提示
    expect(screen.getByText(/「方案B」.*500 万/)).toBeInTheDocument();
    expect(screen.getByText(/方案A 缺保额，无法参与比较/)).toBeInTheDocument();
    // 车损：insufficient → 信息不足
    expect(screen.getByText("各方案均无可用保额，信息不足")).toBeInTheDocument();
  });

  it("第三问：缺失清单按方案列出（交强险不计入由服务端保证）", () => {
    render(<FiveQuestions result={makeCompareResult()} />);
    expect(screen.getByText(/「方案B」缺少：/)).toBeInTheDocument();
    expect(screen.getByText("司机险、乘客险")).toBeInTheDocument();
  });

  it("第四问：Δ分项与「明细保费不完整」阻断说明都渲染", () => {
    render(<FiveQuestions result={makeCompareResult()} />);
    expect(screen.getByText(/比基准贵 ¥300/)).toBeInTheDocument();
    expect(screen.getByText("Δ商业险：+¥300")).toBeInTheDocument();
    expect(screen.getByText("Δ交强险：数据不足，无法比较")).toBeInTheDocument();
    expect(screen.getByText(/明细保费不完整，无法继续拆分/)).toBeInTheDocument();
  });

  it("第五问：同口径提示与未识别项数量", () => {
    render(<FiveQuestions result={makeCompareResult()} />);
    expect(
      screen.getByText("同口径提示：核心保障口径不同，不能仅按总价判断")
    ).toBeInTheDocument();
    expect(screen.getByText("2 项未识别保障未参与结构化对比")).toBeInTheDocument();
    expect(
      screen.getByText("三者险保额不同：「方案A」300 万、「方案B」500 万")
    ).toBeInTheDocument();
  });

  it("第四问无可用净支出时展示 unavailableReason", () => {
    const result = makeCompareResult();
    result.fiveQuestions.attribution = {
      priceBaselineQuoteId: null,
      unavailableReason: "所选报价均缺少可用净支出，无法进行价格归因",
      pairs: [],
    };
    render(<FiveQuestions result={result} />);
    expect(screen.getByText(/无法进行价格归因/)).toBeInTheDocument();
  });
});

describe("六区对比表", () => {
  it("按服务端顺序渲染六个分区并显示方案表头与异常标注", () => {
    render(<CompareTables result={makeCompareResult()} />);
    for (const title of ["价格", "核心保障", "附加险", "额外保障", "增值服务", "优惠/净支出"]) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
    // 异常标注不得隐藏（每个分区表头各出现一次）
    expect(screen.getAllByText("含用户估值").length).toBe(6);
    // 基准徽标逐分区出现
    expect(screen.getAllByText("差异基准").length).toBe(6);
    expect(screen.getAllByText("价格基准").length).toBe(6);
  });

  it("差异行默认可见且置顶；相同行默认折叠、可展开", () => {
    render(<CompareTables result={makeCompareResult()} />);
    // 差异行单元格（净支出 5300 / 三者保额 500 万）可见
    expect(screen.getAllByText("¥5,300.00").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("500 万")).toBeInTheDocument();
    // 相同行（官方总价 5,500）默认折叠
    expect(screen.queryByText("¥5,500.00")).not.toBeInTheDocument();
    // 展开价格分区的相同项后可见（两个方案列各一个单元格）
    fireEvent.click(screen.getAllByText(/展开相同项（1）$/)[0]);
    expect(screen.getAllByText("¥5,500.00").length).toBe(2);
  });

  it("差异行带 ↑ 标签，基准列无箭头", () => {
    render(<CompareTables result={makeCompareResult()} />);
    // 净支出 ↑ + 商业险 ↑ + 折现合计 ↑（每个分区表头各一组）
    expect(screen.getAllByLabelText("↑ 增加").length).toBeGreaterThanOrEqual(3);
  });

  it("空分区与全相同行折叠的分区显示无差异说明", () => {
    render(<CompareTables result={makeCompareResult()} />);
    // 附加险/额外保障无行；增值服务仅相同行且默认折叠 → 共 3 处
    expect(screen.getAllByText("此分区各方案无差异。").length).toBe(3);
  });

  it("增值服务分区相同行折叠后可通过展开按钮查看", () => {
    render(<CompareTables result={makeCompareResult()} />);
    // 道路救援行各方案相同 → 默认折叠；展开按钮顺序：价格、核心保障、增值服务
    expect(screen.queryByText("道路救援")).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByText(/展开相同项（1）$/)[2]);
    expect(screen.getByText("道路救援")).toBeInTheDocument();
    expect(screen.getAllByText("免费 · 2 次 · ¥0.00").length).toBe(2);
  });
});