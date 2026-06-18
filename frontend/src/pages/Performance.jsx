import { useEffect, useMemo, useRef, useState } from 'react'
import PageToolbar from '../components/PageToolbar'
import { fetchPortfolioPerformance, fetchPriceComparison } from '../lib/api'

const RANGES = ['1m', '3m', '6m', '1y', '3y', '5y']
const BENCHMARK_OPTIONS = ['', 'SPY', 'QQQ', 'ONEQ']
const MANAGEMENT_MODES = [
  { value: 'all', label: 'All positions' },
  { value: 'managed', label: 'Managed' },
  { value: 'track', label: 'Track only' },
  { value: 'excluded', label: 'Excluded' },
]

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

function displayTickerLabel(ticker) {
  if (ticker === 'ONEQ') return 'Nasdaq Composite (ONEQ)'
  return ticker
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
    .map(
      (point, idx) =>
        `${idx === 0 ? 'M' : 'L'} ${xFor(idx)} ${yFor(Number(point.market_value ?? 0))}`,
    )
    .join(' ')

  const costPath = series
    .map(
      (point, idx) =>
        `${idx === 0 ? 'M' : 'L'} ${xFor(idx)} ${yFor(Number(point.cost_basis ?? 0))}`,
    )
    .join(' ')

  const ticks = 4
  const yTicks = Array.from({ length: ticks + 1 }, (_, i) => {
    const value = minValue + ((maxValue - minValue) * i) / ticks
    return { value, y: yFor(value) }
  })

  const first = series[0]
  const last = series[series.length - 1]
  const marketChange =
    Number(last.market_value ?? 0) - Number(first.market_value ?? 0)
  const positive = marketChange >= 0

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-slate-500">
            Portfolio Value Over Time
          </div>
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
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="h-80 w-full min-w-[800px]"
        >
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
          <text
            x={width - padding}
            y={height - 10}
            textAnchor="end"
            fontSize="11"
            fill="#64748b"
          >
            {formatShortDate(last.date)}
          </text>
        </svg>
      </div>
    </div>
  )
}

function ComparisonChart({ seriesByTicker }) {
  const tickers = Object.keys(seriesByTicker || {}).filter(
    (ticker) =>
      Array.isArray(seriesByTicker[ticker]) && seriesByTicker[ticker].length > 0,
  )

  if (tickers.length === 0) {
    return (
      <div className="flex h-80 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-sm text-slate-500">
        No comparison data available.
      </div>
    )
  }

  const width = 1000
  const height = 320
  const padding = 48
  const palette = ['#2563eb', '#16a34a', '#9333ea', '#ea580c', '#dc2626', '#0891b2']

  const firstSeries = seriesByTicker[tickers[0]]
  const allValues = []
  tickers.forEach((ticker) => {
    seriesByTicker[ticker].forEach((point) => {
      allValues.push(Number(point.normalized ?? 0))
    })
  })

  const minValue = Math.min(...allValues)
  const maxValue = Math.max(...allValues)
  const valueRange = maxValue - minValue || 1

  const xFor = (idx) => {
    if (firstSeries.length === 1) return padding
    return padding + (idx / (firstSeries.length - 1)) * (width - padding * 2)
  }

  const yFor = (value) => {
    return height - padding - ((value - minValue) / valueRange) * (height - padding * 2)
  }

  const ticks = 4
  const yTicks = Array.from({ length: ticks + 1 }, (_, i) => {
    const value = minValue + ((maxValue - minValue) * i) / ticks
    return { value, y: yFor(value) }
  })

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-4">
        <div className="text-sm font-medium text-slate-500">
          Normalized Comparison
        </div>
        <div className="text-lg font-semibold text-slate-900">
          All series start at 100
        </div>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-4 text-xs text-slate-600">
        {tickers.map((ticker, idx) => (
          <div key={ticker} className="flex items-center gap-2">
            <span
              className="inline-block h-0.5 w-6"
              style={{ backgroundColor: palette[idx % palette.length] }}
            />
            {displayTickerLabel(ticker)}
          </div>
        ))}
      </div>

      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="h-80 w-full min-w-[800px]"
        >
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
                {tick.value.toFixed(1)}
              </text>
            </g>
          ))}

          {tickers.map((ticker, idx) => {
            const series = seriesByTicker[ticker]
            const path = series
              .map(
                (point, pointIdx) =>
                  `${pointIdx === 0 ? 'M' : 'L'} ${xFor(pointIdx)} ${yFor(Number(point.normalized ?? 0))}`,
              )
              .join(' ')

            return (
              <path
                key={ticker}
                d={path}
                fill="none"
                stroke={palette[idx % palette.length]}
                strokeWidth="3"
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            )
          })}

          <text x={padding} y={height - 10} fontSize="11" fill="#64748b">
            {formatShortDate(firstSeries[0]?.date)}
          </text>
          <text
            x={width - padding}
            y={height - 10}
            textAnchor="end"
            fontSize="11"
            fill="#64748b"
          >
            {formatShortDate(firstSeries[firstSeries.length - 1]?.date)}
          </text>
        </svg>
      </div>
    </div>
  )
}

export default function Performance() {
  const [range, setRange] = useState('3y')
  const [benchmark, setBenchmark] = useState('')
  const [compareInput, setCompareInput] = useState('')
  const [compareTickers, setCompareTickers] = useState([])
  const [selectedAccounts, setSelectedAccounts] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadingCompare, setLoadingCompare] = useState(false)
  const [error, setError] = useState(null)
  const [compareError, setCompareError] = useState(null)
  const [data, setData] = useState(null)
  const [comparisonData, setComparisonData] = useState({})
  const [lastUpdated, setLastUpdated] = useState(null)
  const [managementMode, setManagementMode] = useState('all')
  const performanceRequestIdRef = useRef(0)

  async function loadPerformance(
    nextRange = range,
    nextAccounts = selectedAccounts,
    nextMode = managementMode,
  ) {
    const requestId = performanceRequestIdRef.current + 1
    performanceRequestIdRef.current = requestId
    setLoading(true)
    setError(null)
    try {
      const payload = await fetchPortfolioPerformance(nextRange, nextAccounts, nextMode)
      if (requestId !== performanceRequestIdRef.current) return
      setData(payload)
      setLastUpdated(new Date())
    } catch (err) {
      if (requestId !== performanceRequestIdRef.current) return
      setError(
        err instanceof Error
          ? err.message
          : 'Failed to load portfolio performance',
      )
    } finally {
      if (requestId === performanceRequestIdRef.current) {
        setLoading(false)
      }
    }
  }

  async function loadComparison(
    nextRange = range,
    nextBenchmark = benchmark,
    extraTickers = compareTickers,
  ) {
    const tickers = [
      ...(nextBenchmark ? [nextBenchmark] : []),
      ...extraTickers,
    ]
      .map((ticker) => String(ticker).trim().toUpperCase())
      .filter(Boolean)

    if (tickers.length === 0) {
      setComparisonData({})
      setCompareError(null)
      return
    }

    setLoadingCompare(true)
    setCompareError(null)
    try {
      const payload = await fetchPriceComparison(tickers, nextRange)
      setComparisonData(payload?.series ?? {})
    } catch (err) {
      setCompareError(
        err instanceof Error ? err.message : 'Failed to load comparison data',
      )
      setComparisonData({})
    } finally {
      setLoadingCompare(false)
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      loadPerformance(range, selectedAccounts, managementMode)
    }, 250)

    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range, selectedAccounts, managementMode])

  useEffect(() => {
    loadComparison(range, benchmark, compareTickers)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range, benchmark, compareTickers])

  const latest = data?.latest ?? {}
  const series = data?.series ?? []
  const positions = data?.positions ?? {}
  const accounts = data?.accounts ?? []
  const accountFilterActive = selectedAccounts.length > 0
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

  const handleAddCompareTicker = () => {
    const ticker = String(compareInput).trim().toUpperCase()
    if (!ticker) return
    setCompareTickers((current) =>
      current.includes(ticker) ? current : [...current, ticker],
    )
    setCompareInput('')
  }

  const handleRemoveCompareTicker = (ticker) => {
    setCompareTickers((current) => current.filter((item) => item !== ticker))
  }

  const handleToggleAccount = (account) => {
    setSelectedAccounts((current) => {
      if (current.length === 0) {
        return accounts
          .map((row) => row.account)
          .filter((name) => name && name !== account)
      }

      if (current.includes(account)) {
        const next = current.filter((item) => item !== account)
        return next.length === 0 ? current : next
      }

      return [...current, account].sort()
    })
  }

  const handleSelectAllAccounts = () => {
    setSelectedAccounts([])
  }

  return (
    <div className="mx-auto w-[98vw] px-4 py-8">
      <PageToolbar
        title="Performance"
        description="Portfolio performance over time based on your transaction ledger and historical price data."
        loading={loading}
        onReload={() => loadPerformance(range, selectedAccounts)}
        onUpdatePrices={() => loadPerformance(range, selectedAccounts, managementMode)}
      />

      {error && (
        <div
          role="alert"
          className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
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
        <div className="flex gap-1 rounded-lg border border-slate-200 bg-white p-1">
          {MANAGEMENT_MODES.map((mode) => (
            <button
              key={mode.value}
              type="button"
              onClick={() => setManagementMode(mode.value)}
              className={[
                'rounded-md px-3 py-1.5 text-xs font-medium',
                managementMode === mode.value
                  ? 'bg-slate-900 text-white'
                  : 'text-slate-600 hover:bg-slate-50',
              ].join(' ')}
            >
              {mode.label}
            </button>
          ))}
        </div>
      </div>

      {accounts.length > 0 && (
        <section className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">Accounts</h2>
              <p className="mt-1 text-xs text-slate-500">
                Filter the portfolio graph to accounts you actively manage.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleSelectAllAccounts}
                className="rounded border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                Clear filter
              </button>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {accounts.map((row) => {
              const checked = !accountFilterActive || selectedAccounts.includes(row.account)
              return (
                <label
                  key={row.account}
                  className={[
                    'inline-flex cursor-pointer items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition',
                    checked
                      ? 'border-blue-200 bg-blue-50 text-blue-800'
                      : 'border-slate-200 bg-white text-slate-500',
                  ].join(' ')}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => handleToggleAccount(row.account)}
                    className="h-3.5 w-3.5 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                  />
                  {row.account}
                  <span className="text-slate-400">
                    {Number(row.transaction_count ?? 0).toLocaleString()}
                  </span>
                </label>
              )
            })}
          </div>
        </section>
      )}

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

      <section className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="mb-4">
          <h2 className="text-sm font-semibold text-slate-900">Comparison</h2>
          <p className="mt-1 text-xs text-slate-500">
            Compare benchmarks and selected tickers on a normalized basis.
          </p>
        </div>

        <div className="mb-4 flex flex-wrap items-end gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">
              Benchmark
            </label>
            <select
              value={benchmark}
              onChange={(e) => setBenchmark(e.target.value)}
              className="rounded border border-slate-300 px-3 py-2 text-sm text-slate-900"
            >
              {BENCHMARK_OPTIONS.map((option) => (
                <option key={option || 'none'} value={option}>
                  {option === ''
                    ? 'None'
                    : option === 'ONEQ'
                      ? 'Nasdaq Composite (ONEQ)'
                      : option}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">
              Add Ticker
            </label>
            <input
              type="text"
              value={compareInput}
              onChange={(e) => setCompareInput(e.target.value.toUpperCase())}
              placeholder="e.g. NVDA"
              className="rounded border border-slate-300 px-3 py-2 text-sm text-slate-900"
            />
          </div>

          <button
            type="button"
            onClick={handleAddCompareTicker}
            className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
          >
            Add Compare Ticker
          </button>
        </div>

        {compareTickers.length > 0 && (
          <div className="mb-4 flex flex-wrap gap-2">
            {compareTickers.map((ticker) => (
              <button
                key={ticker}
                type="button"
                onClick={() => handleRemoveCompareTicker(ticker)}
                className="rounded-full border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                {ticker} ×
              </button>
            ))}
          </div>
        )}

        {compareError && (
          <div
            role="alert"
            className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
          >
            {compareError}
          </div>
        )}

        {loadingCompare ? (
          <div className="flex h-80 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-sm text-slate-500">
            Loading comparison…
          </div>
        ) : (
          <ComparisonChart seriesByTicker={comparisonData} />
        )}
      </section>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-200 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-900">
            Latest Position Summaries
          </h2>
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
