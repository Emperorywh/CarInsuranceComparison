/**
 * 五问总结卡片（对比页第一屏，SPEC §7.2 / PRD 65 节）。
 *
 * 渲染完全由服务端结构化数据驱动：文案、基准身份、异常口径
 * （暂为最低/价格不足/明细保费不完整）都来自 CompareResult，
 * 前端不自行推导任何业务结论。
 */
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { CompareResult } from "@/lib/api";
import { formatCoverageAmount, formatMoney } from "@/lib/format";

/** 按 quoteId 查方案展示名（五问各卡引用方案身份时使用） */
function nameOf(result: CompareResult, quoteId: number): string {
  return (
    result.quotes.find((quote) => quote.quoteId === quoteId)?.displayName ??
    `#${quoteId}`
  );
}

function QuestionCard({
  index,
  title,
  children,
}: {
  index: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center gap-2 pb-2">
        <span className="bg-primary/10 text-primary flex size-6 shrink-0 items-center justify-center rounded-full text-sm font-bold">
          {index}
        </span>
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-1.5 text-sm leading-relaxed">
        {children}
      </CardContent>
    </Card>
  );
}

export function FiveQuestions({ result }: { result: CompareResult }) {
  const { fiveQuestions: fq } = result;
  const priceBaselineName = result.priceBaselineQuoteId
    ? nameOf(result, result.priceBaselineQuoteId)
    : null;

  return (
    <section aria-label="五问总结" className="flex flex-col gap-3">
      <h2 className="text-base font-semibold">五问总结</h2>

      {/* ① 哪个最便宜（kind 决定口径：MIN/TENTATIVE/价格不足） */}
      <QuestionCard index={1} title="哪个最便宜？">
        <p>{fq.cheapest.text}</p>
        {fq.cheapest.kind !== "INSUFFICIENT_PRICE" ? (
          <p className="text-muted-foreground text-xs">
            金额口径：实际净支出 =（官方总价 ?? 系统总价）− 计入折现的优惠
          </p>
        ) : null}
      </QuestionCard>

      {/* ② 哪些关键保障额度最高（分别比较，绝不求和） */}
      <QuestionCard index={2} title="哪些关键保障额度最高？">
        {fq.strongest.map((metric) => (
          <p key={metric.key}>
            {metric.label}：
            {metric.insufficient || metric.maxAmount === null ? (
              <span className="text-muted-foreground">各方案均无可用保额，信息不足</span>
            ) : (
              <>
                {metric.maxQuoteIds.map((id) => nameOf(result, id)).map((name, i) => (
                  <span key={`${metric.key}-${name}`} className="font-medium">
                    {i > 0 ? "、" : ""}
                    「{name}」
                  </span>
                ))}
                {` ${formatCoverageAmount(metric.maxAmount)}`}
                {metric.missingQuoteIds.length > 0 ? (
                  <span className="text-muted-foreground">
                    {" "}
                    （{metric.missingQuoteIds.map((id) => nameOf(result, id)).join("、")} 缺保额，无法参与比较）
                  </span>
                ) : null}
              </>
            )}
          </p>
        ))}
      </QuestionCard>

      {/* ③ 哪些方案保障不完整（商业四大主险；交强险不计入） */}
      <QuestionCard index={3} title="哪些方案保障不完整？">
        {fq.incomplete.every((item) => item.complete) ? (
          <p>所选方案商业四大主险（车损/三者/司机/乘客）均完整。</p>
        ) : (
          fq.incomplete
            .filter((item) => !item.complete)
            .map((item) => (
              <p key={item.quoteId}>
                「{item.displayName}」缺少：
                <span className="text-orange-600">{item.missing.join("、")}</span>
              </p>
            ))
        )}
      </QuestionCard>

      {/* ④ 贵的钱贵在哪里（归因基准 = 最低净支出，与差异基准分开标注） */}
      <QuestionCard index={4} title="贵的钱贵在哪里？">
        {priceBaselineName ? (
          <p className="text-muted-foreground text-xs">
            归因基准：「{priceBaselineName}」（最低净支出，与左侧对比基准可能不同）
          </p>
        ) : null}
        {fq.attribution.unavailableReason ? (
          <p className="text-muted-foreground">{fq.attribution.unavailableReason}</p>
        ) : (
          fq.attribution.pairs.map((pair) => (
            <div
              key={pair.otherQuoteId}
              className="rounded-xl border px-3 py-2"
            >
              <p>
                「{nameOf(result, pair.otherQuoteId)}」
                {pair.deltaNet == null ? (
                  <span className="text-muted-foreground">净支出不可比</span>
                ) : pair.deltaNet > 0 ? (
                  <span className="text-orange-600">
                    比基准贵 ¥{pair.deltaNet.toLocaleString("zh-CN")}
                  </span>
                ) : pair.deltaNet < 0 ? (
                  <span className="text-emerald-700">
                    比基准便宜 ¥{Math.abs(pair.deltaNet).toLocaleString("zh-CN")}
                  </span>
                ) : (
                  "与基准持平"
                )}
              </p>
              <ul className="text-muted-foreground mt-1 space-y-0.5 text-xs">
                {pair.parts.map((part) => (
                  <li key={part.key}>
                    Δ{part.label}：
                    {part.comparable && part.delta != null
                      ? `${part.delta > 0 ? "+" : ""}¥${part.delta.toLocaleString("zh-CN")}`
                      : "数据不足，无法比较"}
                  </li>
                ))}
                {pair.topChanges.length > 0 ? (
                  <li>
                    险种变化：
                    {pair.topChanges
                      .map(
                        (change) =>
                          `${change.label} ${formatMoney(change.baselinePremium)}→${formatMoney(change.otherPremium)}`
                      )
                      .join("；")}
                  </li>
                ) : null}
                {pair.note ? <li className="text-amber-600">{pair.note}</li> : null}
              </ul>
            </div>
          ))
        )}
      </QuestionCard>

      {/* ⑤ 哪些不能直接比（同口径提示 / 信息不足 / 未识别项） */}
      <QuestionCard index={5} title="哪些不能直接比？">
        {fq.incomparable.messages.length === 0 ? (
          <p>所选方案核心保障口径一致，可以直接比较。</p>
        ) : (
          <>
            <ul className="space-y-1">
              {fq.incomparable.messages.map((message) => (
                <li key={message} className="text-amber-600">
                  {message}
                </li>
              ))}
            </ul>
            {fq.incomparable.differences.length > 0 ? (
              <ul className="text-muted-foreground space-y-0.5 text-xs">
                {fq.incomparable.differences.map((difference) => (
                  <li key={`${difference.code}-${difference.dimension}`}>
                    {difference.detail}
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        )}
      </QuestionCard>
    </section>
  );
}
