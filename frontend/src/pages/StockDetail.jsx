import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  createTransaction,
  deleteTransaction,
  fetchPortfolioScenarios,
  fetchPositionSummary,
  fetchStockScenario,
  fetchTickersConfig,
  fetchTransactions,
  saveStockScenario,
  updateTransaction,
} from '../lib/api'
import {
  apiToForm,
  defaultFormState,
  formToApi,
} from '../lib/scenarioForm'

const SCENARIO_BLOCKS = [
  {
    key: 'bull',
    title: 'Bull',
    headerClass:
      'bg-gradient-to-r from-emerald-600 to-emerald-500 text-white',
  },
  {
    key: 'base',
    title: 'Base',
    headerClass: 'bg-gradient-to-r from-blue-600 to-blue-500 text-white',
  },
  {
    key: 'bear',
    title: 'Bear',
    headerClass: 'bg-gradient-to-r from-amber-700 to-orange-600 text-white',
  },
]

const TRANSACTION_TYPE_OPTIONS = [
  'buy',
  'sell',
  'dividend',
  'split',
  'transfer_in',
  'transfer_out',
  'adjustment',
]

function collectTickers(tickersPayload, scenariosPayload) {
  const fromConfig = [
    ...(tickersPayload?.portfolio ?? []).map((p) =>
      String(p.ticker).trim().toUpperCase(),
    ),
    ...(tickersPayload?.watchlist ?? []).map((t) =>
      String(t).trim().toUpperCase(),
    ),
  ]
  const fromScenarios = Object.keys(scenariosPayload?.scenarios ?? {})
  return [...new Set([...fromConfig, ...fromScenarios])].filter(Boolean).sort()
}

const inputClass =
  'mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20'

const labelClass = 'block text-sm font-medium text-slate-700'

function emptyTransactionForm() {
  return {
    id: null,
    date: '',
    type: 'buy',
    shares: '',
    price_per_share: '',
    fees: '0',
    notes: '',
  }
}

function formatMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return Number(value).toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export default function StockDetail() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [tickers, setTickers] = useState([])
  const [selectedTicker, setSelectedTicker] = useState('')
  const [form, setForm] = useState(defaultFormState)
  const [transactions, setTransactions] = useState([])
  const [positionSummary, setPositionSummary] = useState(null)
  const [transactionForm, setTransactionForm] = useState(emptyTransactionForm())
  const [loadingTickers, setLoadingTickers] = useState(true)
  const [loadingForm, setLoadingForm] = useState(false)
  const [loadingTransactions, setLoadingTransactions] = useState(false)
  const [loadingPosition, setLoadingPosition] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savingTransaction, setSavingTransaction] = useState(false)
  const [error, setError] = useState(null)
  const [saveMessage, setSaveMessage] = useState(null)
  const [transactionMessage, setTransactionMessage] = useState(null)
  const [isNewTicker, setIsNewTicker] = useState(false)

  useEffect(() => {
    let cancelled = false

    async function loadTickers() {
      setLoadingTickers(true)
      setError(null)
      try {
        const [tickersPayload, scenariosPayload] = await Promise.all([
          fetchTickersConfig(),
          fetchPortfolioScenarios(),
        ])
        if (cancelled) return

        const list = collectTickers(tickersPayload, scenariosPayload)
        setTickers(list)

        const queryTicker = searchParams.get('ticker')?.trim().toUpperCase()
        const initial =
          queryTicker && list.includes(queryTicker)
            ? queryTicker
            : list[0] ?? ''

        setSelectedTicker(initial)

        if (initial) {
          setSearchParams({ ticker: initial }, { replace: true })
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : 'Failed to load ticker list',
          )
        }
      } finally {
        if (!cancelled) setLoadingTickers(false)
      }
    }

    loadTickers()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const queryTicker = searchParams.get('ticker')?.trim().toUpperCase()
    if (
      queryTicker &&
      tickers.includes(queryTicker) &&
      queryTicker !== selectedTicker
    ) {
      setSelectedTicker(queryTicker)
    }
  }, [searchParams, tickers, selectedTicker])

  const loadScenario = useCallback(async (ticker) => {
    if (!ticker) {
      setForm(defaultFormState())
      return
    }

    setLoadingForm(true)
    setError(null)
    setSaveMessage(null)
    setIsNewTicker(false)

    try {
      const data = await fetchStockScenario(ticker)
      setForm(apiToForm(data.scenario))
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load stock'
      if (message.includes('404') || message.toLowerCase().includes('not found')) {
        setForm(defaultFormState())
        setIsNewTicker(true)
      } else {
        setError(message)
      }
    } finally {
      setLoadingForm(false)
    }
  }, [])

  const loadTickerTransactions = useCallback(async (ticker) => {
    if (!ticker) {
      setTransactions([])
      return
    }

    setLoadingTransactions(true)
    try {
      const payload = await fetchTransactions(ticker)
      const rows = Array.isArray(payload?.transactions) ? payload.transactions : []
      const sorted = [...rows].sort((a, b) => String(b.date).localeCompare(String(a.date)))
      setTransactions(sorted)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load transactions')
    } finally {
      setLoadingTransactions(false)
    }
  }, [])

  const loadPosition = useCallback(async (ticker) => {
    if (!ticker) {
      setPositionSummary(null)
      return
    }

    setLoadingPosition(true)
    try {
      const payload = await fetchPositionSummary(ticker)
      setPositionSummary(payload?.summary ?? null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load position summary')
    } finally {
      setLoadingPosition(false)
    }
  }, [])

  useEffect(() => {
    if (!selectedTicker) return
    loadScenario(selectedTicker)
    loadTickerTransactions(selectedTicker)
    loadPosition(selectedTicker)
    setTransactionForm(emptyTransactionForm())
  }, [selectedTicker, loadScenario, loadTickerTransactions, loadPosition])

  const handleTickerChange = (event) => {
    const nextTicker = event.target.value
    setSelectedTicker(nextTicker)
    setSearchParams({ ticker: nextTicker }, { replace: true })
  }

  const updateBase = (field, value) => {
    setForm((prev) => ({ ...prev, [field]: value }))
    setSaveMessage(null)
  }

  const updateScenario = (scenarioKey, field, value) => {
    setForm((prev) => ({
      ...prev,
      [scenarioKey]: { ...prev[scenarioKey], [field]: value },
    }))
    setSaveMessage(null)
  }

  const handleSave = async (event) => {
    event.preventDefault()
    if (!selectedTicker) return

    setSaving(true)
    setError(null)
    setSaveMessage(null)

    try {
      const payload = formToApi(form)
      await saveStockScenario(selectedTicker, payload)
      setIsNewTicker(false)
      setSaveMessage(`Saved assumptions for ${selectedTicker}.`)
      await loadScenario(selectedTicker)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save assumptions')
    } finally {
      setSaving(false)
    }
  }

  const handleTransactionFieldChange = (field, value) => {
    setTransactionForm((prev) => ({
      ...prev,
      [field]: value,
    }))
    setTransactionMessage(null)
  }

  const handleEditTransaction = (tx) => {
    setTransactionForm({
      id: tx.id,
      date: tx.date ?? '',
      type: tx.type ?? 'buy',
      shares: tx.shares ?? '',
      price_per_share: tx.price_per_share ?? '',
      fees: tx.fees ?? '0',
      notes: tx.notes ?? '',
    })
    setTransactionMessage(null)
  }

  const resetTransactionForm = () => {
    setTransactionForm(emptyTransactionForm())
  }

  const handleSaveTransaction = async (event) => {
    event.preventDefault()
    if (!selectedTicker) return

    setSavingTransaction(true)
    setError(null)
    setTransactionMessage(null)

    const payload = {
      date: transactionForm.date,
      type: transactionForm.type,
      shares: Number(transactionForm.shares),
      price_per_share:
        transactionForm.price_per_share === ''
          ? null
          : Number(transactionForm.price_per_share),
      fees:
        transactionForm.fees === ''
          ? 0
          : Number(transactionForm.fees),
      notes: transactionForm.notes ?? '',
    }

    try {
      if (transactionForm.id) {
        await updateTransaction(selectedTicker, transactionForm.id, payload)
        setTransactionMessage('Transaction updated.')
      } else {
        await createTransaction(selectedTicker, payload)
        setTransactionMessage('Transaction added.')
      }

      resetTransactionForm()
      await loadTickerTransactions(selectedTicker)
      await loadPosition(selectedTicker)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save transaction')
    } finally {
      setSavingTransaction(false)
    }
  }

  const handleDeleteTransaction = async (transactionId) => {
    if (!selectedTicker) return
    const confirmed = window.confirm('Delete this transaction?')
    if (!confirmed) return

    setSavingTransaction(true)
    setError(null)
    setTransactionMessage(null)
    try {
      await deleteTransaction(selectedTicker, transactionId)
      setTransactionMessage('Transaction deleted.')
      if (transactionForm.id === transactionId) {
        resetTransactionForm()
      }
      await loadTickerTransactions(selectedTicker)
      await loadPosition(selectedTicker)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete transaction')
    } finally {
      setSavingTransaction(false)
    }
  }

  const disableForm = loadingForm || saving || !selectedTicker

  const pageSubtitle = useMemo(() => {
    if (loadingForm) return 'Loading assumptions…'
    if (isNewTicker) return 'No saved assumptions yet — defaults shown below.'
    return 'Edit assumptions and save to update the cache.'
  }, [loadingForm, isNewTicker])

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
          Stock Detail
        </h1>
        <p className="mt-1 text-sm text-slate-600">{pageSubtitle}</p>
      </header>

      {error && (
        <div
          role="alert"
          className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {error}
        </div>
      )}

      {saveMessage && (
        <div
          role="status"
          className="mb-6 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900"
        >
          {saveMessage}
        </div>
      )}

      {transactionMessage && (
        <div
          role="status"
          className="mb-6 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900"
        >
          {transactionMessage}
        </div>
      )}

      <div className="mb-6 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <label htmlFor="ticker-select" className={labelClass}>
          Ticker
        </label>
        <select
          id="ticker-select"
          value={selectedTicker}
          onChange={handleTickerChange}
          disabled={loadingTickers || tickers.length === 0}
          className={`${inputClass} max-w-xs`}
        >
          {tickers.length === 0 ? (
            <option value="">No tickers available</option>
          ) : (
            tickers.map((ticker) => (
              <option key={ticker} value={ticker}>
                {ticker}
              </option>
            ))
          )}
        </select>
        {selectedTicker && (
          <p className="mt-2 text-sm font-medium text-slate-900">
            Editing: <span className="font-semibold">{selectedTicker}</span>
          </p>
        )}
      </div>

      {!selectedTicker ? (
        <p className="text-sm text-slate-500">Select a ticker to edit assumptions.</p>
      ) : (
        <div className="space-y-6">
          <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="mb-4">
              <h2 className="text-lg font-semibold text-slate-900">Position Summary</h2>
              <p className="mt-1 text-sm text-slate-500">
                Derived from the transaction ledger for this ticker.
              </p>
            </div>

            {loadingPosition ? (
              <p className="text-sm text-slate-500">Loading position summary…</p>
            ) : (
              <>
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                  <SummaryCard
                    label="Current Shares"
                    value={formatNumber(positionSummary?.current_shares, 4)}
                  />
                  <SummaryCard
                    label="Cost Basis"
                    value={formatMoney(positionSummary?.total_cost_basis)}
                  />
                  <SummaryCard
                    label="Avg Cost / Share"
                    value={formatMoney(positionSummary?.average_cost_per_share)}
                  />
                  <SummaryCard
                    label="Realized Gain/Loss"
                    value={formatMoney(positionSummary?.realized_gain_loss)}
                  />
                  <SummaryCard
                    label="Dividend Cash"
                    value={formatMoney(positionSummary?.dividend_cash)}
                  />
                </div>

                {Array.isArray(positionSummary?.warnings) &&
                  positionSummary.warnings.length > 0 && (
                    <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                      <div className="font-medium">Warnings</div>
                      <ul className="mt-2 list-disc space-y-1 pl-5">
                        {positionSummary.warnings.map((warning, idx) => (
                          <li key={`${warning}-${idx}`}>{warning}</li>
                        ))}
                      </ul>
                    </div>
                  )}
              </>
            )}
          </section>

          <form onSubmit={handleSave} className="space-y-6">
            <fieldset disabled={disableForm} className="space-y-6">
              <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
                <h2 className="text-lg font-semibold text-slate-900">
                  Base Inputs
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  Latest-quarter starting point (stored as raw dollars/shares;
                  enter values in billions).
                </p>
                <div className="mt-5 grid gap-5 sm:grid-cols-3">
                  <div>
                    <label htmlFor="latest-q-rev" className={labelClass}>
                      Latest Quarter Revenue ($B)
                    </label>
                    <input
                      id="latest-q-rev"
                      type="number"
                      step="0.01"
                      value={form.latestQuarterRevenueB}
                      onChange={(e) =>
                        updateBase('latestQuarterRevenueB', e.target.value)
                      }
                      className={inputClass}
                      placeholder="e.g. 95.00"
                    />
                  </div>
                  <div>
                    <label htmlFor="latest-q-ni" className={labelClass}>
                      Latest Quarter Net Income ($B)
                    </label>
                    <input
                      id="latest-q-ni"
                      type="number"
                      step="0.01"
                      value={form.latestQuarterNetIncomeB}
                      onChange={(e) =>
                        updateBase('latestQuarterNetIncomeB', e.target.value)
                      }
                      className={inputClass}
                      placeholder="e.g. 24.00"
                    />
                  </div>
                  <div>
                    <label htmlFor="shares-out" className={labelClass}>
                      Shares Outstanding (B)
                    </label>
                    <input
                      id="shares-out"
                      type="number"
                      step="0.01"
                      value={form.sharesOutstandingB}
                      onChange={(e) =>
                        updateBase('sharesOutstandingB', e.target.value)
                      }
                      className={inputClass}
                      placeholder="e.g. 15.00"
                    />
                  </div>
                </div>

                <div className="mt-5">
                  <label htmlFor="notes" className={labelClass}>
                    Notes (optional)
                  </label>
                  <textarea
                    id="notes"
                    rows={2}
                    value={form.notes}
                    onChange={(e) => updateBase('notes', e.target.value)}
                    className={inputClass}
                    placeholder="Assumption notes…"
                  />
                </div>
              </section>

              {SCENARIO_BLOCKS.map(({ key, title, headerClass }) => (
                <ScenarioBlock
                  key={key}
                  scenarioKey={key}
                  title={title}
                  headerClass={headerClass}
                  values={form[key]}
                  onChange={(field, value) => updateScenario(key, field, value)}
                />
              ))}
            </fieldset>

            <div className="flex flex-wrap items-center gap-4">
              <button
                type="submit"
                disabled={disableForm}
                className="inline-flex items-center justify-center rounded-lg bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {saving ? 'Saving…' : 'Save Assumptions'}
              </button>
              {loadingForm && (
                <span className="text-sm text-slate-500">Loading form…</span>
              )}
            </div>
          </form>

          <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="mb-5">
              <h2 className="text-lg font-semibold text-slate-900">Transactions</h2>
              <p className="mt-1 text-sm text-slate-500">
                Record buys, sells, dividends, splits, transfers, and adjustments.
              </p>
            </div>

            <form onSubmit={handleSaveTransaction} className="mb-6 grid gap-4 lg:grid-cols-6">
              <div>
                <label className={labelClass}>Date</label>
                <input
                  type="date"
                  value={transactionForm.date}
                  onChange={(e) => handleTransactionFieldChange('date', e.target.value)}
                  className={inputClass}
                />
              </div>

              <div>
                <label className={labelClass}>Type</label>
                <select
                  value={transactionForm.type}
                  onChange={(e) => handleTransactionFieldChange('type', e.target.value)}
                  className={inputClass}
                >
                  {TRANSACTION_TYPE_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className={labelClass}>Shares</label>
                <input
                  type="number"
                  step="0.0001"
                  value={transactionForm.shares}
                  onChange={(e) => handleTransactionFieldChange('shares', e.target.value)}
                  className={inputClass}
                />
              </div>

              <div>
                <label className={labelClass}>Price / Share</label>
                <input
                  type="number"
                  step="0.0001"
                  value={transactionForm.price_per_share}
                  onChange={(e) =>
                    handleTransactionFieldChange('price_per_share', e.target.value)
                  }
                  className={inputClass}
                />
              </div>

              <div>
                <label className={labelClass}>Fees</label>
                <input
                  type="number"
                  step="0.01"
                  value={transactionForm.fees}
                  onChange={(e) => handleTransactionFieldChange('fees', e.target.value)}
                  className={inputClass}
                />
              </div>

              <div>
                <label className={labelClass}>Notes</label>
                <input
                  type="text"
                  value={transactionForm.notes}
                  onChange={(e) => handleTransactionFieldChange('notes', e.target.value)}
                  className={inputClass}
                />
              </div>

              <div className="lg:col-span-6 flex flex-wrap items-center gap-3">
                <button
                  type="submit"
                  disabled={savingTransaction}
                  className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60"
                >
                  {savingTransaction
                    ? 'Saving…'
                    : transactionForm.id
                      ? 'Update Transaction'
                      : 'Add Transaction'}
                </button>

                {transactionForm.id && (
                  <button
                    type="button"
                    onClick={resetTransactionForm}
                    className="rounded border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  >
                    Cancel Edit
                  </button>
                )}
              </div>
            </form>

            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
                <thead className="bg-slate-50">
                  <tr>
                    <th className="px-4 py-3 font-semibold text-slate-700">Date</th>
                    <th className="px-4 py-3 font-semibold text-slate-700">Type</th>
                    <th className="px-4 py-3 font-semibold text-slate-700">Shares</th>
                    <th className="px-4 py-3 font-semibold text-slate-700">Price / Share</th>
                    <th className="px-4 py-3 font-semibold text-slate-700">Fees</th>
                    <th className="px-4 py-3 font-semibold text-slate-700">Notes</th>
                    <th className="px-4 py-3 font-semibold text-slate-700">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {loadingTransactions ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-10 text-center text-slate-500">
                        Loading transactions…
                      </td>
                    </tr>
                  ) : transactions.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-10 text-center text-slate-500">
                        No transactions recorded for this ticker yet.
                      </td>
                    </tr>
                  ) : (
                    transactions.map((tx) => (
                      <tr key={tx.id} className="hover:bg-slate-50/80">
                        <td className="px-4 py-3 text-slate-700">{tx.date ?? '—'}</td>
                        <td className="px-4 py-3 text-slate-700">{tx.type ?? '—'}</td>
                        <td className="px-4 py-3 text-slate-700">{tx.shares ?? '—'}</td>
                        <td className="px-4 py-3 text-slate-700">
                          {tx.price_per_share ?? '—'}
                        </td>
                        <td className="px-4 py-3 text-slate-700">{tx.fees ?? '—'}</td>
                        <td className="px-4 py-3 text-slate-700">{tx.notes ?? '—'}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => handleEditTransaction(tx)}
                              className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              onClick={() => handleDeleteTransaction(tx.id)}
                              className="rounded border border-red-300 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50"
                            >
                              Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}

function SummaryCard({ label, value }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold text-slate-900">{value}</div>
    </div>
  )
}

function ScenarioBlock({ scenarioKey, title, headerClass, values, onChange }) {
  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className={`px-4 py-2.5 text-center text-sm font-bold tracking-wide ${headerClass}`}>
        {title} Scenario
      </div>
      <div className="space-y-5 p-6">
        <div>
          <p className="text-sm font-semibold text-slate-800">
            Revenue Growth (%)
          </p>
          <div className="mt-3 grid grid-cols-3 gap-3">
            <GrowthYearInput
              label="Year 1"
              id={`${scenarioKey}-rev-y1`}
              value={values.revGrowthY1}
              onChange={(v) => onChange('revGrowthY1', v)}
            />
            <GrowthYearInput
              label="Year 2"
              id={`${scenarioKey}-rev-y2`}
              value={values.revGrowthY2}
              onChange={(v) => onChange('revGrowthY2', v)}
            />
            <GrowthYearInput
              label="Year 3"
              id={`${scenarioKey}-rev-y3`}
              value={values.revGrowthY3}
              onChange={(v) => onChange('revGrowthY3', v)}
            />
          </div>
        </div>

        <div>
          <p className="text-sm font-semibold text-slate-800">
            Net Income Growth (%)
          </p>
          <div className="mt-3 grid grid-cols-3 gap-3">
            <GrowthYearInput
              label="Year 1"
              id={`${scenarioKey}-ni-y1`}
              value={values.netIncomeGrowthY1}
              onChange={(v) => onChange('netIncomeGrowthY1', v)}
            />
            <GrowthYearInput
              label="Year 2"
              id={`${scenarioKey}-ni-y2`}
              value={values.netIncomeGrowthY2}
              onChange={(v) => onChange('netIncomeGrowthY2', v)}
            />
            <GrowthYearInput
              label="Year 3"
              id={`${scenarioKey}-ni-y3`}
              value={values.netIncomeGrowthY3}
              onChange={(v) => onChange('netIncomeGrowthY3', v)}
            />
          </div>
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <label htmlFor={`${scenarioKey}-durable`} className={labelClass}>
              Durable Growth View (%)
            </label>
            <input
              id={`${scenarioKey}-durable`}
              type="number"
              step="0.01"
              value={values.durableGrowthView}
              onChange={(e) => onChange('durableGrowthView', e.target.value)}
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor={`${scenarioKey}-weight`} className={labelClass}>
              Weight on Growth View (%)
            </label>
            <input
              id={`${scenarioKey}-weight`}
              type="number"
              step="1"
              min="0"
              max="100"
              value={values.growthWeightPct}
              onChange={(e) => onChange('growthWeightPct', e.target.value)}
              className={inputClass}
            />
          </div>
        </div>
      </div>
    </section>
  )
}

function GrowthYearInput({ label, id, value, onChange }) {
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-slate-500">
        {label}
      </label>
      <input
        id={id}
        type="number"
        step="0.01"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={inputClass}
      />
    </div>
  )
}