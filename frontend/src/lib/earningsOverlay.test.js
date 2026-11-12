import test from 'node:test'
import assert from 'node:assert/strict'
import { withEarnings } from './earningsOverlay.js'

const points = [
  { date: '2026-01-02', close: 100 },
  { date: '2026-02-02', close: 120 },
  { date: '2026-03-02', close: 150 },
]
const history = [
  { filed: '2025-11-01', ttm_eps: 5 },
  { filed: '2026-02-02', ttm_eps: 6 },
]

test('earnings line starts at the price and follows EPS', () => {
  const { points: out, anchorPe } = withEarnings(points, history)
  assert.equal(anchorPe, 20)
  assert.deepEqual(out.map((p) => p.earnings), [100, 120, 120])
  assert.deepEqual(out.map((p) => p.pe), [20, 20, 25])
})

test('reports filed after a day are not used for it', () => {
  const { points: out } = withEarnings([{ date: '2025-10-01', close: 90 }, ...points], history)
  assert.equal(out[0].earnings, null)
  assert.equal(out[0].pe, null)
  assert.equal(out[1].earnings, 100)
})

test('losses leave gaps', () => {
  const { points: out, anchorPe } = withEarnings(points, [{ filed: '2025-11-01', ttm_eps: -1 }, { filed: '2026-03-01', ttm_eps: 3 }])
  assert.equal(anchorPe, 50)
  assert.deepEqual(out.map((p) => p.earnings), [null, null, 150])
})

test('no history leaves points alone', () => {
  assert.equal(withEarnings(points, []).points, points)
})
