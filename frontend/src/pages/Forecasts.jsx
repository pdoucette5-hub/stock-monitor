import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchForecastScorecard, refreshReportedFundamentals } from '../lib/api'
import { formatCagrDecimal, formatMoney } from '../lib/format'

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `${(Number(value) * 100).toFixed(1)}%`
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function statusClass(status) {
  if (status === 'ahead') return 'bg-emerald-100 text-emerald-800'
  if (status === 'behind') return 'bg-rose-100 text-rose-800'
  if (status === 'tracking') return 'bg-sky-100 text-sky-800'
  return 'bg-slate-100 text-slate-600'
}

function StatusPill({ status }) {
  return (
    <span
      className={[
        'inline-flex rounded-full px-2 py-1 text-xs font-medium capitalize',
        statusClass(status),
      ].join(' ')}
    >
      {status || 'needs actuals'}
    </span>
  )
}

function sourceLabel(value) {
  if (value === 'sec-companyfacts') return 'SEC'
  if (value === 'mixed') return 'SEC + fallback'
  if (value === 'stock-detail') return 'Stock Detail'
  return '—'
}

function savedLabel(row) {
  if (row?.is_current_assumption) return 'Current'
  return formatDate(row?.snapshot_timestamp)
}

function formatRevision(row) {
  const summary = Array.isArray(row?.change_summary) ? row.change_summary : []
  if (summary.length > 0) return summary.join(', ')
  if (row?.revision_type === 'initial') return 'Initial baseline'
  if (row?.revision_type === 'update') return 'Updated baseline'
  return 'Saved baseline'
}

function formatAssumptionChange(previous, current) {
  if (current == null || Number.isNaN(Number(current))) return '—'
  if (previous == null || Number.isNaN(Number(previous))) return formatPercent(current)
  const delta = Number(current) - Number(previous)
  const sign = delta > 0 ? '+' : ''
  return `${formatPercent(previous)} → ${formatPercent(current)} (${sign}${(delta * 100).toFixed(1)} pts)`
}

export default function Forecasts() {
  const [payload, setPayload] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState(null)
  const [message, setMessage] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    setMessage(null)
    try {
      setPayload(await fetchForecastScorecard())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load forecasts')
    } finally {
      setLoading(false)
    }
  }, [])

  const refreshActuals = useCallback(async () => {
    setRefreshing(true)
    setError(null)
    setMessage(null)
    try {
      const result = await refreshReportedFundamentals()
      const refreshed = result?.refreshed?.length ?? 0
      const errors = result?.errors?.length ?? 0
      setMessage(`Refreshed ${refreshed} tickers${errors ? `; ${errors} tickers need review` : ''}.`)
      setPayload(await fetchForecastScorecard())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to refresh online data')
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const rows = useMemo(() => payload?.rows ?? [], [payload])
  const historyRows = useMemo(() => payload?.history ?? [], [payload])

  return (
    <div className="mx-auto w-[98vw] px-4 py-8">
      <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Forecasts
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-600">
            Scorecard for saved Stock Detail assumptions against actual fundamentals and price movement to date.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={load}
            disabled={loading || refreshing}
            className="rounded-md border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            Reload
          </button>
          <button
            type="button"
            onClick={refreshActuals}
            disabled={loading || refreshing}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {refreshing ? 'Refreshing...' : 'Refresh online data'}
          </button>
        </div>
      </header>

      {error && (
        <div
          role="alert"
          className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      {message && (
        <div
          role="status"
          className="mb-6 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900"
        >
          {message}
        </div>
      )}

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Snapshots
          </div>
          <div className="mt-1 text-2xl font-semibold text-slate-900">
            {payload?.snapshot_count ?? 0}
          </div>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Covered Tickers
          </div>
          <div className="mt-1 text-2xl font-semibold text-slate-900">
            {rows.length}
          </div>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Latest View
          </div>
          <div className="mt-1 text-sm font-medium text-slate-900">
            Base case versus actuals to date
          </div>
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-4 py-3 font-semibold text-slate-700">Ticker</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Saved</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Actuals Source</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Base Revenue</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Revenue Read</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Base Earnings</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Earnings Read</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Price Path</th>
                <th className="px-4 py-3 font-semibold text-slate-700">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading && rows.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-4 py-12 text-center text-slate-500">
                    Loading...
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-4 py-12 text-center text-slate-500">
                    No holding assumptions yet. Save Stock Detail assumptions for portfolio holdings to begin tracking.
                  </td>
                </tr>
              ) : (
                rows.map((row) => (
                  <tr key={row.snapshot_id} className="hover:bg-slate-50/80">
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-blue-700">
                      <Link to={`/stock?ticker=${encodeURIComponent(row.ticker)}`}>
                        {row.ticker}
                      </Link>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {savedLabel(row)}
                      <div className="text-xs text-slate-500">
                        {row.is_current_assumption ? 'baseline starts now' : `${row.elapsed_days} days ago`}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {sourceLabel(row.actuals_source)}
                      <div className="text-xs text-slate-500">
                        {row.reported_period_end ? `period ${formatDate(row.reported_period_end)}` : 'manual fallback'}
                      </div>
                      <div className="text-xs text-slate-500">
                        {row.reported_confidence || '—'}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {formatPercent(row.base_revenue_growth_y1)}
                      <div className="text-xs text-slate-500">Y1 assumption</div>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      <StatusPill status={row.revenue_status} />
                      <div className="mt-1 text-xs text-slate-500">
                        actual {formatPercent(row.revenue_actual_growth)} vs expected {formatPercent(row.revenue_expected_growth_to_date)}
                      </div>
                      <div className="text-xs text-slate-500">
                        error {formatPercent(row.revenue_error)}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {formatPercent(row.base_earnings_growth_y1)}
                      <div className="text-xs text-slate-500">Y1 assumption</div>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      <StatusPill status={row.earnings_status} />
                      <div className="mt-1 text-xs text-slate-500">
                        actual {formatPercent(row.earnings_actual_growth)} vs expected {formatPercent(row.earnings_expected_growth_to_date)}
                      </div>
                      <div className="text-xs text-slate-500">
                        error {formatPercent(row.earnings_error)}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {formatPercent(row.price_return)}
                      <div className="text-xs text-slate-500">
                        {formatMoney(row.start_price)} to {formatMoney(row.current_price)}
                      </div>
                      <div className="text-xs text-slate-500">
                        base CAGR {formatCagrDecimal(row.base_cagr_y3_at_snapshot)}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {row.action_at_snapshot || '—'}
                      <div className="text-xs text-slate-500">
                        now {row.current_action || '—'}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <section className="mt-8">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">
              Assumption Revision History
            </h2>
            <p className="mt-1 max-w-3xl text-sm text-slate-600">
              Saved baselines for portfolio holdings. Each revision keeps its own starting actuals, price, CAGR, and action so later results can be judged against what you believed then.
            </p>
          </div>
          <div className="text-sm text-slate-500">
            {historyRows.length.toLocaleString()} saved baseline{historyRows.length === 1 ? '' : 's'}
          </div>
        </div>

        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-3 font-semibold text-slate-700">Ticker</th>
                  <th className="px-4 py-3 font-semibold text-slate-700">Baseline</th>
                  <th className="px-4 py-3 font-semibold text-slate-700">Revision</th>
                  <th className="px-4 py-3 font-semibold text-slate-700">Revenue Assumption</th>
                  <th className="px-4 py-3 font-semibold text-slate-700">Earnings Assumption</th>
                  <th className="px-4 py-3 font-semibold text-slate-700">Actual Read</th>
                  <th className="px-4 py-3 font-semibold text-slate-700">Price Path</th>
                  <th className="px-4 py-3 font-semibold text-slate-700">Saved View</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {historyRows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-10 text-center text-slate-500">
                      No saved assumption revisions yet. Save a holding in Stock Detail to create the first baseline.
                    </td>
                  </tr>
                ) : (
                  historyRows.map((row) => (
                    <tr key={row.snapshot_id} className="hover:bg-slate-50/80">
                      <td className="whitespace-nowrap px-4 py-3 font-medium text-blue-700">
                        <Link to={`/stock?ticker=${encodeURIComponent(row.ticker)}`}>
                          {row.ticker}
                        </Link>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                        {formatDate(row.snapshot_timestamp)}
                        <div className="text-xs text-slate-500">
                          {row.elapsed_days} days old
                        </div>
                      </td>
                      <td className="max-w-xs px-4 py-3 text-slate-700">
                        {formatRevision(row)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                        {formatAssumptionChange(
                          row.previous_base_revenue_growth_y1,
                          row.base_revenue_growth_y1,
                        )}
                        <div className="text-xs text-slate-500">base Y1 revenue</div>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                        {formatAssumptionChange(
                          row.previous_base_earnings_growth_y1,
                          row.base_earnings_growth_y1,
                        )}
                        <div className="text-xs text-slate-500">base Y1 earnings</div>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                        <div>
                          Revenue <StatusPill status={row.revenue_status} />
                        </div>
                        <div className="mt-1">
                          Earnings <StatusPill status={row.earnings_status} />
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                        {formatPercent(row.price_return)}
                        <div className="text-xs text-slate-500">
                          {formatMoney(row.start_price)} to {formatMoney(row.current_price)}
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                        {row.action_at_snapshot || '—'}
                        <div className="text-xs text-slate-500">
                          base CAGR {formatCagrDecimal(row.base_cagr_y3_at_snapshot)}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  )
}
