// Run with:  node --test frontend/src/lib/holdingsGain.test.js
import test from 'node:test'
import assert from 'node:assert/strict'
import { gainForRow, summarizeHoldings, periodGainFromHoldings as pgh, withPreviousClose as wpc } from './holdingsGain.js'

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
  // day 1: +50 -150 on 4000 = -2.5%; step 2: +50 -150 on 1050+2850=3900
  near(r.percent, ((1 - 100 / 4000) * (1 - 100 / 3900) - 1) * 100, 'percent')
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
  const history = wpc({ A: intraday }, daily)
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
