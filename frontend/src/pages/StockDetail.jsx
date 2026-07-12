import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  createTransaction,
  deleteTransaction,
  fetchAccounts,
  fetchPortfolioScenarios,
  fetchPositionSummary,
  fetchPriceHistory,
  fetchReportedFundamentals,
  fetchStockScenario,
  fetchTickerRegistry,
  fetchTransactions,
  refreshReportedFundamentals,
  saveStockScenario,
  updateTransaction,
} from '../lib/api'
import {
  apiToForm,
  defaultFormState,
  formToApi,
} from '../lib/scenarioForm'
import { clearPortfolioViewCache } from '../hooks/usePortfolioView'
import { useAuth } from '../auth/AuthContext'

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

const PRICE_RANGES = ['1m', '3m', '6m', '1y', '3y', '5y']

function collectTickers(tickersPayload, scenariosPayload) {
  const tickersConfig = tickersPayload?.effective ?? tickersPayload
  const fromConfig = [
    ...(tickersConfig?.portfolio ?? []).map((p) =>
      String(p.ticker).trim().toUpperCase(),
    ),
    ...(tickersConfig?.watchlist ?? []).map((t) =>
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
    account: '',
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

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return `${Number(value).toFixed(1)}%`
}

function formatBillions(value) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `$${(Number(value) / 1_000_000_000).toFixed(2)}B`
}

function formatSharesBillions(value) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `${(Number(value) / 1_000_000_000).toFixed(2)}B`
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

function formatShortDate(dateString) {
  if (!dateString) return ''
  const d = new Date(dateString)
  if (Number.isNaN(d.getTime())) return dateString
  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: '2-digit',
  })
}

function pickFirstValue(...values) {
  return values.find((value) => value !== null && value !== undefined && value !== '')
}

function getProjectionMetric(projection, metricKey) {
  if (!projection || typeof projection !== 'object') return [null, null, null]

  const direct = projection[metricKey]
  const snake = projection[`${metricKey}_rates`]
  const pct = projection[`${metricKey}_pct`]
  const growth = projection[`${metricKey}_growth`]

  if (Array.isArray(direct)) return direct
  if (Array.isArray(snake)) return snake
  if (Array.isArray(pct)) return pct
  if (Array.isArray(growth)) return growth

  const objectSource =
    (direct && typeof direct === 'object' ? direct : null) ||
    (growth && typeof growth === 'object' ? growth : null) ||
    projection

  const fieldPrefix = metricKey.replace(/_growth$/, '')
  return [1, 2, 3].map((year) =>
    pickFirstValue(
      objectSource[`y${year}`],
      objectSource[`year${year}`],
      objectSource[`${metricKey}_y${year}`],
      objectSource[`${metricKey}_year${year}`],
      objectSource[`${fieldPrefix}_growth_y${year}`],
      objectSource[`${fieldPrefix}_growth_year${year}`],
    ),
  )
}

function buildOnlineProjection(reported) {
  if (!reported || typeof reported !== 'object') return null
  const projection =
    reported.online_projection ||
    reported.online_projections ||
    reported.analyst_projection ||
    reported.consensus_projection ||
    reported.projection ||
    reported.projections

  if (!projection || typeof projection !== 'object') return null

  return {
    source:
      projection.source ||
      projection.provider ||
      reported.projection_source ||
      reported.projections_source ||
      'online',
    asOf:
      projection.as_of ||
      projection.asOf ||
      projection.updated_at ||
      reported.projection_as_of ||
      reported.projections_as_of,
    revenueGrowth: getProjectionMetric(projection, 'revenue_growth'),
    earningsGrowth: getProjectionMetric(projection, 'earnings_growth'),
  }
}

function PriceChart({ points }) {
  if (!points || points.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-sm text-slate-500">
        No chart data available.
      </div>
    )
  }

  const width = 900
  const height = 280
  const padding = 36

  const validPoints = points.filter((p) => p?.close !== null && p?.close !== undefined && !Number.isNaN(Number(p.close)))
  if (validPoints.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-sm text-slate-500">
        No chart data available.
      </div>
    )
  }

  if (validPoints.length === 1) {
    const point = validPoints[0]
    return (
      <div className="flex h-64 flex-col items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-center text-sm text-slate-500">
        <div className="text-lg font-semibold text-slate-900">
          {formatMoney(point.close)}
        </div>
        <div className="mt-2">
          Only one close has been imported for this ticker.
        </div>
        <div className="mt-1 text-xs text-slate-400">
          Last imported: {formatShortDate(point.date)}
        </div>
      </div>
    )
  }

  const closes = validPoints.map((p) => Number(p.close))
  const minPrice = Math.min(...closes)
  const maxPrice = Math.max(...closes)
  const priceRange = maxPrice - minPrice || 1

  const xFor = (idx) => {
    if (validPoints.length === 1) return padding
    return padding + (idx / (validPoints.length - 1)) * (width - padding * 2)
  }

  const yFor = (price) => {
    return height - padding - ((price - minPrice) / priceRange) * (height - padding * 2)
  }

  const linePath = validPoints
    .map((point, idx) => `${idx === 0 ? 'M' : 'L'} ${xFor(idx)} ${yFor(Number(point.close))}`)
    .join(' ')

  const firstPoint = validPoints[0]
  const lastPoint = validPoints[validPoints.length - 1]
  const change = Number(lastPoint.close) - Number(firstPoint.close)
  const changePct = firstPoint.close ? (change / Number(firstPoint.close)) * 100 : 0
  const positive = change >= 0

  const ticks = 4
  const priceTicks = Array.from({ length: ticks + 1 }, (_, i) => {
    const value = minPrice + ((maxPrice - minPrice) * i) / ticks
    return {
      value,
      y: yFor(value),
    }
  })

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-slate-500">Price Trend</div>
          <div className="text-lg font-semibold text-slate-900">
            {formatMoney(lastPoint.close)}
          </div>
        </div>
        <div
          className={`rounded-full px-3 py-1 text-sm font-medium ${
            positive
              ? 'bg-emerald-50 text-emerald-700'
              : 'bg-red-50 text-red-700'
          }`}
        >
          {positive ? '+' : ''}
          {formatMoney(change)} ({positive ? '+' : ''}
          {changePct.toFixed(2)}%)
        </div>
      </div>

      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${width} ${height}`} className="h-72 w-full min-w-[700px]">
          {priceTicks.map((tick) => (
            <g key={tick.value}>
              <line
                x1={padding}
                x2={width - padding}
                y1={tick.y}
                y2={tick.y}
                stroke="#e2e8f0"
                strokeDasharray="4 4"
              />
              <text
                x={8}
                y={tick.y + 4}
                fontSize="11"
                fill="#64748b"
              >
                {Number(tick.value).toFixed(2)}
              </text>
            </g>
          ))}

          <path
            d={linePath}
            fill="none"
            stroke={positive ? '#059669' : '#dc2626'}
            strokeWidth="3"
            strokeLinejoin="round"
            strokeLinecap="round"
          />

          {validPoints.map((point, idx) => {
            if (idx !== 0 && idx !== validPoints.length - 1) return null
            return (
              <g key={`${point.date}-${idx}`}>
                <circle
                  cx={xFor(idx)}
                  cy={yFor(Number(point.close))}
                  r="4"
                  fill={positive ? '#059669' : '#dc2626'}
                />
                <text
                  x={xFor(idx)}
                  y={height - 8}
                  textAnchor={idx === 0 ? 'start' : 'end'}
                  fontSize="11"
                  fill="#64748b"
                >
                  {formatShortDate(point.date)}
                </text>
              </g>
            )
          })}
        </svg>
      </div>
    </div>
  )
}

export default function StockDetail() {
  const { authEnabled, user } = useAuth()
  const hasFullAccess = !authEnabled || user?.role !== 'limited'
  const [searchParams, setSearchParams] = useSearchParams()
  const [tickers, setTickers] = useState([])
  const [selectedTicker, setSelectedTicker] = useState('')
  const [priceRange, setPriceRange] = useState('3y')
  const [form, setForm] = useState(defaultFormState)
  const [transactions, setTransactions] = useState([])
  const [accountOptions, setAccountOptions] = useState([])
  const [positionSummary, setPositionSummary] = useState(null)
  const [priceHistory, setPriceHistory] = useState([])
  const [reportedFundamentals, setReportedFundamentals] = useState({})
  const [transactionForm, setTransactionForm] = useState(emptyTransactionForm())
  const [loadingTickers, setLoadingTickers] = useState(true)
  const [loadingForm, setLoadingForm] = useState(false)
  const [loadingTransactions, setLoadingTransactions] = useState(false)
  const [loadingPosition, setLoadingPosition] = useState(false)
  const [loadingPriceHistory, setLoadingPriceHistory] = useState(false)
  const [loadingReported, setLoadingReported] = useState(false)
  const [saving, setSaving] = useState(false)
  const [refreshingReported, setRefreshingReported] = useState(false)
  const [savingTransaction, setSavingTransaction] = useState(false)
  const [error, setError] = useState(null)
  const [saveMessage, setSaveMessage] = useState(null)
  const [transactionMessage, setTransactionMessage] = useState(null)
  const [isNewTicker, setIsNewTicker] = useState(false)
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)
  const [autosaveState, setAutosaveState] = useState('idle')
  const lastSavedPayloadRef = useRef('')
  const saveSequenceRef = useRef(0)

  useEffect(() => {
    let cancelled = false

    async function loadTickers() {
      setLoadingTickers(true)
      setError(null)
      try {
        const [tickersPayload, scenariosPayload] = await Promise.all([
          fetchTickerRegistry(),
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

  const loadAccounts = useCallback(async () => {
    if (!hasFullAccess) {
      setAccountOptions([])
      return
    }

    try {
      const payload = await fetchAccounts()
      const accounts = Array.isArray(payload?.accounts) ? payload.accounts : []
      setAccountOptions(
        [...new Set(accounts.map((account) => String(account).trim()).filter(Boolean))]
          .sort((a, b) => a.localeCompare(b)),
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load accounts')
    }
  }, [hasFullAccess])

  const loadReportedFundamentals = useCallback(async () => {
    if (!hasFullAccess) {
      setReportedFundamentals({})
      return
    }

    setLoadingReported(true)
    try {
      const payload = await fetchReportedFundamentals()
      setReportedFundamentals(payload?.fundamentals ?? {})
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load reported fundamentals')
    } finally {
      setLoadingReported(false)
    }
  }, [hasFullAccess])

  useEffect(() => {
    loadAccounts()
    loadReportedFundamentals()
  }, [loadAccounts, loadReportedFundamentals])

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
      const loadedForm = apiToForm(data.scenario)
      setForm(loadedForm)
      lastSavedPayloadRef.current = JSON.stringify(formToApi(loadedForm))
      setHasUnsavedChanges(false)
      setAutosaveState('idle')
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load stock'
      const lowerMessage = message.toLowerCase()
      if (
        message.includes('404') ||
        lowerMessage.includes('not found') ||
        lowerMessage.includes('no scenario found')
      ) {
        setError(null)
        const fallbackForm = defaultFormState()
        setForm(fallbackForm)
        lastSavedPayloadRef.current = ''
        setHasUnsavedChanges(false)
        setAutosaveState('idle')
        setIsNewTicker(true)
      } else {
        setError(message)
      }
    } finally {
      setLoadingForm(false)
    }
  }, [])

  const loadTickerTransactions = useCallback(async (ticker) => {
    if (!ticker || !hasFullAccess) {
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
  }, [hasFullAccess])

  const loadPosition = useCallback(async (ticker) => {
    if (!ticker || !hasFullAccess) {
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
  }, [hasFullAccess])

  const loadPriceHistory = useCallback(async (ticker, range) => {
    if (!ticker) {
      setPriceHistory([])
      return
    }

    setLoadingPriceHistory(true)
    try {
      const payload = await fetchPriceHistory(ticker, range)
      setPriceHistory(Array.isArray(payload?.points) ? payload.points : [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load price history')
      setPriceHistory([])
    } finally {
      setLoadingPriceHistory(false)
    }
  }, [])

  useEffect(() => {
    if (!selectedTicker) return
    loadScenario(selectedTicker)
    loadTickerTransactions(selectedTicker)
    loadPosition(selectedTicker)
    setTransactionForm(emptyTransactionForm())
  }, [selectedTicker, loadScenario, loadTickerTransactions, loadPosition])

  useEffect(() => {
    if (!selectedTicker) return
    loadPriceHistory(selectedTicker, priceRange)
  }, [selectedTicker, priceRange, loadPriceHistory])

  const handleTickerChange = (event) => {
    const nextTicker = event.target.value
    if (
      hasUnsavedChanges &&
      !window.confirm('Discard unsaved assumption changes?')
    ) {
      return
    }
    setHasUnsavedChanges(false)
    setAutosaveState('idle')
    setSelectedTicker(nextTicker)
    setSearchParams({ ticker: nextTicker }, { replace: true })
  }

  const updateBase = (field, value) => {
    setForm((prev) => ({ ...prev, [field]: value }))
    setSaveMessage(null)
    setHasUnsavedChanges(true)
    setAutosaveState('idle')
  }

  const refreshSelectedReportedFundamentals = async () => {
    if (!selectedTicker) return
    setRefreshingReported(true)
    setError(null)
    setSaveMessage(null)
    try {
      const result = await refreshReportedFundamentals([selectedTicker])
      const refreshed = result?.refreshed?.length ?? 0
      const tickerError = result?.errors?.find((item) => item?.ticker === selectedTicker)
      if (tickerError) {
        setError(tickerError.error || `Failed to refresh ${selectedTicker}`)
      } else {
        setSaveMessage(
          refreshed
            ? `Refreshed SEC actuals for ${selectedTicker}.`
            : `No SEC actuals found for ${selectedTicker}.`,
        )
      }
      await loadReportedFundamentals()
      clearPortfolioViewCache()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to refresh SEC actuals')
    } finally {
      setRefreshingReported(false)
    }
  }

  const updateScenario = (scenarioKey, field, value) => {
    setForm((prev) => ({
      ...prev,
      [scenarioKey]: { ...prev[scenarioKey], [field]: value },
    }))
    setSaveMessage(null)
    setHasUnsavedChanges(true)
    setAutosaveState('idle')
  }

  const persistScenario = useCallback(async () => {
    if (!selectedTicker) return

    const payload = formToApi(form)
    const signature = JSON.stringify(payload)

    if (signature === lastSavedPayloadRef.current) {
      setHasUnsavedChanges(false)
      setAutosaveState('idle')
      return
    }

    const saveId = saveSequenceRef.current + 1
    saveSequenceRef.current = saveId

    setSaving(true)
    setAutosaveState('saving')
    setSaveMessage(null)

    setError(null)

    try {
      await saveStockScenario(selectedTicker, payload)

      if (saveId !== saveSequenceRef.current) return

      lastSavedPayloadRef.current = signature
      setHasUnsavedChanges(false)
      setIsNewTicker(false)
      clearPortfolioViewCache()

      setAutosaveState('saved')
      await loadScenario(selectedTicker)
      setSaveMessage(`Saved assumptions for ${selectedTicker}.`)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to save assumptions'
      setError(message)
      setAutosaveState('error')
    } finally {
      setSaving(false)
    }
  }, [form, loadScenario, selectedTicker])

  const handleSave = async (event) => {
    event.preventDefault()
    persistScenario()
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
      account: tx.account ?? '',
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
      account: transactionForm.account ?? '',
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
      clearPortfolioViewCache()

      const savedAccount = String(payload.account || '').trim()
      if (savedAccount) {
        setAccountOptions((current) =>
          current.some((account) => account.toLowerCase() === savedAccount.toLowerCase())
            ? current
            : [...current, savedAccount].sort((a, b) => a.localeCompare(b)),
        )
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
      clearPortfolioViewCache()
      await loadTickerTransactions(selectedTicker)
      await loadPosition(selectedTicker)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete transaction')
    } finally {
      setSavingTransaction(false)
    }
  }

  const disableForm = loadingForm || saving || !selectedTicker
  const selectedReported = selectedTicker ? reportedFundamentals[selectedTicker] : null
  const actualsPreference =
    form.actualsSourcePreference === 'reported' ? 'reported' : 'manual'
  const onlineProjection = buildOnlineProjection(selectedReported)
  const reportedAvailable = Boolean(
    selectedReported &&
      (
        selectedReported.latest_quarter_revenue != null ||
        selectedReported.latest_quarter_net_income != null ||
        selectedReported.shares_outstanding != null
      ),
  )

  const pageSubtitle = useMemo(() => {
    if (loadingForm) return 'Loading assumptions…'
    if (isNewTicker) return 'No saved assumptions yet — defaults shown below.'
    return 'Edit assumptions, then save when you are ready.'
  }, [loadingForm, isNewTicker])

  const autosaveLabel = useMemo(() => {
    if (autosaveState === 'saving') return 'Saving…'
    if (autosaveState === 'saved') return 'Saved'
    if (autosaveState === 'error') return 'Save failed'
    if (hasUnsavedChanges) return 'Unsaved changes'
    return 'Saved'
  }, [autosaveState, hasUnsavedChanges])

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
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-slate-900">Price History</h2>
                <p className="mt-1 text-sm text-slate-500">
                  Historical close prices for {selectedTicker}.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {PRICE_RANGES.map((range) => (
                  <button
                    key={range}
                    type="button"
                    onClick={() => setPriceRange(range)}
                    className={[
                      'rounded-full px-3 py-1 text-sm font-medium transition',
                      priceRange === range
                        ? 'bg-slate-900 text-white'
                        : 'bg-slate-100 text-slate-700 hover:bg-slate-200',
                    ].join(' ')}
                  >
                    {range.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>

            {loadingPriceHistory ? (
              <div className="flex h-64 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-sm text-slate-500">
                Loading price history…
              </div>
            ) : (
              <PriceChart points={priceHistory} />
            )}
          </section>

          {hasFullAccess && (
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
          )}

          <form onSubmit={handleSave} className="space-y-6">
            <fieldset disabled={disableForm} className="space-y-6">
              <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <h2 className="text-lg font-semibold text-slate-900">
                      Actuals Source
                    </h2>
                    <p className="mt-1 text-sm text-slate-500">
                      Choose which starting actuals valuation uses. Manual inputs are preserved.
                    </p>
                  </div>
                  {hasFullAccess && (
                    <button
                      type="button"
                      onClick={refreshSelectedReportedFundamentals}
                      disabled={refreshingReported || !selectedTicker}
                      className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {refreshingReported ? 'Refreshing…' : 'Refresh SEC actuals'}
                    </button>
                  )}
                </div>

                <div className="mt-5 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => updateBase('actualsSourcePreference', 'manual')}
                    className={[
                      'rounded-md px-3 py-2 text-sm font-medium',
                      actualsPreference === 'manual'
                        ? 'bg-slate-900 text-white'
                        : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50',
                    ].join(' ')}
                  >
                    Use manual inputs
                  </button>
                  <button
                    type="button"
                    onClick={() => updateBase('actualsSourcePreference', 'reported')}
                    disabled={!reportedAvailable}
                    title={reportedAvailable ? 'Use saved SEC reported actuals' : 'Refresh SEC actuals first'}
                    className={[
                      'rounded-md px-3 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50',
                      actualsPreference === 'reported'
                        ? 'bg-slate-900 text-white'
                        : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50',
                    ].join(' ')}
                  >
                    Use pulled SEC actuals
                  </button>
                </div>

                <div className="mt-5 grid gap-4 lg:grid-cols-2">
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                    <div className="text-sm font-semibold text-slate-900">
                      Manual Stock Detail
                    </div>
                    <dl className="mt-3 grid gap-2 text-sm text-slate-700">
                      <div className="flex justify-between gap-4">
                        <dt>Revenue</dt>
                        <dd>{form.latestQuarterRevenueB === '' ? '—' : `$${Number(form.latestQuarterRevenueB).toFixed(2)}B`}</dd>
                      </div>
                      <div className="flex justify-between gap-4">
                        <dt>Net income</dt>
                        <dd>{form.latestQuarterNetIncomeB === '' ? '—' : `$${Number(form.latestQuarterNetIncomeB).toFixed(2)}B`}</dd>
                      </div>
                      <div className="flex justify-between gap-4">
                        <dt>Shares</dt>
                        <dd>{form.sharesOutstandingB === '' ? '—' : `${Number(form.sharesOutstandingB).toFixed(2)}B`}</dd>
                      </div>
                    </dl>
                  </div>

                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-semibold text-slate-900">
                        Pulled SEC Actuals
                      </div>
                      <span className="rounded-full bg-white px-2 py-1 text-xs font-medium text-slate-600">
                        {selectedReported?.confidence ?? (loadingReported ? 'loading' : 'not pulled')}
                      </span>
                    </div>
                    <dl className="mt-3 grid gap-2 text-sm text-slate-700">
                      <div className="flex justify-between gap-4">
                        <dt>Revenue</dt>
                        <dd>{formatBillions(selectedReported?.latest_quarter_revenue)}</dd>
                      </div>
                      <div className="flex justify-between gap-4">
                        <dt>Net income</dt>
                        <dd>{formatBillions(selectedReported?.latest_quarter_net_income)}</dd>
                      </div>
                      <div className="flex justify-between gap-4">
                        <dt>Shares</dt>
                        <dd>{formatSharesBillions(selectedReported?.shares_outstanding)}</dd>
                      </div>
                    </dl>
                    <div className="mt-3 text-xs text-slate-500">
                      Period {formatDate(selectedReported?.period_end)} · filed {formatDate(selectedReported?.filed_date)}
                    </div>
                  </div>
                </div>
              </section>

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
                  onlineProjection={onlineProjection}
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
              <span
                className={[
                  'text-sm',
                  autosaveState === 'error'
                    ? 'text-red-600'
                    : autosaveState === 'saving' || hasUnsavedChanges
                      ? 'text-amber-600'
                      : 'text-slate-500',
                ].join(' ')}
              >
                {autosaveLabel}
              </span>
              {loadingForm && (
                <span className="text-sm text-slate-500">Loading form…</span>
              )}
            </div>
          </form>

          {hasFullAccess && (
          <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="mb-5">
              <h2 className="text-lg font-semibold text-slate-900">Transactions</h2>
              <p className="mt-1 text-sm text-slate-500">
                Record buys, sells, dividends, splits, transfers, and adjustments.
              </p>
            </div>

            <form onSubmit={handleSaveTransaction} className="mb-6 grid gap-4 lg:grid-cols-7">
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
                <label className={labelClass}>Account</label>
                <input
                  type="text"
                  list="transaction-account-options"
                  value={transactionForm.account}
                  onChange={(e) => handleTransactionFieldChange('account', e.target.value)}
                  placeholder="e.g. Schwab IRA"
                  autoComplete="off"
                  className={inputClass}
                />
                <datalist id="transaction-account-options">
                  {accountOptions.map((account) => (
                    <option key={account} value={account} />
                  ))}
                </datalist>
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

              <div className="lg:col-span-7 flex flex-wrap items-center gap-3">
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
                    <th className="px-4 py-3 font-semibold text-slate-700">Account</th>
                    <th className="px-4 py-3 font-semibold text-slate-700">Notes</th>
                    <th className="px-4 py-3 font-semibold text-slate-700">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {loadingTransactions ? (
                    <tr>
                      <td colSpan={8} className="px-4 py-10 text-center text-slate-500">
                        Loading transactions…
                      </td>
                    </tr>
                  ) : transactions.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="px-4 py-10 text-center text-slate-500">
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
                        <td className="px-4 py-3 text-slate-700">
                          {tx.account || 'Unassigned'}
                        </td>
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
          )}
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

function ScenarioBlock({ scenarioKey, title, headerClass, values, onlineProjection, onChange }) {
  const projectionSource = onlineProjection?.source ?? 'not pulled'
  const projectionAsOf = onlineProjection?.asOf
  const hasProjection = Boolean(onlineProjection)

  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className={`px-4 py-2.5 text-center text-sm font-bold tracking-wide ${headerClass}`}>
        {title} Scenario
      </div>
      <div className="grid gap-6 p-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(280px,0.75fr)]">
        <div className="space-y-5">
          <div>
            <p className="text-sm font-semibold text-slate-800">
              Manual Revenue Growth (%)
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
              Manual Net Income Growth (%)
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

        <div className="border-t border-slate-200 pt-5 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-slate-900">
                Online Projection
              </p>
              <p className="mt-1 text-xs text-slate-500">
                Read-only consensus benchmark.
              </p>
            </div>
            <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600">
              {projectionSource}
            </span>
          </div>

          <ProjectionRows
            title="Revenue Growth"
            values={onlineProjection?.revenueGrowth}
          />
          <ProjectionRows
            title="Earnings Growth"
            values={onlineProjection?.earningsGrowth}
          />

          <div className="mt-4 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
            {hasProjection
              ? `As of ${formatDate(projectionAsOf)}`
              : 'No online projection has been saved for this ticker yet.'}
          </div>
        </div>
      </div>
    </section>
  )
}

function ProjectionRows({ title, values = [] }) {
  return (
    <div className="mt-5">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </p>
      <div className="mt-2 grid grid-cols-3 gap-2">
        {[0, 1, 2].map((idx) => (
          <div key={idx} className="rounded-md bg-slate-50 px-3 py-2">
            <div className="text-[11px] font-medium text-slate-500">
              Y{idx + 1}
            </div>
            <div className="mt-1 text-sm font-semibold text-slate-900">
              {formatPercent(values[idx])}
            </div>
          </div>
        ))}
      </div>
    </div>
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
