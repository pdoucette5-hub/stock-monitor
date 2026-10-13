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
