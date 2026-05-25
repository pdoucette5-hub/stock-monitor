import { useMemo, useState } from 'react'
import PageToolbar from '../components/PageToolbar'
import ValuationTable, { getValuationColumns } from '../components/ValuationTable'
import { usePortfolioView } from '../hooks/usePortfolioView'
import {
  addPortfolioTicker,
  addWatchlistTicker,
  archiveTicker,
  removeTicker,
  savePortfolioControls,
  syncSheetTickers,
} from '../lib/api'

export default function Watchlist() {
  const { view, loading, error, lastUpdated, load } = usePortfolioView()
  const [showHidden, setShowHidden] = useState(false)
  const [savingTicker, setSavingTicker] = useState(null)
  const [newTicker, setNewTicker] = useState('')
  const [moveShares, setMoveShares] = useState({})
  const [formError, setFormError] = useState(null)
  const [formMessage, setFormMessage] = useState(null)
  const [syncingTickers, setSyncingTickers] = useState(false)

  const watchlistRows = view?.watchlist ?? []

  const visibleRows = useMemo(() => {
    if (showHidden) return watchlistRows
    return watchlistRows.filter((row) => row.show_in_holdings !== false)
  }, [watchlistRows, showHidden])

  const toggleVisibility = async (ticker, showInHoldings) => {
    setSavingTicker(ticker)
    setFormError(null)
    setFormMessage(null)
    try {
      await savePortfolioControls([{ ticker, show_in_holdings: showInHoldings }])
      await load(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to update visibility')
    } finally {
      setSavingTicker(null)
    }
  }

  const handleAddWatchlist = async (event) => {
    event.preventDefault()
    setFormError(null)
    setFormMessage(null)

    const ticker = String(newTicker).trim().toUpperCase()
    if (!ticker) {
      setFormError('Ticker is required.')
      return
    }

    try {
      await addWatchlistTicker(ticker)
      setNewTicker('')
      setFormMessage(`Added ${ticker} to watchlist.`)
      await load(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to add watchlist ticker')
    }
  }

  const handleMoveToPortfolio = async (ticker) => {
    setSavingTicker(ticker)
    setFormError(null)
    setFormMessage(null)

    const rawShares = moveShares[ticker]
    const shares = rawShares === undefined || rawShares === '' ? 0 : Number(rawShares)

    if (Number.isNaN(shares) || shares < 0) {
      setFormError(`Shares for ${ticker} must be a non-negative number.`)
      setSavingTicker(null)
      return
    }

    try {
      await addPortfolioTicker(ticker, shares)
      setFormMessage(`Moved ${ticker} to portfolio.`)
      setMoveShares((current) => {
        const next = { ...current }
        delete next[ticker]
        return next
      })
      await load(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to move ticker to portfolio')
    } finally {
      setSavingTicker(null)
    }
  }

  const handleArchive = async (ticker) => {
    setSavingTicker(ticker)
    setFormError(null)
    setFormMessage(null)
    try {
      await archiveTicker(ticker)
      setFormMessage(`Archived ${ticker}.`)
      await load(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to archive ticker')
    } finally {
      setSavingTicker(null)
    }
  }

  const handleRemove = async (ticker) => {
    const confirmed = window.confirm(`Remove ${ticker} from tracking?`)
    if (!confirmed) return

    setSavingTicker(ticker)
    setFormError(null)
    setFormMessage(null)
    try {
      await removeTicker(ticker)
      setFormMessage(`Removed ${ticker} from tracking.`)
      await load(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to remove ticker')
    } finally {
      setSavingTicker(null)
    }
  }

  const handleSyncTickers = async () => {
    setSyncingTickers(true)
    setFormError(null)
    setFormMessage(null)
    try {
      const result = await syncSheetTickers()
      setFormMessage(`Synced ${result.ticker_count ?? 0} tickers to Google Sheets.`)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to sync tickers')
    } finally {
      setSyncingTickers(false)
    }
  }

  const columns = useMemo(() => {
    const baseColumns = getValuationColumns('watchlist')

    return [
      ...baseColumns,
      {
        key: 'move_shares',
        label: 'Portfolio Shares',
        render: (row) => (
          <input
            type="number"
            step="0.01"
            min="0"
            value={moveShares[row.ticker] ?? ''}
            disabled={savingTicker === row.ticker}
            onChange={(e) =>
              setMoveShares((current) => ({
                ...current,
                [row.ticker]: e.target.value,
              }))
            }
            placeholder="0.00"
            className="w-24 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
            title="Shares to use if moving this name into the portfolio"
          />
        ),
      },
      {
        key: 'row_actions',
        label: 'Actions',
        render: (row) => (
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={savingTicker === row.ticker}
              onClick={() => handleMoveToPortfolio(row.ticker)}
              className="rounded border border-blue-300 px-2 py-1 text-xs font-medium text-blue-700 hover:bg-blue-50 disabled:opacity-60"
            >
              Move to portfolio
            </button>
            <button
              type="button"
              disabled={savingTicker === row.ticker}
              onClick={() => handleArchive(row.ticker)}
              className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
            >
              Archive
            </button>
            <button
              type="button"
              disabled={savingTicker === row.ticker}
              onClick={() => handleRemove(row.ticker)}
              className="rounded border border-red-300 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-60"
            >
              Remove
            </button>
          </div>
        ),
      },
    ]
  }, [moveShares, savingTicker])

  return (
    <div className="mx-auto w-[98vw] px-4 py-8">
      <PageToolbar
        title="Watchlist"
        description="Track names you are watching without including them in portfolio holdings or redistribution calculations."
        loading={loading}
        onReload={load}
        onUpdatePrices={load}
        onSyncTickers={handleSyncTickers}
        syncingTickers={syncingTickers}
      />

      {error && (
        <div
          role="alert"
          className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      {formError && (
        <div
          role="alert"
          className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {formError}
        </div>
      )}

      {formMessage && (
        <div
          role="status"
          className="mb-6 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900"
        >
          {formMessage}
        </div>
      )}

      <form
        onSubmit={handleAddWatchlist}
        className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
      >
        <div className="mb-3">
          <h2 className="text-sm font-semibold text-slate-900">Add watchlist ticker</h2>
          <p className="mt-1 text-xs text-slate-500">
            Add a new stock to the watchlist directly from the UI.
          </p>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">
              Ticker
            </label>
            <input
              type="text"
              value={newTicker}
              onChange={(e) => setNewTicker(e.target.value.toUpperCase())}
              placeholder="e.g. TSM"
              className="w-32 rounded border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
            />
          </div>

          <button
            type="submit"
            className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
          >
            Add to watchlist
          </button>
        </div>
      </form>

      <div className="mb-4 flex flex-wrap items-center justify-end gap-3">
        <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={showHidden}
            onChange={(e) => setShowHidden(e.target.checked)}
            className="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-500"
          />
          Show hidden tickers
        </label>
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <ValuationTable
          rows={visibleRows}
          columns={columns}
          loading={loading}
          emptyMessage="No visible watchlist names."
          lastUpdated={lastUpdated}
          leadingColumn={{
            header: 'Show',
            render: (row) => (
              <input
                type="checkbox"
                checked={row.show_in_holdings !== false}
                disabled={savingTicker === row.ticker}
                onChange={(e) => toggleVisibility(row.ticker, e.target.checked)}
                title="Show this ticker in the watchlist table"
                className="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-500"
              />
            ),
          }}
        />
      </div>
    </div>
  )
}
