// Earnings and P/E lines for a stock's price chart.
//
// history: [{ filed, period_end, quarter_eps, ttm_eps }] from
// /api/fundamentals/{ticker}/eps-history. Each day uses the figures last
// reported on or before it (the market learns a quarter's earnings on its
// filing date).
//
// - Earnings line: the latest quarter's own EPS, scaled so it starts at the
//   price on the chart's first day (EPS times that day's price / EPS). If the
//   price later sits above it, the stock has risen faster than quarterly
//   earnings; below, slower. Loss-making quarters leave a gap.
// - P/E line: price / trailing four-quarter EPS (the usual P/E).
export function withEarnings(points, history) {
  const reports = (history || [])
    .filter((item) => item?.filed && (Number.isFinite(Number(item.quarter_eps ?? NaN)) || Number.isFinite(Number(item.ttm_eps ?? NaN))))
    .map((item) => ({
      filed: String(item.filed).slice(0, 10),
      periodEnd: item.period_end ? String(item.period_end).slice(0, 10) : null,
      quarter: item.quarter_eps == null ? null : Number(item.quarter_eps),
      ttm: item.ttm_eps == null ? null : Number(item.ttm_eps),
    }))
    .sort((a, b) => (a.filed < b.filed ? -1 : a.filed > b.filed ? 1 : 0))
  if (!reports.length || !points?.length) return { points: points || [], anchorPe: null, anchor: null }
  let r = -1
  let ttm = null
  const withEps = points.map((point) => {
    const day = String(point?.date || '').slice(0, 10)
    while (r + 1 < reports.length && reports[r + 1].filed <= day) {
      r += 1
      if (reports[r].ttm != null) ttm = reports[r].ttm
    }
    const report = r >= 0 ? reports[r] : null
    // Older responses only have trailing EPS: use it for the line too.
    const quarterEps = report ? (report.quarter ?? report.ttm) : null
    const close = Number(point?.close)
    const pe = ttm > 0 && close > 0 ? close / ttm : null
    return { point, quarterEps, quarterEnd: report?.periodEnd ?? null, ttm, pe, close }
  })
  const first = withEps.find((item) => item.quarterEps > 0 && item.close > 0)
  const anchor = first ? first.close / first.quarterEps : null
  const anchorPe = withEps.find((item) => item.pe != null)?.pe ?? null
  return {
    anchor,
    anchorPe,
    points: withEps.map(({ point, quarterEps, quarterEnd, ttm: ttmEps, pe }) => ({
      ...point,
      pe,
      earnings: anchor != null && quarterEps > 0 ? quarterEps * anchor : null,
      quarter_eps: quarterEps,
      quarter_end: quarterEnd,
      eps: ttmEps,
    })),
  }
}
