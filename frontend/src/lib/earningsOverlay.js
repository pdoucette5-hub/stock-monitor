// Earnings and P/E lines for a stock's price chart.
//
// history: [{ filed, ttm_eps }] from /api/fundamentals/{ticker}/eps-history.
// Each day uses the trailing EPS last reported on or before it (the market
// learns a quarter's earnings on its filing date). The earnings line is that
// EPS times the P/E on the chart's first day, so it starts at the price and
// any gap later is the multiple expanding (price above) or contracting
// (price below). Loss-making stretches have no P/E and no earnings line.
export function withEarnings(points, history) {
  const reports = (history || [])
    .filter((item) => item?.filed && Number.isFinite(Number(item.ttm_eps)))
    .map((item) => ({ filed: String(item.filed).slice(0, 10), eps: Number(item.ttm_eps) }))
    .sort((a, b) => (a.filed < b.filed ? -1 : a.filed > b.filed ? 1 : 0))
  if (!reports.length || !points?.length) return { points: points || [], anchorPe: null }
  let r = -1
  const withEps = points.map((point) => {
    const day = String(point?.date || '').slice(0, 10)
    while (r + 1 < reports.length && reports[r + 1].filed <= day) r += 1
    const eps = r >= 0 ? reports[r].eps : null
    const close = Number(point?.close)
    const pe = eps > 0 && close > 0 ? close / eps : null
    return { point, eps, pe }
  })
  const anchorPe = withEps.find((item) => item.pe != null)?.pe ?? null
  return {
    anchorPe,
    points: withEps.map(({ point, eps, pe }) => ({
      ...point,
      pe,
      earnings: anchorPe != null && eps > 0 ? eps * anchorPe : null,
      eps,
    })),
  }
}
