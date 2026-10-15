// Price change for holdings over the selected period. Used by each row on the
// Holdings tab and by the summary at the top, so both show the same numbers.

// Change from the first close in `points` to the current price (or the last
// close when there is no current price). `amount` is per share.
export function gain(points, currentPrice) {
  const valid = (points || []).filter((point) => Number.isFinite(Number(point?.close)))
  if (valid.length < 2) return null
  const start = Number(valid[0].close)
  const end = Number.isFinite(Number(currentPrice)) ? Number(currentPrice) : Number(valid.at(-1).close)
  return start ? { amount: end - start, percent: ((end - start) / start) * 100, start, end } : null
}

// Like gain(), but for the 1Y range falls back to the one-year return the
// server calculated when there is not enough price history.
export function gainForRow(row, points, range) {
  const fromHistory = gain(points, row?.price)
  if (fromHistory || range !== '1y' || row?.price_return_1y == null) return fromHistory
  const returnDecimal = Number(row.price_return_1y)
  const currentPrice = Number(row.price)
  if (!Number.isFinite(returnDecimal) || !Number.isFinite(currentPrice) || returnDecimal <= -1) return null
  const priorPrice = currentPrice / (1 + returnDecimal)
  return { amount: currentPrice - priorPrice, percent: returnDecimal * 100, start: priorPrice, end: currentPrice }
}

// Totals for the rows currently listed: total market value, and the dollar
// and percent change in value of the shares held now over the period.
// Positions without enough price history for the period are counted in the
// total value but left out of the change, and reported in `missing`.
export function summarizeHoldings(rows, history, range) {
  let totalValue = 0
  let startValue = 0
  let change = 0
  let priced = 0
  const missing = []
  for (const row of rows || []) {
    const shares = Number(row?.shares)
    if (!Number.isFinite(shares) || shares <= 0) continue
    const value = Number(row.market_value)
    if (Number.isFinite(value)) totalValue += value
    const rowGain = gainForRow(row, history?.[row.ticker] || [], range)
    if (!rowGain) {
      missing.push(row.ticker)
      continue
    }
    priced += 1
    startValue += shares * rowGain.start
    change += shares * rowGain.amount
  }
  return {
    totalValue,
    change: priced ? change : null,
    percent: priced && startValue ? (change / startValue) * 100 : null,
    positions: priced + missing.length,
    priced,
    missing,
  }
}

// Holdings filter -> performance modes whose results are added together.
export const PERFORMANCE_MODES_FOR_FILTER = {
  active: ['managed', 'track'],
  managed: ['managed'],
  track: ['track'],
  excluded: ['excluded'],
  all: ['all'],
}

// Gain or loss over a period from one or more /api/performance/portfolio
// responses (one per mode), based on the transaction ledger: the change in
// capital gain (market value minus money put in) between the first and last
// day of the period. Money added or withdrawn during the period is therefore
// not counted as gain, and shares bought part-way through only count from
// when they were bought. Cash from sales and dividends counts as part of the
// portfolio until it is reinvested, so selling one stock to buy another is
// neutral and dividends count as gain. The percentage is relative to the value at the start
// plus any net money added during the period.
export function periodGainFromPerformance(payloads) {
  let change = 0
  let startValue = 0
  let netAdded = 0
  let used = 0
  let startDate = null
  let endDate = null
  for (const payload of payloads || []) {
    const series = [...(payload?.series || [])]
      .filter((point) => point?.date)
      .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))
    if (series.length < 2) continue
    const first = series[0]
    const last = series.at(-1)
    const firstGain = Number(first.capital_gain_loss ?? Number(first.market_value) - Number(first.capital_basis))
    const lastGain = Number(last.capital_gain_loss ?? Number(last.market_value) - Number(last.capital_basis))
    if (!Number.isFinite(firstGain) || !Number.isFinite(lastGain)) continue
    used += 1
    change += lastGain - firstGain
    startValue += (Number(first.market_value) || 0) + (Number(first.cash) || 0)
    netAdded += (Number(last.capital_basis) || 0) - (Number(first.capital_basis) || 0)
    startDate = !startDate || first.date < startDate ? first.date : startDate
    endDate = !endDate || last.date > endDate ? last.date : endDate
  }
  if (!used) return { change: null, percent: null, startDate: null, endDate: null }
  const base = startValue + Math.max(netAdded, 0)
  return { change, percent: base > 0 ? (change / base) * 100 : null, startDate, endDate }
}
