import { useEffect, useMemo, useState } from 'react'
import {
  fetchManagementSettings,
  saveManagementSettings,
  saveManualAllocation,
} from '../lib/api'

const MODES = [
  { value: 'managed', label: 'Managed' },
  { value: 'track', label: 'Track only' },
  { value: 'excluded', label: 'Excluded' },
]

function formatShares(value) {
  return Number(value || 0).toLocaleString(undefined, {
    maximumFractionDigits: 4,
  })
}

export default function Management() {
  const [data, setData] = useState({ accounts: [], positions: [] })
  const [selectedAccount, setSelectedAccount] = useState('')
  const [loading, setLoading] = useState(true)
  const [savingKey, setSavingKey] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [allocationDrafts, setAllocationDrafts] = useState({})

  async function load() {
    setLoading(true)
    setError('')
    try {
      const payload = await fetchManagementSettings()
      setData(payload)
      setSelectedAccount((current) => current || payload.accounts?.[0]?.account || '')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load management settings')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const account = data.accounts?.find((row) => row.account === selectedAccount)
  const positions = useMemo(
    () =>
      (data.positions || []).filter((row) => row.account === selectedAccount),
    [data.positions, selectedAccount],
  )

  async function saveMode(update, key) {
    setSavingKey(key)
    setError('')
    setMessage('')
    try {
      const payload = await saveManagementSettings([update])
      setData(payload)
      setMessage('Management setting saved. Redistribution and actions now use the new scope.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save management setting')
    } finally {
      setSavingKey('')
    }
  }

  function allocationDraftKey(position) {
    return `${position.account}:${position.ticker}`
  }

  function updateAllocationDraft(position, patch) {
    const key = allocationDraftKey(position)
    setAllocationDrafts((current) => ({
      ...current,
      [key]: {
        account: '',
        shares: '',
        mode: 'track',
        ...(current[key] || {}),
        ...patch,
      },
    }))
  }

  async function saveAllocation(position) {
    const draftKey = allocationDraftKey(position)
    const draft = allocationDrafts[draftKey] || {}
    const accountName =
      position.source === 'manual_allocation'
        ? position.account
        : String(draft.account || '').trim()
    const shares =
      draft.shares === undefined || draft.shares === ''
        ? Number(position.shares || 0)
        : Number(draft.shares)
    const mode = draft.mode || position.mode || 'track'
    const key = `allocation:${position.account}:${position.ticker}`

    if (!accountName) {
      setError('Choose or enter an account name.')
      return
    }
    if (!Number.isFinite(shares) || shares < 0) {
      setError('Allocation shares must be a non-negative number.')
      return
    }

    setSavingKey(key)
    setError('')
    setMessage('')
    try {
      const payload = await saveManualAllocation(
        position.ticker,
        accountName,
        shares,
        mode,
      )
      setData(payload)
      setAllocationDrafts((current) => {
        const next = { ...current }
        delete next[draftKey]
        return next
      })
      setSelectedAccount(position.source === 'manual_allocation' ? position.account : accountName)
      setMessage(
        shares > 0
          ? `${formatShares(shares)} ${position.ticker} shares allocated to ${accountName}.`
          : `${position.ticker} allocation removed.`,
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save allocation')
    } finally {
      setSavingKey('')
    }
  }

  return (
    <div className="mx-auto max-w-[96rem] px-4 py-8 sm:px-6 lg:px-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Portfolio Management</h1>
        <p className="mt-1 text-sm text-slate-600">
          Positions backed by imported transactions are managed automatically. Shares without
          transactions start as track only, and you can promote them here.
        </p>
      </div>

      {error && (
        <div className="mb-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </div>
      )}
      {message && (
        <div className="mb-5 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          {message}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <aside className="border-r border-slate-200 pr-4">
          <h2 className="mb-3 text-sm font-semibold text-slate-900">Accounts</h2>
          <div className="space-y-1">
            {(data.accounts || []).map((row) => (
              <button
                key={row.account}
                type="button"
                onClick={() => setSelectedAccount(row.account)}
                className={[
                  'flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm',
                  selectedAccount === row.account
                    ? 'bg-slate-900 text-white'
                    : 'text-slate-700 hover:bg-slate-100',
                ].join(' ')}
              >
                <span>{row.account}</span>
                <span className={selectedAccount === row.account ? 'text-slate-300' : 'text-slate-400'}>
                  {row.position_count}
                </span>
              </button>
            ))}
          </div>
        </aside>

        <section>
          {loading ? (
            <div className="py-12 text-center text-sm text-slate-500">Loading settings...</div>
          ) : !account ? (
            <div className="py-12 text-center text-sm text-slate-500">
              No transaction accounts are available.
            </div>
          ) : (
            <>
              <div className="mb-5 flex flex-wrap items-end justify-between gap-4 border-b border-slate-200 pb-5">
                <div>
                  <h2 className="text-lg font-semibold text-slate-900">{account.account}</h2>
                  <p className="mt-1 text-sm text-slate-500">
                    {account.default_source === 'automatic'
                      ? 'Automatic default based on whether the positions have transactions.'
                      : 'The saved account default applies unless a ticker has its own override.'}
                  </p>
                </div>
                <label className="text-sm font-medium text-slate-700">
                  Account default
                  <select
                    value={account.default_mode}
                    disabled={savingKey === `account:${account.account}`}
                    onChange={(event) =>
                      saveMode(
                        { account: account.account, mode: event.target.value },
                        `account:${account.account}`,
                      )
                    }
                    className="ml-3 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
                  >
                    {MODES.map((mode) => (
                      <option key={mode.value} value={mode.value}>{mode.label}</option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="overflow-x-auto border-y border-slate-200">
                <datalist id="allocation-account-options">
                  {(data.accounts || [])
                    .filter((row) => row.account !== 'Unassigned')
                    .map((row) => (
                      <option key={row.account} value={row.account} />
                    ))}
                </datalist>
                <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
                  <thead className="bg-slate-50 text-slate-600">
                    <tr>
                      <th className="px-4 py-3 font-semibold">Ticker</th>
                      <th className="px-4 py-3 font-semibold">Current shares</th>
                      <th className="px-4 py-3 font-semibold">Source</th>
                      <th className="px-4 py-3 font-semibold">Management mode</th>
                      <th className="px-4 py-3 font-semibold">Account allocation</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 bg-white">
                    {positions.map((position) => {
                      const key = `${position.account}:${position.ticker}`
                      return (
                        <tr key={key}>
                          <td className="px-4 py-3 font-medium text-slate-900">
                            {position.ticker}
                          </td>
                          <td className="px-4 py-3 tabular-nums text-slate-700">
                            {formatShares(position.shares)}
                          </td>
                          <td className="px-4 py-3 text-slate-500">
                            {position.source === 'configured_residual'
                              ? 'Unallocated manual shares'
                              : position.source === 'manual_allocation'
                                ? 'Manual account allocation'
                                : 'Imported transactions; managed automatically'}
                            {position.mode_source === 'ticker_override' && (
                              <div className="mt-1 text-xs font-medium text-blue-700">
                                Ticker override
                              </div>
                            )}
                          </td>
                          <td className="px-4 py-3">
                            <select
                              value={position.mode}
                              disabled={savingKey === key}
                              onChange={(event) =>
                                saveMode(
                                  {
                                    account: position.account,
                                    ticker: position.ticker,
                                    mode: event.target.value,
                                  },
                                  key,
                                )
                              }
                              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
                            >
                              {MODES.map((mode) => (
                                <option key={mode.value} value={mode.value}>{mode.label}</option>
                              ))}
                            </select>
                          </td>
                          <td className="px-4 py-3">
                            {position.source === 'transactions' ? (
                              <span className="text-xs text-slate-400">
                                Account comes from imported transactions
                              </span>
                            ) : (
                              <div className="flex min-w-[28rem] items-center gap-2">
                                {position.source === 'configured_residual' ? (
                                  <>
                                    <input
                                      type="text"
                                      list="allocation-account-options"
                                      value={
                                        allocationDrafts[allocationDraftKey(position)]?.account
                                          ?? ''
                                      }
                                      onChange={(event) =>
                                        updateAllocationDraft(position, {
                                          account: event.target.value,
                                        })
                                      }
                                      placeholder="Account"
                                      className="w-40 rounded-md border border-slate-300 px-3 py-2 text-sm"
                                    />
                                  </>
                                ) : (
                                  <span className="w-40 truncate text-sm text-slate-700">
                                    {position.account}
                                  </span>
                                )}
                                <input
                                  type="number"
                                  min="0"
                                  max={
                                    position.source === 'configured_residual'
                                      ? position.shares
                                      : undefined
                                  }
                                  step="0.0001"
                                  value={
                                    allocationDrafts[allocationDraftKey(position)]?.shares
                                      ?? String(position.shares)
                                  }
                                  onChange={(event) =>
                                    updateAllocationDraft(position, {
                                      shares: event.target.value,
                                    })
                                  }
                                  className="w-28 rounded-md border border-slate-300 px-3 py-2 text-sm tabular-nums"
                                />
                                <select
                                  value={
                                    allocationDrafts[allocationDraftKey(position)]?.mode
                                      ?? position.mode
                                  }
                                  onChange={(event) =>
                                    updateAllocationDraft(position, {
                                      mode: event.target.value,
                                    })
                                  }
                                  className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                                >
                                  {MODES.map((mode) => (
                                    <option key={mode.value} value={mode.value}>
                                      {mode.label}
                                    </option>
                                  ))}
                                </select>
                                <button
                                  type="button"
                                  disabled={savingKey === `allocation:${position.account}:${position.ticker}`}
                                  onClick={() => saveAllocation(position)}
                                  className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
                                >
                                  {position.source === 'configured_residual' ? 'Allocate' : 'Save'}
                                </button>
                              </div>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
