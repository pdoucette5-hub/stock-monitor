// Price change for holdings over the selected period. Used by each row on the
// Holdings tab and by the summary at the top, so both show the same numbers.

// Change from the first close in `points` to the current price (or the last
// close when there is no current price). `amount` is per share.
export function gain(points, currentPrice) {
  const valid = (points || []).filter((point) => Number.isFinite(Number(point?.close)))
  const hasPrice = currentPrice != null && currentPrice !== '' && Number.isFinite(Number(currentPrice))
  // Need a starting close plus either a later close or the current price.
  if (valid.length < 2 && !(valid.length === 1 && hasPrice)) return null
  const start = Number(valid[0].close)
  const end = hasPrice ? Number(currentPrice) : Number(valid.at(-1).close)
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

// ---------------------------------------------------------------------------
// Gain or loss of the holdings over a period, day by day.
//
// For every stock and every step in its price history (each day, or each
// sample for Today) the gain is the shares held at that point times the price
// move to the next point. The shares held at each point are worked out from
// today's share count by undoing the trades made after that point, so a stock
// sold mid-period only counts while it was held, and a stock bought
// mid-period only counts from the price paid. Trades themselves (and so money
// added or withdrawn) are never counted as gain. Dividends are not included:
// this is the change in the value of the shares.
//
// The dollar figure is the sum of those gains. The percentage is that gain
// divided by the money at work: the value at the start plus the most extra
// money put in during the period. Positions sold
// during the period are included (pass them with shares 0) for the time they
// were held.
// ---------------------------------------------------------------------------

function pointKey(point) {
  const date = String(point?.date || '').slice(0, 10)
  const ts = Number(point?.timestamp)
  return Number.isFinite(ts) && ts > 0 ? `${date}T${String(Math.trunc(ts)).padStart(12, '0')}` : date
}

const END_KEY = '~'

// Series used for Today's gain: the previous trading day's close (from a
// daily series such as the 7D range), followed by any intraday samples taken
// today. Samples from an earlier day are ignored, so stale intraday data can
// never become the starting point. `today` is the local date (YYYY-MM-DD).
export function withPreviousClose(history, dailyHistory, today) {
  const result = {}
  const tickers = new Set([...Object.keys(history || {}), ...Object.keys(dailyHistory || {})])
  for (const ticker of tickers) {
    const prior = (dailyHistory?.[ticker] || [])
      .filter((point) => Number.isFinite(Number(point?.close)) && String(point?.date || '').slice(0, 10) < today)
      .sort((a, b) => (String(a.date) < String(b.date) ? -1 : 1))
      .at(-1)
    if (!prior) {
      result[ticker] = history?.[ticker] || []
      continue
    }
    const todays = (history?.[ticker] || []).filter((point) => Number.isFinite(Number(point?.close)) && String(point?.date || '').slice(0, 10) === today)
    result[ticker] = [{ date: String(prior.date).slice(0, 10), close: Number(prior.close), previous_close: true }, ...todays]
  }
  return result
}

export function localDate(now = new Date()) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

// The earliest date in the history, used to ask for the trades in the period.
export function historyStartDate(history) {
  let start = null
  for (const points of Object.values(history || {})) {
    for (const point of points || []) {
      const date = String(point?.date || '').slice(0, 10)
      if (date && (!start || date < start)) start = date
    }
  }
  return start
}

function signedShares(trade) {
  const shares = Math.abs(Number(trade?.shares) || 0)
  const type = String(trade?.type || '').toLowerCase()
  if (type === 'sell' || type === 'transfer_out') return -shares
  if (type === 'buy' || type === 'transfer_in' || type === 'adjustment') return shares
  return 0
}

// rows: holdings with `ticker`, `shares` (held now) and `price` (current).
// history: { ticker: [{ date, close, timestamp? }] } for the period.
// trades: { ticker: [{ date, type, shares, price_per_share }] } on or after the
//   period's first date (from /api/transactions/window).
export function periodGainFromHoldings(rows, history, trades) {
  const tickers = []
  const allKeys = new Set()
  const missing = []
  for (const row of rows || []) {
    const ticker = row?.ticker
    const sharesNow = Number(row?.shares)
    const tickerTrades = trades?.[ticker] || []
    if (!ticker || ((!Number.isFinite(sharesNow) || sharesNow <= 0) && !tickerTrades.length)) continue
    const points = (history?.[ticker] || [])
      .filter((point) => Number.isFinite(Number(point?.close)) && Number(point.close) > 0 && point?.date)
      .map((point) => ({ key: pointKey(point), close: Number(point.close) }))
      .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
    if (!points.length) {
      if (sharesNow > 0 || tickerTrades.length) missing.push(ticker)
      continue
    }
    const price = Number(row.price)
    if (Number.isFinite(price) && price > 0) points.push({ key: END_KEY, close: price })
    if (points.length < 2) {
      if (sharesNow > 0) missing.push(ticker)
      continue
    }
    points.forEach((point) => allKeys.add(point.key))
    tickers.push({ ticker, sharesNow: Number.isFinite(sharesNow) ? Math.max(sharesNow, 0) : 0, points, trades: tickerTrades })
  }
  const keys = [...allKeys].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
  if (keys.length < 2 || !tickers.length) {
    return { change: null, percent: null, startDate: null, endDate: null, priced: 0, missing }
  }

  const steps = keys.slice(1).map(() => ({ gain: 0, base: 0 }))
  let startValue = 0
  const byTicker = {}
  for (const item of tickers) {
    // Closing price at each key, carrying the last close forward.
    const closes = []
    let j = -1
    for (const key of keys) {
      while (j + 1 < item.points.length && item.points[j + 1].key <= key) j += 1
      closes.push(j >= 0 ? item.points[j].close : null)
    }
    // Trades go in the step that ends on or after their date. Trades dated on
    // or before the first point happened before the period started.
    const tradesByStep = keys.slice(1).map(() => [])
    for (const trade of item.trades) {
      const date = String(trade?.date || '').slice(0, 10)
      const delta = signedShares(trade)
      if (!date || !delta) continue
      const step = keys.findIndex((key, index) => index > 0 && keys[index - 1] < date && date <= key)
      if (step > 0) tradesByStep[step - 1].push({ delta, price: Number(trade?.price_per_share), cash: trade?.cash == null || trade.cash === '' ? null : Number(trade.cash) })
    }
    // Shares held at each key: today's shares minus later trades.
    const held = new Array(keys.length).fill(0)
    let shares = item.sharesNow
    held[keys.length - 1] = shares
    for (let i = keys.length - 2; i >= 0; i -= 1) {
      for (const trade of tradesByStep[i]) shares -= trade.delta
      held[i] = Math.max(shares, 0)
    }
    // Value on the first day of the period. A stock whose prices only begin
    // later is left out, like its gain before then (matches Performance).
    if (closes[0] != null) startValue += held[0] * closes[0]
    let tickerGain = 0
    for (let i = 0; i < steps.length; i += 1) {
      const start = closes[i]
      const end = closes[i + 1]
      if (start == null || end == null) continue
      let gain = held[i] * (end - start)
      let base = held[i] * start
      for (const trade of tradesByStep[i]) {
        // What the trade actually cost (or brought in), from Fidelity's amount
        // including fees when available; otherwise shares x price.
        const perShare = Number.isFinite(trade.price) && trade.price > 0 ? trade.price : end
        const amount = Number.isFinite(trade.cash) && trade.cash !== 0 ? -trade.cash : trade.delta * perShare
        gain += trade.delta * end - amount
        if (trade.delta > 0) base += amount
      }
      steps[i].gain += gain
      steps[i].base += base
      tickerGain += gain
    }
    byTicker[item.ticker] = tickerGain
  }

  // Percent = gain / money at work: the value held at the start plus the most
  // extra money that was put in at any point in the period (buys minus sale
  // proceeds, running total, sales on a day counted before that day's buys).
  // Selling one stock to buy another adds nothing, and the result always has
  // the same sign as the dollar gain.
  const change = steps.reduce((sum, step) => sum + step.gain, 0)
  const flows = []
  for (const item of tickers) {
    for (const trade of item.trades) {
      const date = String(trade?.date || '').slice(0, 10)
      const delta = signedShares(trade)
      const price = Number(trade?.price_per_share)
      if (!delta || !(date > keys[0].slice(0, 10)) || !Number.isFinite(price) || price <= 0) continue
      flows.push({ date, amount: delta * price })
    }
  }
  flows.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : a.amount - b.amount))
  let running = 0
  let peak = 0
  for (const flow of flows) {
    running += flow.amount
    peak = Math.max(peak, running)
  }
  const invested = startValue + peak
  const firstKey = keys[0]
  return {
    change,
    percent: invested > 0 ? (change / invested) * 100 : null,
    invested,
    startValue,
    startDate: firstKey.slice(0, 10),
    endDate: keys.filter((key) => key !== END_KEY).at(-1)?.slice(0, 10) || null,
    priced: tickers.length,
    missing,
    byTicker,
  }
}

// Cash and dividends over a period, from /api/transactions/window.
//
// Uninvested cash is part of the portfolio: sale proceeds and dividends add
// to it, buys spend it, and a buy costing more than the account's cash was
// paid with new money from outside. Dividends (and interest on the money
// market fund) count as gain. Returns the cash at the start and end, the
// dividends received, and the new money added during the period.
export function cashActivity(trades, cashStart, startDate) {
  const balances = { ...(cashStart || {}) }
  const start = Object.values(balances).reduce((sum, value) => sum + (Number(value) || 0), 0)
  const events = []
  for (const entries of Object.values(trades || {})) {
    for (const trade of entries || []) {
      const date = String(trade?.date || '').slice(0, 10)
      const cash = Number(trade?.cash)
      if (!date || date <= startDate || !Number.isFinite(cash) || cash === 0) continue
      events.push({ date, cash, account: trade.account || '', income: trade.type === 'dividend' || trade.type === 'interest' })
    }
  }
  // Within a day, money comes in before it is spent.
  events.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : (a.cash < 0) - (b.cash < 0)))
  let dividends = 0
  let newMoney = 0
  for (const event of events) {
    if (event.income) dividends += event.cash
    const balance = (Number(balances[event.account]) || 0) + event.cash
    if (balance < 0) newMoney -= balance
    balances[event.account] = Math.max(balance, 0)
  }
  const end = Object.values(balances).reduce((sum, value) => sum + (Number(value) || 0), 0)
  return { start, end, dividends, newMoney }
}

// Whole-portfolio gain for the period: price moves on the shares held each
// day (from periodGainFromHoldings) plus dividends. The percentage is that
// gain divided by the money at work: stocks and cash at the start plus new
// money added from outside during the period.
export function combinedPeriodGain(stocks, cash) {
  if (!stocks || stocks.change == null) return stocks
  const change = stocks.change + (cash?.dividends || 0)
  const invested = (stocks.startValue || 0) + (cash?.start || 0) + (cash?.newMoney || 0)
  return {
    ...stocks,
    priceChange: stocks.change,
    dividends: cash?.dividends || 0,
    change,
    percent: invested > 0 ? (change / invested) * 100 : null,
    invested,
    cashStart: cash?.start || 0,
    cashEnd: cash?.end || 0,
    newMoney: cash?.newMoney || 0,
  }
}

// Stocks plus uninvested cash for one Performance series point.
export function seriesValue(point) {
  return Number(point?.market_value ?? 0) + Number(point?.cash ?? 0)
}

// Gain over the selected range, by the same rule as the Holdings summary:
// change in (value + cash - money put in) between the first and last day, i.e.
// price moves and dividends; new money is not gain. Percent is of the value
// and cash at the start plus any new money added during the range.
export function periodGainFromSeries(series) {
  const points = (series || []).filter((point) => point && point.date)
  if (points.length < 2) return null
  const first = points[0]
  const last = points[points.length - 1]
  const capital = (point) => Number(point.capital_basis ?? point.cost_basis ?? 0)
  const gainOf = (point) => Number(point.capital_gain_loss ?? (seriesValue(point) - capital(point)))
  // period_gain is the server's day-by-day gain (price moves on the shares held
  // each day, trades excluded, plus dividends); fall back to the change in
  // overall gain for older responses.
  const change = last.period_gain != null && first.period_gain != null
    ? Number(last.period_gain) - Number(first.period_gain)
    : gainOf(last) - gainOf(first)
  // Same rule as the Holdings summary: value and cash on the first day plus
  // new money from outside (buys the account's cash could not cover).
  const newMoney = last.new_money != null && first.new_money != null
    ? Number(last.new_money) - Number(first.new_money)
    : Math.max(capital(last) - capital(first), 0)
  const invested = seriesValue(first) + newMoney
  return { change, percent: invested > 0 ? (change / invested) * 100 : null, startDate: first.date }
}
