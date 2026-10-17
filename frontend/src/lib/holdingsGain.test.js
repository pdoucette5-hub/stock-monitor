// Run with:  node --test frontend/src/lib/holdingsGain.test.js
import test from 'node:test'
import assert from 'node:assert/strict'
import { gainForRow, summarizeHoldings, periodGainFromHoldings as pgh, withPreviousClose as wpc, cashActivity, combinedPeriodGain, periodGainFromSeries } from './holdingsGain.js'

const pts = (...closes) => closes.map((close, i) => ({ date: `2026-10-0${i + 1}`, close }))

test('totals value and gain across positions, weighting by shares held', () => {
  const rows = [
    { ticker: 'AAA', shares: 10, price: 110, market_value: 1100 },
    { ticker: 'BBB', shares: 5, price: 40, market_value: 200 },
  ]
  const history = { AAA: pts(100, 105), BBB: pts(50, 45) }
  const s = summarizeHoldings(rows, history, '7d')
  assert.equal(s.totalValue, 1300)
  assert.equal(s.change, 10 * 10 - 5 * 10) // +100 and -50
  assert.equal(s.percent, (50 / (10 * 100 + 5 * 50)) * 100) // 4%
  assert.equal(s.positions, 2)
  assert.deepEqual(s.missing, [])
})

test('positions without history count in value but not in gain, and are reported', () => {
  const rows = [
    { ticker: 'AAA', shares: 10, price: 110, market_value: 1100 },
    { ticker: 'FUND', shares: 2, price: 30, market_value: 60 },
  ]
  const s = summarizeHoldings(rows, { AAA: pts(100, 105) }, '7d')
  assert.equal(s.totalValue, 1160)
  assert.equal(s.change, 100)
  assert.equal(s.percent, 10)
  assert.deepEqual(s.missing, ['FUND'])
  assert.equal(s.positions, 2)
})

test('ignores rows with no shares (e.g. outside the Managed filter)', () => {
  const rows = [
    { ticker: 'AAA', shares: 0, price: 110, market_value: 0 },
    { ticker: 'BBB', shares: 1, price: 60, market_value: 60 },
  ]
  const s = summarizeHoldings(rows, { AAA: pts(100, 105), BBB: pts(50, 55) }, '1m')
  assert.equal(s.totalValue, 60)
  assert.equal(s.change, 10)
  assert.equal(s.positions, 1)
})

test('1Y uses the server one-year return when there is no price history, like the rows do', () => {
  const row = { ticker: 'AAA', shares: 4, price: 125, market_value: 500, price_return_1y: 0.25 }
  const g = gainForRow(row, [], '1y')
  assert.equal(g.start, 100)
  const s = summarizeHoldings([row], {}, '1y')
  assert.equal(s.change, 100)
  assert.equal(s.percent, 25)
})

test('summary gain equals the sum of each row gain times its shares', () => {
  const rows = [
    { ticker: 'AAA', shares: 3.5, price: 141.25, market_value: 3.5 * 141.25 },
    { ticker: 'BBB', shares: 12, price: 22.4, market_value: 12 * 22.4 },
  ]
  const history = { AAA: pts(150.28, 139.9), BBB: pts(20, 21) }
  const expected = rows.reduce((sum, r) => sum + r.shares * gainForRow(r, history[r.ticker], '7d').amount, 0)
  assert.ok(Math.abs(summarizeHoldings(rows, history, '7d').change - expected) < 1e-9)
})

test('no positions with history gives no gain rather than zero', () => {
  const s = summarizeHoldings([{ ticker: 'FUND', shares: 2, price: 30, market_value: 60 }], {}, '7d')
  assert.equal(s.change, null)
  assert.equal(s.percent, null)
})

import { periodGainFromPerformance, PERFORMANCE_MODES_FOR_FILTER } from './holdingsGain.js'

const day = (date, market_value, capital_basis) => ({ date, market_value, capital_basis, capital_gain_loss: market_value - capital_basis })

test('period gain is the change in capital gain, so money added is not counted', () => {
  // Start: $1,000 invested, worth $1,200. During the year $5,000 more is added.
  // End: $6,000 invested, worth $6,600. Gain over the period: 600 - 200 = 400.
  const g = periodGainFromPerformance([{ series: [day('2025-10-14', 1200, 1000), day('2026-10-14', 6600, 6000)] }])
  assert.equal(g.change, 400)
  assert.equal(g.percent, (400 / (1200 + 5000)) * 100)
  assert.equal(g.startDate, '2025-10-14')
})

test('shares bought after a price fall are not charged for the fall', () => {
  // Nothing held at the start; bought $10,000 during the year; now worth $11,000.
  const g = periodGainFromPerformance([{ series: [day('2025-10-14', 0, 0), day('2026-03-01', 10000, 10000), day('2026-10-14', 11000, 10000)] }])
  assert.equal(g.change, 1000)
  assert.equal(g.percent, 10)
})

test('managed and tracked results are added together', () => {
  const managed = { series: [day('2025-10-14', 1000, 800), day('2026-10-14', 1100, 800)] }
  const track = { series: [day('2025-10-14', 500, 500), day('2026-10-14', 450, 500)] }
  const g = periodGainFromPerformance([managed, track])
  assert.equal(g.change, 100 - 50)
  assert.equal(g.percent, (50 / 1500) * 100)
  assert.deepEqual(PERFORMANCE_MODES_FOR_FILTER.active, ['managed', 'track'])
})

test('unsorted series and missing data are handled', () => {
  const g = periodGainFromPerformance([{ series: [day('2026-10-14', 900, 1000), day('2025-10-14', 1000, 1000)] }, { series: [] }, null])
  assert.equal(g.change, -100)
  assert.equal(g.percent, -10)
  assert.equal(periodGainFromPerformance([]).change, null)
})

// ---- periodGainFromHoldings: shares held each day x that day's price move ----

const days = (closes, start = 1) => closes.map((close, i) => ({ date: `2026-10-${String(start + i).padStart(2, '0')}`, close }))
const near = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-6, `${msg}: ${a} vs ${b}`)

test('no trades: shares x price change, percent is value-weighted', () => {
  const rows = [{ ticker: 'A', shares: 10, price: 110 }, { ticker: 'B', shares: 30, price: 90 }]
  const history = { A: days([100, 105]), B: days([100, 95]) }
  const r = pgh(rows, history, {})
  // A: 10 x (105-100) + 10 x (110-105) = 100; B: 30 x (90-100) = -300
  near(r.change, -200, 'change')
  near(r.byTicker.A, 100, 'A'); near(r.byTicker.B, -300, 'B')
  near(r.percent, -200 / 4000 * 100, 'percent of starting value')
})

test('selling one stock and buying another is not a gain or loss', () => {
  // Hold 100 A, sell all on day 2 at 100, buy 100 B on day 2 at 100; prices flat.
  const rows = [{ ticker: 'A', shares: 0, price: 100 }, { ticker: 'B', shares: 100, price: 100 }]
  const history = { A: days([100, 100, 100]), B: days([100, 100, 100]) }
  const trades = { A: [{ date: '2026-10-02', type: 'sell', shares: 100, price_per_share: 100 }], B: [{ date: '2026-10-02', type: 'buy', shares: 100, price_per_share: 100 }] }
  const r = pgh(rows, history, trades)
  near(r.change, 0, 'change'); near(r.percent, 0, 'percent')
})

test('a stock sold mid-period only counts while held', () => {
  const rows = [{ ticker: 'A', shares: 0, price: 150 }]
  const history = { A: days([100, 110, 150]) }
  const trades = { A: [{ date: '2026-10-02', type: 'sell', shares: 10, price_per_share: 110 }] }
  const r = pgh(rows, history, trades)
  near(r.change, 100, 'gain up to the sale only') // 10 x (110 - 100); the later rise is not ours
  near(r.percent, 10, 'percent')
})

test('a stock bought mid-period counts from the price paid; new money is not gain', () => {
  const rows = [{ ticker: 'A', shares: 20, price: 120 }]
  const history = { A: days([100, 110, 120]) }
  const trades = { A: [{ date: '2026-10-02', type: 'buy', shares: 10, price_per_share: 105 }] }
  const r = pgh(rows, history, trades)
  // 10 held: 10 x 20 = 200; 10 bought at 105: 10 x (120 - 105) = 150
  near(r.change, 350, 'change')
})

test('trades on or before the first day are already in the starting shares', () => {
  const rows = [{ ticker: 'A', shares: 10, price: 110 }]
  const r = pgh(rows, { A: days([100, 110]) }, { A: [{ date: '2026-10-01', type: 'buy', shares: 10, price_per_share: 90 }] })
  near(r.change, 100, 'change')
})

test('Today starts from the previous close and counts trades made today', () => {
  const intraday = [{ date: '2026-10-15', timestamp: 1000, close: 102 }, { date: '2026-10-15', timestamp: 2000, close: 104 }]
  const daily = { A: [...days([95, 100], 13), { date: '2026-10-15', close: 104 }] }
  const history = wpc({ A: intraday }, daily, '2026-10-15')
  assert.equal(history.A[0].close, 100)
  assert.equal(history.A[0].date, '2026-10-14')
  const rows = [{ ticker: 'A', shares: 15, price: 105 }]
  const trades = { A: [{ date: '2026-10-15', type: 'buy', shares: 5, price_per_share: 103 }] }
  const r = pgh(rows, history, trades)
  // 10 held from 100 -> 105 = 50; 5 bought at 103 -> 105 = 10
  near(r.change, 60, 'change')
})

test('positions without price history are reported, not guessed', () => {
  const r = pgh([{ ticker: 'A', shares: 1, price: 2 }, { ticker: 'FUND', shares: 5, price: 10 }], { A: days([1, 2]) }, {})
  assert.deepEqual(r.missing, ['FUND'])
  near(r.change, 1, 'change')
})

test('Today ignores intraday samples from an earlier day', () => {
  // Intraday sampling stopped on Oct 13; today is Oct 15. Start must be Oct 14's close.
  const stale = [{ date: '2026-10-13', timestamp: 1, close: 936.7 }, { date: '2026-10-13', timestamp: 2, close: 940 }]
  const daily = { MU: days([989.04, 953.19, 914.13, 911.5], 12) }
  const history = wpc({ MU: stale }, daily, '2026-10-15')
  assert.deepEqual(history.MU.map((p) => p.close), [914.13])
  const g = gainForRow({ price: 911.5 }, history.MU, '1d')
  near(g.amount, 911.5 - 914.13, 'per-share change')
  near(g.percent, (911.5 / 914.13 - 1) * 100, 'percent')
  const r = pgh([{ ticker: 'MU', shares: 68.16, price: 911.5 }], history, {})
  near(r.change, 68.16 * (911.5 - 914.13), 'position change')
})

test('percent has the same sign as the dollars and uses money at work', () => {
  // Small loss on a small early position, big gain after adding money.
  const rows = [{ ticker: 'A', shares: 100, price: 150 }]
  const history = { A: days([100, 50, 100]) }
  const trades = { A: [{ date: '2026-10-02', type: 'buy', shares: 90, price_per_share: 50 }] }
  const r = pgh(rows, history, trades)
  // 10 sh: 100->50 = -500; 90 bought at 50, all 100 sh 50->150 = 10000; total 9500
  near(r.change, 9500, 'change')
  near(r.percent, 9500 / (1000 + 4500) * 100, 'percent') // start value 1000 + 4500 new money
  assert.ok(r.percent > 0)
})

test('a position sold during the period counts while held, and its sale funds the next buy', () => {
  const rows = [{ ticker: 'B', shares: 10, price: 110 }, { ticker: 'SOLD', shares: 0, price: null }]
  const history = { SOLD: days([100, 80, 80]), B: days([100, 100, 100]) }
  const trades = { SOLD: [{ date: '2026-10-02', type: 'sell', shares: 10, price_per_share: 80 }], B: [{ date: '2026-10-02', type: 'buy', shares: 10, price_per_share: 100 }] }
  const r = pgh(rows, history, trades)
  near(r.byTicker.SOLD, -200, 'loss while held')
  near(r.byTicker.B, 100, 'gain on B')
  near(r.change, -100, 'total')
  // start 1000; sold 800 then bought 1000 -> 200 new money at peak
  near(r.percent, -100 / 1200 * 100, 'percent')
})

test('cash: sale proceeds wait as cash, buys spend it, extra is new money, dividends are gain', () => {
  const trades = {
    A: [{ date: '2026-10-02', type: 'sell', cash: 1000, account: 'Roth' }],
    B: [{ date: '2026-10-03', type: 'buy', cash: -1500, account: 'Roth' }],
    NVDA: [{ date: '2026-10-04', type: 'dividend', cash: 25, account: 'Roth' }],
    SPAXX: [{ date: '2026-10-05', type: 'interest', cash: 3, account: 'IRA' }],
    OLD: [{ date: '2026-10-01', type: 'buy', cash: -999, account: 'Roth' }], // on the start date: already counted
  }
  const c = cashActivity(trades, { Roth: 200, IRA: 10 }, '2026-10-01')
  near(c.start, 210, 'start'); near(c.dividends, 28, 'dividends')
  near(c.newMoney, 300, 'new money') // 200 + 1000 - 1500 = -300
  near(c.end, 0 + 25 + 13, 'end cash')
  const g = combinedPeriodGain({ change: 100, startValue: 5000 }, c)
  near(g.change, 128, 'price + dividends')
  near(g.percent, 128 / (5000 + 210 + 300) * 100, 'percent')
})

test('selling and holding the cash is neutral; the cash stays in the base', () => {
  const rows = [{ ticker: 'A', shares: 0, price: 100 }]
  const trades = { A: [{ date: '2026-10-02', type: 'sell', shares: 10, price_per_share: 100, cash: 1000, account: 'X' }] }
  const stocks = pgh(rows, { A: days([100, 100, 100]) }, trades)
  const g = combinedPeriodGain(stocks, cashActivity(trades, {}, '2026-10-01'))
  near(g.change, 0, 'change'); near(g.newMoney, 0, 'no new money'); near(g.cashEnd, 1000, 'cash')
})

test('Performance range gain: change in (value + cash - capital), new money not gain', () => {
  const series = [
    { date: '2026-01-01', market_value: 1000, cash: 0, capital_basis: 1000, capital_gain_loss: 0 },
    { date: '2026-06-01', market_value: 1600, cash: 50, capital_basis: 1500, capital_gain_loss: 150 }, // +500 new money
    { date: '2026-10-16', market_value: 1700, cash: 75, capital_basis: 1500, capital_gain_loss: 275 },
  ]
  const g = periodGainFromSeries(series)
  near(g.change, 275, 'change')
  near(g.percent, 275 / (1000 + 500) * 100, 'percent')
  assert.equal(periodGainFromSeries(series.slice(0, 1)), null)
})

test('Performance range gain prefers the server day-by-day figure', () => {
  const series = [
    { date: '2026-01-01', market_value: 1000, cash: 0, capital_basis: 1000, capital_gain_loss: -500, period_gain: 0 },
    { date: '2026-10-16', market_value: 5000, cash: 0, capital_basis: 1000, capital_gain_loss: 4000, period_gain: 120 },
  ]
  near(periodGainFromSeries(series).change, 120, 'uses period_gain, not the jump in overall gain')
})

test('trade gain uses the actual amount, including fees, when available', () => {
  const rows = [{ ticker: 'A', shares: 10, price: 110 }]
  const trades = { A: [{ date: '2026-10-02', type: 'buy', shares: 10, price_per_share: 100, cash: -1000.11 }] }
  const r = pgh(rows, { A: days([100, 100]) }, trades)
  near(r.change, 10 * 110 - 1000.11, 'gain net of the fee')
})

test('Performance percent: first-day value and cash plus new money from outside', () => {
  const series = [
    { date: '2026-01-01', market_value: 1000, cash: 100, capital_basis: 1000, period_gain: 0, new_money: 0 },
    { date: '2026-10-16', market_value: 2000, cash: 0, capital_basis: 1700, period_gain: 300, new_money: 400 },
  ]
  near(periodGainFromSeries(series).percent, 300 / (1100 + 400) * 100, 'percent')
})

test('gain series ends at the headline gain, with dividends added on their dates', () => {
  const rows = [{ ticker: 'AAA', shares: 10, price: 13 }]
  const history = { AAA: [{ date: '2026-01-02', close: 10 }, { date: '2026-01-03', close: 11 }, { date: '2026-01-04', close: 12 }] }
  const trades = { AAA: [{ date: '2026-01-03', type: 'dividend', shares: 0, account: 'IRA', cash: 5 }] }
  const stocks = pgh(rows, history, trades)
  const g = combinedPeriodGain(stocks, cashActivity(trades, {}, stocks.startDate))
  assert.deepEqual(g.series.map((point) => point.gain), [0, 15, 25, 35])
  assert.equal(g.series.at(-1).gain, g.change)
})

test('value series is shares held times price plus cash, ending at today', () => {
  const rows = [{ ticker: 'AAA', shares: 10, price: 13 }]
  const history = { AAA: [{ date: '2026-01-02', close: 10 }, { date: '2026-01-03', close: 11 }, { date: '2026-01-04', close: 12 }] }
  const trades = { AAA: [{ date: '2026-01-03', type: 'dividend', shares: 0, account: 'IRA', cash: 5 }] }
  const stocks = pgh(rows, history, trades)
  const g = combinedPeriodGain(stocks, cashActivity(trades, { IRA: 20 }, stocks.startDate))
  assert.deepEqual(g.series.map((point) => point.value), [120, 135, 145, 155])
})
