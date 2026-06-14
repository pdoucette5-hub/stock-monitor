import { useMemo, useState } from 'react'
import PageToolbar from '../components/PageToolbar'
import ValuationTable, { getValuationColumns } from '../components/ValuationTable'
import { usePortfolioView } from '../hooks/usePortfolioView'
import {
  addPortfolioTicker,
  archiveTicker,
  removeTicker,
  savePortfolioControls,
  savePortfolioShares,
  syncSheetTickers,
} from '../lib/api'

const SUB_TABS = [
  { id: 'portfolio', label: 'Portfolio' },
  { id: 'watchlist', label: 'Watchlist' },
]

const MANAGEMENT_FILTERS = [
  { id: 'active', label: 'Managed + tracked' },
  { id: 'managed', label: 'Managed' },
  { id: 'track', label: 'Track only' },
  { id: 'excluded', label: 'Excluded' },
  { id: 'all', label: 'All' },
]

function sharesForManagementFilter(row, filter) {
  const managed = Number(row.managed_shares || 0)
  const tracked = Number(row.track_shares || 0)
  const excluded = Number(row.excluded_shares || 0)

  if (filter === 'managed') return managed
  if (filter === 'track') return tracked
  if (filter === 'excluded') return excluded
  if (filter === 'active') return managed + tracked
  return Number(row.shares || 0)
}

export default function Holdings() {
  const { view, loading, error, lastUpdated, load } = usePortfolioView()
  const [activeTab, setActiveTab] = useState('portfolio')
  const [showHidden, setShowHidden] = useState(false)
  const [managementFilter, setManagementFilter] = useState('active')
  const [savingTicker, setSavingTicker] = useState(null)
  const [shareDrafts, setShareDrafts] = useState({})
  const [newTicker, setNewTicker] = useState('')
  const [newShares, setNewShares] = useState('')
  const [formError, setFormError] = useState(null)
  const [formMessage, setFormMessage] = useState(null)
  const [syncingTickers, setSyncingTickers] = useState(false)

  const portfolioRows = view?.portfolio ?? []
  const watchlistRows = view?.watchlist ?? []

  const sourceRows = activeTab === 'portfolio' ? portfolioRows : watchlistRows
  const visibleRows = useMemo(() => {
    let rows = showHidden
      ? sourceRows
      : sourceRows.filter((row) => row.show_in_holdings !== false)

    if (activeTab !== 'portfolio') return rows

    return rows
      .map((row) => {
        const filteredShares = sharesForManagementFilter(row, managementFilter)
        const price = Number(row.price)
        return {
          ...row,
          total_shares: row.shares,
          shares: filteredShares,
          market_value: Number.isFinite(price) ? filteredShares * price : null,
        }
      })
      .filter((row) => managementFilter === 'all' || Number(row.shares) > 0)
  }, [activeTab, managementFilter, sourceRows, showHidden])

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

  const saveShares = async (ticker) => {
    const rawValue = shareDrafts[ticker]
    if (rawValue === undefined) return

    const numeric = Number(rawValue)
    if (Number.isNaN(numeric) || numeric < 0) return

    setSavingTicker(ticker)
    setFormError(null)
    setFormMessage(null)
    try {
      await savePortfolioShares(ticker, numeric)
      await load(false)
      setShareDrafts((current) => {
        const next = { ...current }
        delete next[ticker]
        return next
      })
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to save shares')
    } finally {
      setSavingTicker(null)
    }
  }

  const handleAddPortfolio = async (event) => {
    event.preventDefault()
    setFormError(null)
    setFormMessage(null)

    const ticker = String(newTicker).trim().toUpperCase()
    const shares = Number(newShares)

    if (!ticker) {
      setFormError('Ticker is required.')
      return
    }
    if (Number.isNaN(shares) || shares < 0) {
      setFormError('Shares must be a non-negative number.')
      return
    }

    try {
      await addPortfolioTicker(ticker, shares)
      setNewTicker('')
      setNewShares('')
      try {
        await syncSheetTickers()
        setFormMessage(`Added ${ticker} to portfolio and synced Google Sheets.`)
      } catch {
        setFormMessage(`Added ${ticker} to portfolio. Sheet sync did not complete.`)
      }
      await load(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to add portfolio ticker')
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
    const baseColumns = getValuationColumns(activeTab)

    if (activeTab !== 'portfolio') {
      return baseColumns
    }

    const filterLabel = {
      active: 'Managed + Tracked',
      managed: 'Managed',
      track: 'Tracked',
      excluded: 'Excluded',
      all: 'Total',
    }[managementFilter]

    const withEditableShares = baseColumns.map((column) => {
      if (column.key === 'market_value') {
        return {
          ...column,
          label: `${filterLabel} Value`,
        }
      }
      if (column.key !== 'shares') return column

      return {
        ...column,
        label: `${filterLabel} Shares`,
        render: (row) => {
          if (managementFilter !== 'all') {
            return Number(row.shares || 0).toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 4,
            })
          }

          const draftValue = shareDrafts[row.ticker]
          const inputValue = draftValue ?? row.total_shares ?? row.shares ?? ''

          return (
            <input
              type="number"
              step="0.01"
              min="0"
              value={inputValue}
              disabled={savingTicker === row.ticker}
              onChange={(e) =>
                setShareDrafts((current) => ({
                  ...current,
                  [row.ticker]: e.target.value,
                }))
              }
              onBlur={() => saveShares(row.ticker)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.currentTarget.blur()
                }
              }}
              className="w-24 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
              title="Edit shares and click away to save"
            />
          )
        },
      }
    })

    const managementColumn = {
      key: 'management_mode',
      label: 'Management',
      render: (row) => {
        const label = {
          managed: 'Managed',
          track: 'Track only',
          excluded: 'Excluded',
          mixed: 'Mixed',
        }[row.management_mode] ?? 'Managed'
        return (
          <div>
            <div className="font-medium text-slate-800">{label}</div>
            <div className="text-xs text-slate-500">
              {Number(row.managed_shares || 0).toLocaleString(undefined, {
                maximumFractionDigits: 2,
              })}{' '}
              managed
            </div>
          </div>
        )
      },
    }

    return [
      ...withEditableShares.slice(0, 2),
      managementColumn,
      ...withEditableShares.slice(2),
      {
        key: 'row_actions',
        label: 'Actions',
        render: (row) => (
          <div className="flex items-center gap-2">
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
  }, [activeTab, managementFilter, shareDrafts, savingTicker])

  return (
    <div className="mx-auto w-[98vw] px-4 py-8">
      <PageToolbar
        title="Holdings"
        description="Valuation view for positions you choose to show. Hiding a ticker does not remove it from your config or action calculations."
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

      {activeTab === 'portfolio' && (
        <form
          onSubmit={handleAddPortfolio}
          className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
        >
          <div className="mb-3">
            <h2 className="text-sm font-semibold text-slate-900">Add portfolio holding</h2>
            <p className="mt-1 text-xs text-slate-500">
              Add a new stock directly from the UI.
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
                placeholder="e.g. CRM"
                className="w-32 rounded border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-600">
                Shares
              </label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={newShares}
                onChange={(e) => setNewShares(e.target.value)}
                placeholder="e.g. 25"
                className="w-32 rounded border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
              />
            </div>

            <button
              type="submit"
              className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
            >
              Add to portfolio
            </button>
          </div>
        </form>
      )}

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1 rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
          {SUB_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={[
                'rounded-md px-4 py-2 text-sm font-medium transition',
                activeTab === tab.id
                  ? 'bg-slate-900 text-white'
                  : 'text-slate-600 hover:bg-slate-50',
              ].join(' ')}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {activeTab === 'portfolio' && (
            <div className="flex gap-1 rounded-lg border border-slate-200 bg-white p-1">
              {MANAGEMENT_FILTERS.map((filter) => (
                <button
                  key={filter.id}
                  type="button"
                  onClick={() => setManagementFilter(filter.id)}
                  className={[
                    'rounded-md px-3 py-1.5 text-xs font-medium',
                    managementFilter === filter.id
                      ? 'bg-slate-900 text-white'
                      : 'text-slate-600 hover:bg-slate-50',
                  ].join(' ')}
                >
                  {filter.label}
                </button>
              ))}
            </div>
          )}
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
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <ValuationTable
          rows={visibleRows}
          columns={columns}
          loading={loading}
          emptyMessage={
            activeTab === 'portfolio'
              ? 'No visible portfolio positions. Enable Show on hidden rows or add positions in the form above.'
              : 'No visible watchlist names.'
          }
          lastUpdated={lastUpdated}
          leadingColumn={{
            header: 'Show',
            render: (row) => (
              <input
                type="checkbox"
                checked={row.show_in_holdings !== false}
                disabled={savingTicker === row.ticker}
                onChange={(e) => toggleVisibility(row.ticker, e.target.checked)}
                title="Show this ticker in the holdings table"
                className="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-500"
              />
            ),
          }}
        />
      </div>
    </div>
  )
}
