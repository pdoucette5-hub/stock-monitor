import React, { useEffect, useMemo, useState } from 'react'
import { savePortfolioControls } from '../lib/api'
import { usePortfolioView } from '../hooks/usePortfolioView'

export default function Redistribution() {
  const {
    view,
    loading,
    error: loadError,
    lastUpdated,
    load,
  } = usePortfolioView()
  const [rows, setRows] = useState([])
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  useEffect(() => {
    const portfolioRows = view?.portfolio ?? []

    const normalized = portfolioRows.map((row) => {
      const sharesOwned = Number(row.shares ?? 0)
      const eligibleShares = Number(
        row.eligible_redistribution_shares ?? row.shares ?? 0,
      )
      const locked = Math.max(sharesOwned - eligibleShares, 0)

      return {
        ticker: row.ticker,
        include: row.include_in_redistribution !== false,
        sharesOwned,
        managedShares: Number(row.managed_shares ?? sharesOwned),
        eligibleShares,
        locked,
        cagr: row.weighted_cagr_y3,
        action: row.action,
        confidence: row.confidence,
      }
    })

    setRows(normalized)
  }, [view])

  function updateRow(ticker, patch) {
    setRows((current) =>
      current.map((row) => {
        if (row.ticker !== ticker) return row
        const next = { ...row, ...patch }

        const sharesOwned = Number(next.sharesOwned ?? 0)
        const managedShares = Number(next.managedShares ?? sharesOwned)
        const eligibleShares = Math.min(
          Math.max(Number(next.eligibleShares ?? 0), 0),
          managedShares,
        )

        return {
          ...next,
          eligibleShares,
          locked: Math.max(sharesOwned - eligibleShares, 0),
        }
      }),
    )
  }

  const handleExcludeAll = () => {
    setRows((current) =>
      current.map((row) => ({
        ...row,
        include: false,
      })),
    )
  }

  const handleIncludeAll = () => {
    setRows((current) =>
      current.map((row) => ({
        ...row,
        include: true,
      })),
    )
  }

  const handleSetEligibleOwned = () => {
    setRows((current) =>
      current.map((row) => ({
        ...row,
        eligibleShares: row.managedShares,
        locked: Math.max(row.sharesOwned - row.managedShares, 0),
      })),
    )
  }

  const handleToggleRow = (ticker) => {
    const row = rows.find((r) => r.ticker === ticker)
    if (!row) return
    updateRow(ticker, { include: !row.include })
  }

  const handleEligibleChange = (ticker, value) => {
    updateRow(ticker, { eligibleShares: value === '' ? 0 : Number(value) })
  }

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const updates = rows.map((row) => ({
        ticker: row.ticker,
        include_in_redistribution: row.include,
        eligible_redistribution_shares: Number(row.eligibleShares ?? 0),
      }))

      await savePortfolioControls(updates)
      await load(true)
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save redistribution settings')
    } finally {
      setSaving(false)
    }
  }

  const includedCount = useMemo(
    () => rows.filter((r) => r.include).length,
    [rows],
  )

  const getActionBadge = (action) => {
    if (action === 'Strong Buy') return 'bg-emerald-100 text-emerald-800 border-emerald-200'
    if (action === 'Buy') return 'bg-green-50 text-green-700 border-green-200'
    if (action === 'Speculative Buy') return 'bg-blue-50 text-blue-700 border-blue-200'
    if (action === 'Consider Trim') return 'bg-red-50 text-red-700 border-red-200'
    if (action === 'High Risk / Review') return 'bg-amber-50 text-amber-700 border-amber-200'
    return 'bg-slate-100 text-slate-700 border-slate-200'
  }

  const getConfidenceBadge = (confidence) => {
    if (confidence === 'OK') return 'bg-emerald-50 text-emerald-700 border-emerald-200'
    if (confidence === 'Review Assumptions') return 'bg-amber-50 text-amber-700 border-amber-200'
    return 'bg-slate-100 text-slate-700 border-slate-200'
  }

  const formatNumber = (value) => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
    return Number(value).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  }

  const formatCagr = (value) => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
    return `${(Number(value) * 100).toFixed(2)}%`
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 pb-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">
            Redistribution participation
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            {includedCount} of {rows.length} holdings included in action calculations.
          </p>
          {lastUpdated && !loading && (
            <p className="mt-1 text-xs text-slate-400">
              Last updated {lastUpdated.toLocaleTimeString()}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={handleExcludeAll}
            type="button"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
          >
            Exclude all
          </button>
          <button
            onClick={handleIncludeAll}
            type="button"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
          >
            Include all
          </button>
          <button
            onClick={handleSetEligibleOwned}
            type="button"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
          >
            Set eligible = owned
          </button>
          <button
            onClick={handleSave}
            type="button"
            disabled={saving}
            className="ml-2 rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? 'Saving…' : 'Save participation'}
          </button>
        </div>
      </div>

      {(loadError || saveError) && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {loadError || saveError}
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-600">
            <thead className="border-b border-slate-200 bg-white text-xs font-semibold text-slate-500">
              <tr>
                <th className="px-6 py-4">Include</th>
                <th className="px-6 py-4">Ticker</th>
                <th className="px-6 py-4">Shares owned</th>
                <th className="px-6 py-4">Managed shares</th>
                <th className="px-6 py-4">Eligible shares</th>
                <th className="px-6 py-4">Locked</th>
                <th className="px-6 py-4">Weighted CAGR</th>
                <th className="px-6 py-4">Valuation action</th>
                <th className="px-6 py-4">Confidence</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr>
                  <td colSpan={9} className="px-6 py-10 text-center text-slate-500">
                    Loading…
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-6 py-10 text-center text-slate-500">
                    No portfolio rows available.
                  </td>
                </tr>
              ) : (
                rows.map((row) => (
                  <tr
                    key={row.ticker}
                    className="transition-colors hover:bg-slate-50/50"
                  >
                    <td className="px-6 py-3">
                      <input
                        type="checkbox"
                        checked={row.include}
                        onChange={() => handleToggleRow(row.ticker)}
                        className="h-4 w-4 cursor-pointer rounded border-slate-300 bg-slate-100 text-blue-600 focus:ring-blue-500"
                      />
                    </td>
                    <td className="px-6 py-3 font-semibold text-slate-900">
                      {row.ticker}
                    </td>
                    <td className="px-6 py-3 tabular-nums">
                      {formatNumber(row.sharesOwned)}
                    </td>
                    <td className="px-6 py-3 tabular-nums font-medium text-slate-800">
                      {formatNumber(row.managedShares)}
                    </td>
                    <td className="px-6 py-3">
                      <input
                        type="number"
                        min="0"
                        max={row.managedShares}
                        step="0.01"
                        value={row.eligibleShares}
                        onChange={(e) =>
                          handleEligibleChange(row.ticker, e.target.value)
                        }
                        className="w-28 rounded border border-slate-200 px-2 py-1 text-sm tabular-nums text-slate-700 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                      />
                    </td>
                    <td className="px-6 py-3 tabular-nums">
                      {formatNumber(row.locked)}
                    </td>
                    <td className="px-6 py-3 tabular-nums">
                      {formatCagr(row.cagr)}
                    </td>
                    <td className="px-6 py-3">
                      <span
                        className={`rounded-full border px-2.5 py-1 text-xs font-medium ${getActionBadge(row.action)}`}
                      >
                        {row.action ?? '—'}
                      </span>
                    </td>
                    <td className="px-6 py-3">
                      <span
                        className={`rounded-full border px-2.5 py-1 text-xs font-medium ${getConfidenceBadge(row.confidence)}`}
                      >
                        {row.confidence ?? '—'}
                      </span>
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
