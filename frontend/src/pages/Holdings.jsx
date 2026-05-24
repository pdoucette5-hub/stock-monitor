import { useMemo, useState } from 'react'
import MetricCard from '../components/MetricCard'
import PageToolbar from '../components/PageToolbar'
import ValuationTable, { getValuationColumns } from '../components/ValuationTable'
import { usePortfolioView } from '../hooks/usePortfolioView'
import { savePortfolioControls, savePortfolioShares } from '../lib/api'

const SUB_TABS = [
  { id: 'portfolio', label: 'Portfolio' },
  { id: 'watchlist', label: 'Watchlist' },
]

export default function Holdings() {
  const { view, loading, error, lastUpdated, load } = usePortfolioView()
  const [activeTab, setActiveTab] = useState('portfolio')
  const [showHidden, setShowHidden] = useState(false)
  const [savingTicker, setSavingTicker] = useState(null)
  const [shareDrafts, setShareDrafts] = useState({})

  const metrics = view?.metrics ?? {}
  const portfolioRows = view?.portfolio ?? []
  const watchlistRows = view?.watchlist ?? []

  const sourceRows = activeTab === 'portfolio' ? portfolioRows : watchlistRows
  const visibleRows = useMemo(() => {
    if (showHidden) return sourceRows
    return sourceRows.filter((row) => row.show_in_holdings !== false)
  }, [sourceRows, showHidden])

  const hiddenCount = sourceRows.filter((row) => row.show_in_holdings === false).length

  const toggleVisibility = async (ticker, showInHoldings) => {
    setSavingTicker(ticker)
    try {
      await savePortfolioControls([{ ticker, show_in_holdings: showInHoldings }])
      await load(false)
    } finally {
      setSavingTicker(null)
    }
  }

  const saveShares = async (ticker) => {
    const rawValue = shareDrafts[ticker]
    if (rawValue === undefined) return

    const numeric = Number(rawValue)
    if (Number.isNaN(numeric) || numeric < 0) {
      return
    }

    setSavingTicker(ticker)
    try {
      await savePortfolioShares(ticker, numeric)
      await load(false)
      setShareDrafts((current) => {
        const next = { ...current }
        delete next[ticker]
        return next
      })
    } finally {
      setSavingTicker(null)
    }
  }

  const columns = useMemo(() => {
    const baseColumns = getValuationColumns(activeTab)

    if (activeTab !== 'portfolio') {
      return baseColumns
    }

    return baseColumns.map((column) => {
      if (column.key !== 'shares') return column

      return {
        ...column,
        render: (row) => {
          const draftValue = shareDrafts[row.ticker]
          const inputValue = draftValue ?? row.shares ?? ''

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
  }, [activeTab, shareDrafts, savingTicker])

  return (
    <div className="mx-auto w-[98vw] px-4 py-8">
      <PageToolbar
        title="Holdings"
        description="Valuation view for positions you choose to show. Hiding a ticker does not remove it from your config or action calculations."
        loading={loading}
        onReload={load}
        onUpdatePrices={load}
      />

      {error && (
        <div
          role="alert"
          className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label="Portfolio positions"
          value={metrics.portfolio_positions ?? '—'}
        />
        <MetricCard label="Watchlist names" value={metrics.watchlist_names ?? '—'} />
        <MetricCard
          label="Hidden from view"
          value={hiddenCount}
          hint="Still tracked; toggle Show to reveal"
        />
        <MetricCard
          label="Data issues"
          value={metrics.data_issues ?? '—'}
          hint={
            (metrics.data_issues ?? 0) > 0
              ? 'Tickers with missing or stale market data'
              : undefined
          }
        />
      </div>

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
          emptyMessage={
            activeTab === 'portfolio'
              ? 'No visible portfolio positions. Enable Show on hidden rows or add positions in config/tickers.yaml.'
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