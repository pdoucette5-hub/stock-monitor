import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchChangeLog } from '../lib/api'

function formatDate(value) {
  if (!value) return 'Unknown time'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value

  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return 'blank'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') {
    return Number(value).toLocaleString(undefined, {
      maximumFractionDigits: 4,
    })
  }
  return String(value)
}

function eventTitle(event) {
  const ticker = event.ticker ? `${event.ticker}: ` : ''

  switch (event.type) {
    case 'add_portfolio':
      return `${ticker}Added to portfolio`
    case 'add_watchlist':
      return `${ticker}Added to watchlist`
    case 'archive_ticker':
      return `${ticker}Archived`
    case 'restore_ticker':
      return `${ticker}Restored`
    case 'remove_ticker':
      return `${ticker}Removed`
    case 'update_shares':
      return `${ticker}Shares updated`
    case 'update_controls':
      return `${ticker}Controls updated`
    case 'update_assumptions':
      return `${ticker}Assumptions updated`
    case 'create_transaction':
      return `${ticker}Transaction added`
    case 'update_transaction':
      return `${ticker}Transaction updated`
    case 'delete_transaction':
      return `${ticker}Transaction deleted`
    default:
      return `${ticker}${event.type || 'Change recorded'}`
  }
}

function eventSummary(event) {
  const payload = event.payload || {}
  const changes = Array.isArray(payload.changes) ? payload.changes : []

  if (changes.length > 0) {
    return `${changes.length} field${changes.length === 1 ? '' : 's'} changed`
  }

  if (event.type === 'update_shares' || event.type === 'add_portfolio') {
    return `Shares: ${formatValue(payload.shares)}`
  }

  if (event.type === 'restore_ticker') {
    return `Restored to ${payload.target_list || 'active list'}`
  }

  if (event.type === 'archive_ticker') {
    return `Previous list: ${payload.previous_list || 'active'}`
  }

  if (event.type?.includes('transaction')) {
    const type = payload.transaction_type ? `${payload.transaction_type} ` : ''
    const shares = payload.shares !== undefined ? `${formatValue(payload.shares)} shares` : ''
    return `${type}${shares}`.trim() || 'Transaction record changed'
  }

  return 'User change recorded'
}

function ChangeRows({ event }) {
  const changes = Array.isArray(event.payload?.changes) ? event.payload.changes : []

  if (changes.length === 0) return null

  return (
    <div className="mt-3 overflow-hidden rounded-lg border border-slate-200">
      <table className="min-w-full divide-y divide-slate-200 text-left text-xs">
        <thead className="bg-slate-50 text-slate-600">
          <tr>
            <th className="px-3 py-2 font-semibold">Field</th>
            <th className="px-3 py-2 font-semibold">Before</th>
            <th className="px-3 py-2 font-semibold">After</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 bg-white">
          {changes.map((change) => (
            <tr key={change.field}>
              <td className="px-3 py-2 font-medium text-slate-700">
                {change.label || change.field}
              </td>
              <td className="px-3 py-2 text-slate-500">{formatValue(change.old)}</td>
              <td className="px-3 py-2 text-slate-900">{formatValue(change.new)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function ChangeLog() {
  const [events, setEvents] = useState([])
  const [tickerFilter, setTickerFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const payload = await fetchChangeLog({
        ticker: tickerFilter,
        limit: 250,
      })
      setEvents(Array.isArray(payload?.events) ? payload.events : [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load change log')
    } finally {
      setLoading(false)
    }
  }, [tickerFilter])

  useEffect(() => {
    load()
  }, [load])

  const eventCountLabel = useMemo(() => {
    if (loading) return 'Loading changes...'
    return `${events.length.toLocaleString()} change${events.length === 1 ? '' : 's'}`
  }, [events.length, loading])

  return (
    <div className="mx-auto max-w-[96rem] px-4 py-8 sm:px-6 lg:px-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Change Log</h1>
          <p className="mt-1 text-sm text-slate-600">
            User edits recorded from holdings, assumptions, transactions, and ticker changes.
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
        >
          Reload
        </button>
      </div>

      <div className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <label className="block text-sm font-medium text-slate-700" htmlFor="ticker-filter">
          Filter by ticker
        </label>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <input
            id="ticker-filter"
            value={tickerFilter}
            onChange={(event) => setTickerFilter(event.target.value.trim().toUpperCase())}
            placeholder="e.g. AMD"
            className="block w-44 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20"
          />
          <span className="text-sm text-slate-500">{eventCountLabel}</span>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      <section className="space-y-3">
        {loading && events.length === 0 ? (
          <div className="rounded-xl border border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-500 shadow-sm">
            Loading change log...
          </div>
        ) : events.length === 0 ? (
          <div className="rounded-xl border border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-500 shadow-sm">
            No changes recorded yet.
          </div>
        ) : (
          events.map((event) => (
            <article
              key={event.id}
              className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-slate-900">
                    {eventTitle(event)}
                  </h2>
                  <p className="mt-1 text-sm text-slate-600">{eventSummary(event)}</p>
                </div>
                <time className="text-xs font-medium text-slate-500">
                  {formatDate(event.timestamp)}
                </time>
              </div>
              <ChangeRows event={event} />
            </article>
          ))
        )}
      </section>
    </div>
  )
}
