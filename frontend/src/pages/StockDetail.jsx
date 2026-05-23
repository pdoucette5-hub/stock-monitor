import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  fetchPortfolioScenarios,
  fetchStockScenario,
  fetchTickersConfig,
  saveStockScenario,
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

export default function StockDetail() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [tickers, setTickers] = useState([])
  const [selectedTicker, setSelectedTicker] = useState('')
  const [form, setForm] = useState(defaultFormState)
  const [loadingTickers, setLoadingTickers] = useState(true)
  const [loadingForm, setLoadingForm] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [saveMessage, setSaveMessage] = useState(null)
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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- initial ticker list only
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

  useEffect(() => {
    if (!selectedTicker) return
    setSearchParams({ ticker: selectedTicker }, { replace: true })
    loadScenario(selectedTicker)
  }, [selectedTicker, loadScenario, setSearchParams])

  const handleTickerChange = (event) => {
    setSelectedTicker(event.target.value)
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

  const disableForm = loadingForm || saving || !selectedTicker

  const pageSubtitle = useMemo(() => {
    if (loadingForm) return 'Loading assumptions…'
    if (isNewTicker) return 'No saved assumptions yet — defaults shown below.'
    return 'Edit assumptions and save to update the cache.'
  }, [loadingForm, isNewTicker])

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
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
      )}
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
