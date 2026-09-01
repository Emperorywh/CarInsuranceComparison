/**
 * TASK-06 组件测试：单一对比总表。
 * 断言前端把服务端各分区的行合并进一张表（不自行推导业务结论），
 * 差异行高亮置顶、相同行折叠可展开、异常标注与基准徽标不丢失。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CompareTables } from "@/components/compare/compare-table";
import { makeCompareResult } from "./compare-fixtures";

afterEach(() => cleanup());

describe("单一对比总表", () => {
  it("合并所有分区行进一张表：表头与基准徽标只出现一次", () => {
    render(<CompareTables result={makeCompareResult()} />);
    // 单表头：指标列 + 方案列（异常标注不再逐分区重复）
    expect(screen.getByText("指标")).toBeInTheDocument();
    expect(screen.getAllByText("含用户估值")).toHaveLength(1);
    expect(screen.getAllByText("差异基准")).toHaveLength(1);
    expect(screen.getAllByText("价格基准")).toHaveLength(1);
    // 各分区标题不再拆卡展示
    expect(screen.queryByText("核心保障")).not.toBeInTheDocument();
  });

  it("差异行默认可见且置顶；相同行默认折叠", () => {
    render(<CompareTables result={makeCompareResult()} />);
    // 差异行单元格（净支出 5300 / 三者保额 500 万）可见
    // （5300 另出现在方案 B 表头净支出，故 ≥1）
    expect(screen.getAllByText("¥5,300.00").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("500 万")).toBeInTheDocument();
    // 相同行（官方总价 5,500、车损保额、道路救援）默认折叠
    expect(screen.queryByText("¥5,500.00")).not.toBeInTheDocument();
    expect(screen.queryByText("道路救援")).not.toBeInTheDocument();
  });

  it("一键展开后显示全部分区的相同行", () => {
    render(<CompareTables result={makeCompareResult()} />);
    // 相同行共 3 行：官方总价、车损保额、道路救援
    fireEvent.click(screen.getByText(/展开相同项（3）$/));
    expect(screen.getAllByText("¥5,500.00")).toHaveLength(2);
    expect(screen.getAllByText("14.77 万")).toHaveLength(2);
    expect(screen.getAllByText("免费 · 2 次 · ¥0.00")).toHaveLength(2);
  });

  it("差异行带 ↑ 标签，基准列无箭头", () => {
    render(<CompareTables result={makeCompareResult()} />);
    // 4 个差异行（净支出/商业险/三者保额/计入折现合计）各一个 ↑，基准列无箭头
    expect(screen.getAllByLabelText("↑ 增加")).toHaveLength(4);
  });

  it("全部行无差异时显示说明且仍可展开全部指标", () => {
    const result = makeCompareResult();
    result.rows = result.rows.map((row) => ({ ...row, diff: false }));
    render(<CompareTables result={result} />);
    expect(screen.getByText(/各方案无差异行/)).toBeInTheDocument();
    // 全部 7 行都折为相同行，一键展开后可见
    fireEvent.click(screen.getByText(/展开相同项（7）$/));
    expect(screen.getByText("道路救援")).toBeInTheDocument();
  });
});
