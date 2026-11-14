import test from 'node:test'
import assert from 'node:assert/strict'
import { withEarnings } from './earningsOverlay.js'

const points = [
  { date: '2026-01-02', close: 100 },
  { date: '2026-02-02', close: 120 },
  { date: '2026-03-02', close: 150 },
]
const history = [
  { filed: '2025-11-01', period_end: '2025-09-30', quarter_eps: 1.25, ttm_eps: 5 },
  { filed: '2026-02-02', period_end: '2025-12-31', quarter_eps: 1.5, ttm_eps: 6 },
]

test('earnings line follows each quarter\'s EPS, starting at the price', () => {
  const { points: out, anchor } = withEarnings(points, history)
  assert.equal(anchor, 80) // 100 / 1.25
  assert.deepEqual(out.map((p) => p.earnings), [100, 120, 120])
  assert.deepEqual(out.map((p) => p.quarter_eps), [1.25, 1.5, 1.5])
  assert.deepEqual(out.map((p) => p.quarter_end), ['2025-09-30', '2025-12-31', '2025-12-31'])
})

test('P/E uses trailing four-quarter EPS', () => {
  const { points: out } = withEarnings(points, history)
  assert.deepEqual(out.map((p) => p.pe), [20, 20, 25])
  assert.deepEqual(out.map((p) => p.eps), [5, 6, 6])
})

test('a quarter without a trailing figure keeps the last P/E basis', () => {
  const { points: out } = withEarnings(points, [
    { filed: '2025-11-01', quarter_eps: 1, ttm_eps: 4 },
    { filed: '2026-02-02', quarter_eps: 2, ttm_eps: null },
  ])
  assert.deepEqual(out.map((p) => p.pe), [25, 30, 37.5])
  assert.deepEqual(out.map((p) => p.earnings), [100, 200, 200])
})

test('reports filed after a day are not used for it', () => {
  const { points: out } = withEarnings([{ date: '2025-10-01', close: 90 }, ...points], history)
  assert.equal(out[0].earnings, null)
  assert.equal(out[0].pe, null)
  assert.equal(out[1].earnings, 100)
})

test('loss-making quarters leave gaps', () => {
  const { points: out } = withEarnings(points, [
    { filed: '2025-11-01', quarter_eps: -0.5, ttm_eps: -1 },
    { filed: '2026-03-01', quarter_eps: 1, ttm_eps: 3 },
  ])
  assert.deepEqual(out.map((p) => p.earnings), [null, null, 150])
})

test('older responses with only trailing EPS still work', () => {
  const { points: out } = withEarnings(points, [{ filed: '2025-11-01', ttm_eps: 5 }, { filed: '2026-02-02', ttm_eps: 6 }])
  assert.deepEqual(out.map((p) => p.earnings), [100, 120, 120])
})

test('no history leaves points alone', () => {
  assert.equal(withEarnings(points, []).points, points)
})
