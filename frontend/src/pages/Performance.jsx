import { useEffect, useMemo, useState } from 'react'
import PageToolbar from '../components/PageToolbar'
import { fetchPortfolioPerformance } from '../lib/api'

const RANGES = ['1m', '3m', '6m', '1y', '3y', '5y']

function formatMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return Number(value).toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function formatShortDate(dateString) {
  if (!dateString) return ''
  const d = new Date(dateString)
  if (Number.isNaN(d.getTime())) return dateString
  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: '2-digit',
  })
}

function MetricCard({ label, value }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className="mt-2 text-xl font-semibold text-slate-900">{value}</div>
    </div>
  )
}

function PerformanceChart({ series }) {
  if (!series || series.length === 0) {
    return (
      <div className="flex h-80 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-sm text-slate-500">
        No portfolio performance data available.
      </div>
    )
  }

  const width = 1000
  const height = 320
  const padding = 48

  const values = []
  series.forEach((point) => {
    values.push(Number(point.market_value ?? 0))
    values.push(Number(point.cost_basis ?? 0))
  })

  const minValue = Math.min(...values)
  const maxValue = Math.max(...values)
  const valueRange = maxValue - minValue || 1

  const xFor = (idx) => {
    if (series.length === 1) return padding
    return padding + (idx / (series.length - 1)) * (width - padding * 2)
  }

  const yFor = (value) => {
    return height - padding - ((value - minValue) / valueRange) * (height - padding * 2)
  }

  const marketPath = series
    .map((point, idx) => `${idx === 0 ? 'M' : 'L'} ${xFor(idx)} ${yFor(Number(point.market_value ?? 0))}`)
    .join(' ')

  const costPath = series
    .map((point, idx) => `${idx === 0 ? 'M' : 'L'} ${xFor(idx)} ${yFor(Number(point.cost_basis ?? 0))}`)
    .join(' ')

  const ticks = 4
  const yTicks = Array.from({ length: ticks + 1 }, (_, i) => {
    const value = minValue + ((maxValue - minValue) * i) / ticks
    return { value, y: yFor(value) }
  })

  const first = series[0]
  const last = series[series.length - 1]
  const marketChange = Number(last.market_value ?? 0) - Number(first.market_value ?? 0)
  const positive = marketChange >= 0

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-slate-500">Portfolio Value Over Time</div>
          <div className="text-lg font-semibold text-slate-900">
            {formatMoney(last.market_value)}
          </div>
        </div>
        <div
          className={`rounded-full px-3 py-1 text-sm font-medium ${
            positive ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'
          }`}
        >
          {positive ? '+' : ''}
          {formatMoney(marketChange)}
        </div>
      </div>

      <div className="mb-3 flex items-center gap-4 text-xs text-slate-600">
        <div className="flex items-center gap-2">
          <span className="inline-block h-0.5 w-6 bg-blue-600" />
          Market Value
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-block h-0.5 w-6 bg-amber-500" />
          Cost Basis
        </div>
      </div>

      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${width} ${height}`} className="h-80 w-full min-w-[800px]">
          {yTicks.map((tick) => (
            <g key={tick.value}>
              <line
                x1={padding}
                x2={width - padding}
                y1={tick.y}
                y2={tick.y}
                stroke="#e2e8f0"
                strokeDasharray="4 4"
              />
              <text x={6} y={tick.y + 4} fontSize="11" fill="#64748b">
                {Math.round(tick.value).toLocaleString()}
              </text>
            </g>
          ))}

          <path
            d={costPath}
            fill="none"
            stroke="#f59e0b"
            strokeWidth="2.5"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          <path
            d={marketPath}
            fill="none"
            stroke="#2563eb"
            strokeWidth="3"
            strokeLinejoin="round"
            strokeLinecap="round"
          />

          <text x={padding} y={height - 10} fontSize="11" fill="#64748b">
            {formatShortDate(first.date)}
          </text>
          <text x={width - padding} y={height - 10} textAnchor="end" fontSize="11" fill="#64748b">
            {formatShortDate(last.date)}
          </text>
        </svg>
      </div>
    </div>
  )
}

export default function Performance() {
  const [range, setRange] = useState('1y')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)

  async function loadPerformance(nextRange = range) {
    setLoading(true)
    setError(null)
    try {
      const payload = await fetchPortfolioPerformance(nextRange)
      setData(payload)
      setLastUpdated(new Date())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load portfolio performance')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadPerformance(range)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range])

  const latest = data?.latest ?? {}
  const series = data?.series ?? []
  const positions = data?.positions ?? {}
  const positionRows = useMemo(
    () =>
      Object.entries(positions)
        .map(([ticker, summary]) => ({
          ticker,
          current_shares: summary?.current_shares ?? 0,
          total_cost_basis: summary?.total_cost_basis ?? 0,
          average_cost_per_share: summary?.average_cost_per_share ?? 0,
          realized_gain_loss: summary?.realized_gain_loss ?? 0,
        }))
        .sort((a, b) => a.ticker.localeCompare(b.ticker)),
    [positions],
  )

  return (
    <div className="mx-auto w-[98vw] px-4 py-8">
      <PageToolbar
        title="Performance"
        description="Portfolio performance over time based on your transaction ledger and historical price data."
        loading={loading}
        onReload={() => loadPerformance(range)}
        onUpdatePrices={() => loadPerformance(range)}
      />

      {error && (
        <div
          role="alert"
          className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      <div className="mb-6 flex flex-wrap gap-2">
        {RANGES.map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setRange(value)}
            className={[
              'rounded-full px-3 py-1 text-sm font-medium transition',
              range === value
                ? 'bg-slate-900 text-white'
                : 'bg-slate-100 text-slate-700 hover:bg-slate-200',
            ].join(' ')}
          >
            {value.toUpperCase()}
          </button>
        ))}
      </div>

      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <MetricCard label="Market Value" value={formatMoney(latest.market_value)} />
        <MetricCard label="Cost Basis" value={formatMoney(latest.cost_basis)} />
        <MetricCard
          label="Unrealized Gain/Loss"
          value={formatMoney(latest.unrealized_gain_loss)}
        />
      </div>

      <div className="mb-6">
        {loading ? (
          <div className="flex h-80 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-sm text-slate-500">
            Loading performance…
          </div>
        ) : (
          <PerformanceChart series={series} />
        )}
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-200 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-900">Latest Position Summaries</h2>
          {lastUpdated && (
            <p className="mt-1 text-xs text-slate-500">
              Last updated {lastUpdated.toLocaleTimeString()}
            </p>
          )}
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className="whitespace-nowrap px-4 py-3 font-semibold text-slate-700">
                  Ticker
                </th>
                <th className="whitespace-nowrap px-4 py-3 font-semibold text-slate-700">
                  Current Shares
                </th>
                <th className="whitespace-nowrap px-4 py-3 font-semibold text-slate-700">
                  Cost Basis
                </th>
                <th className="whitespace-nowrap px-4 py-3 font-semibold text-slate-700">
                  Avg Cost / Share
                </th>
                <th className="whitespace-nowrap px-4 py-3 font-semibold text-slate-700">
                  Realized Gain/Loss
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr>
                  <td colSpan={5} className="px-4 py-10 text-center text-slate-500">
                    Loading…
                  </td>
                </tr>
              ) : positionRows.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-10 text-center text-slate-500">
                    No position summaries available.
                  </td>
                </tr>
              ) : (
                positionRows.map((row) => (
                  <tr key={row.ticker} className="hover:bg-slate-50/80">
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-slate-900">
                      {row.ticker}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {row.current_shares.toLocaleString(undefined, {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 4,
                      })}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {formatMoney(row.total_cost_basis)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {formatMoney(row.average_cost_per_share)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {formatMoney(row.realized_gain_loss)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}