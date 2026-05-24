import { useEffect, useMemo, useState } from 'react'
import PageToolbar from '../components/PageToolbar'
import { fetchTickerRegistry, removeTicker, restoreTicker } from '../lib/api'

export default function Archived() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [savingTicker, setSavingTicker] = useState(null)
  const [error, setError] = useState(null)
  const [message, setMessage] = useState(null)
  const [restoreTargets, setRestoreTargets] = useState({})

  useEffect(() => {
    loadArchived()
  }, [])

  async function loadArchived() {
    setLoading(true)
    setError(null)
    try {
      const payload = await fetchTickerRegistry()
      const tickersMap = payload?.overrides?.tickers ?? {}

      const archivedRows = Object.entries(tickersMap)
        .filter(([, value]) => value?.archived === true && value?.removed !== true)
        .map(([ticker, value]) => ({
          ticker,
          priorList: value?.list ?? 'watchlist',
          shares: value?.shares ?? null,
        }))
        .sort((a, b) => a.ticker.localeCompare(b.ticker))

      setRows(archivedRows)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load archived tickers')
    } finally {
      setLoading(false)
    }
  }

  const archivedCount = useMemo(() => rows.length, [rows])

  async function handleRestore(ticker) {
    const targetList = restoreTargets[ticker] || 'watchlist'
    setSavingTicker(ticker)
    setError(null)
    setMessage(null)
    try {
      await restoreTicker(ticker, targetList)
      setMessage(`Restored ${ticker} to ${targetList}.`)
      await loadArchived()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to restore ticker')
    } finally {
      setSavingTicker(null)
    }
  }

  async function handleRemove(ticker) {
    const confirmed = window.confirm(`Permanently remove ${ticker} from tracking?`)
    if (!confirmed) return

    setSavingTicker(ticker)
    setError(null)
    setMessage(null)
    try {
      await removeTicker(ticker)
      setMessage(`Removed ${ticker} permanently.`)
      await loadArchived()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to remove ticker')
    } finally {
      setSavingTicker(null)
    }
  }

  return (
    <div className="mx-auto w-[98vw] px-4 py-8">
      <PageToolbar
        title="Archived"
        description="Archived tickers are hidden from active holdings, watchlist, and redistribution calculations, but can be restored at any time."
        loading={loading}
        onReload={loadArchived}
        onUpdatePrices={loadArchived}
      />

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

      <div className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <p className="text-sm font-medium text-slate-900">
          Archived tickers: <span className="font-semibold">{archivedCount}</span>
        </p>
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className="whitespace-nowrap px-4 py-3 font-semibold text-slate-700">
                  Ticker
                </th>
                <th className="whitespace-nowrap px-4 py-3 font-semibold text-slate-700">
                  Prior List
                </th>
                <th className="whitespace-nowrap px-4 py-3 font-semibold text-slate-700">
                  Shares
                </th>
                <th className="whitespace-nowrap px-4 py-3 font-semibold text-slate-700">
                  Restore To
                </th>
                <th className="whitespace-nowrap px-4 py-3 font-semibold text-slate-700">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-slate-500">
                    Loading…
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-slate-500">
                    No archived tickers.
                  </td>
                </tr>
              ) : (
                rows.map((row) => (
                  <tr key={row.ticker} className="hover:bg-slate-50/80">
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-slate-900">
                      {row.ticker}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {row.priorList}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      {row.shares ?? '—'}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                      <select
                        value={restoreTargets[row.ticker] ?? row.priorList}
                        disabled={savingTicker === row.ticker}
                        onChange={(e) =>
                          setRestoreTargets((current) => ({
                            ...current,
                            [row.ticker]: e.target.value,
                          }))
                        }
                        className="rounded border border-slate-300 px-2 py-1 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
                      >
                        <option value="portfolio">portfolio</option>
                        <option value="watchlist">watchlist</option>
                      </select>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3">
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          disabled={savingTicker === row.ticker}
                          onClick={() => handleRestore(row.ticker)}
                          className="rounded border border-blue-300 px-2 py-1 text-xs font-medium text-blue-700 hover:bg-blue-50 disabled:opacity-60"
                        >
                          Restore
                        </button>
                        <button
                          type="button"
                          disabled={savingTicker === row.ticker}
                          onClick={() => handleRemove(row.ticker)}
                          className="rounded border border-red-300 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-60"
                        >
                          Remove permanently
                        </button>
                      </div>
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