import { useEffect, useMemo, useState } from 'react'
import { fetchCompensation } from '../lib/api'

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

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return `${(Number(value) * 100).toFixed(2)}%`
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function MetricCard({ label, value, detail }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className="mt-2 text-2xl font-semibold text-slate-900">{value}</div>
      {detail && <div className="mt-1 text-xs text-slate-500">{detail}</div>}
    </div>
  )
}

export default function Compensation() {
  const [range, setRange] = useState('1y')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const payload = await fetchCompensation(range, 'SPY', 0.33)
        if (!cancelled) setData(payload)
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load Matthew tracker')
          setData(null)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [range])

  const positiveExcess = Number(data?.payout_base ?? 0) > 0
  const benchmarkAsOf =
    data?.benchmark_as_of && data?.benchmark_as_of !== data?.end_date
      ? `, benchmark as of ${formatDate(data.benchmark_as_of)}`
      : ''
  const formula = useMemo(() => {
    if (!data) return 'Excess gain above S&P 500 × 33%'
    return `${formatMoney(data.payout_base)} × ${formatPercent(data.share_pct)}`
  }, [data])

  return (
    <div className="mx-auto w-[98vw] px-4 py-8">
      <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Matthew
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-600">
            Tracks Matthew&apos;s agreed payout: 33% of the two Roth and two Rollover accounts&apos; growth above S&amp;P 500 growth.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {RANGES.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setRange(option)}
              className={[
                'rounded-md px-3 py-2 text-sm font-medium',
                range === option
                  ? 'bg-slate-900 text-white'
                  : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50',
              ].join(' ')}
            >
              {option}
            </button>
          ))}
        </div>
      </header>

      {error && (
        <div className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </div>
      )}

      {loading ? (
        <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500 shadow-sm">
          Loading Matthew tracker...
        </div>
      ) : (
        <>
          <div className="mb-4 grid gap-3 md:grid-cols-4">
            <MetricCard
              label="Payout"
              value={formatMoney(data?.payout)}
              detail={formula}
            />
            <MetricCard
              label="Actual Gain"
              value={formatPercent(data?.portfolio_return)}
              detail={`${formatMoney(data?.invested_capital ?? data?.cost_basis)} capital base to ${formatMoney(data?.actual_terminal_value ?? data?.portfolio_end_value)}`}
            />
            <MetricCard
              label="S&P 500 Gain"
              value={formatPercent(data?.benchmark_return)}
              detail={`${formatMoney(data?.benchmark_gain)} from same capital path`}
            />
            <MetricCard
              label="Excess Gain"
              value={formatMoney(data?.excess_gain)}
              detail={positiveExcess ? 'eligible for payout' : 'no positive excess gain'}
            />
          </div>

          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-900">Calculation</h2>
            <div className="mt-4 grid gap-4 text-sm text-slate-700 md:grid-cols-2">
              <div>
                <div className="font-medium text-slate-900">Measurement Window</div>
                <div className="mt-1">
                  {formatDate(data?.start_date)} to {formatDate(data?.end_date)}
                </div>
              </div>
              <div>
                <div className="font-medium text-slate-900">Actual Gain</div>
                <div className="mt-1">
                  Matthew account value {formatMoney(data?.actual_terminal_value ?? data?.portfolio_end_value)} - capital base {formatMoney(data?.invested_capital ?? data?.cost_basis)} = {formatMoney(data?.actual_gain)}
                </div>
              </div>
              <div>
                <div className="font-medium text-slate-900">S&amp;P 500 Comparison</div>
                <div className="mt-1">
                  Same capital path invested in {data?.benchmark ?? 'SPY'} would be {formatMoney(data?.benchmark_equivalent_value)}{benchmarkAsOf}
                </div>
              </div>
              <div>
                <div className="font-medium text-slate-900">Excess Gain</div>
                <div className="mt-1">
                  Matthew account value {formatMoney(data?.actual_terminal_value ?? data?.portfolio_end_value)} - S&amp;P equivalent {formatMoney(data?.benchmark_equivalent_value)} = {formatMoney(data?.excess_gain)}
                </div>
              </div>
              <div>
                <div className="font-medium text-slate-900">Payout Rule</div>
                <div className="mt-1">
                  33% of positive excess gain: {formatMoney(data?.payout)}
                </div>
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  )
}
