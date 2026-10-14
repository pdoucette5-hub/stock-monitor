// Run with:  node --test frontend/src/lib/holdingsGain.test.js
import test from 'node:test'
import assert from 'node:assert/strict'
import { gainForRow, summarizeHoldings } from './holdingsGain.js'

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
