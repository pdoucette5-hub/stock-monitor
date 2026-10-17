/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import StockDetail from './StockDetail'
import { clearPortfolioViewCache, usePortfolioView } from '../hooks/usePortfolioView'
import {
  addPortfolioTicker, archiveTicker, fetchPriceComparison, fetchStockScenario, fetchTransactionsWindow,
  refreshEarningsCalendar, removeTicker, savePortfolioControls, savePortfolioShares,
  saveStockScenario, syncSheetTickers, refreshIntradayPrices,
} from '../lib/api'
import { apiToForm, formToApi } from '../lib/scenarioForm'
import { cashActivity, combinedPeriodGain, gainForRow, historyStartDate, PERFORMANCE_MODES_FOR_FILTER, periodGainFromHoldings, localDate, summarizeHoldings, withPreviousClose } from '../lib/holdingsGain'
import { useAuth } from '../auth/AuthContext'
import { formatMoney } from '../lib/format'

const FILTERS = [
  ['active', 'Managed + tracked'], ['managed', 'Managed'], ['track', 'Track only'],
  ['excluded', 'Excluded'], ['all', 'All'], ['hidden', 'Hidden'],
]
const RANGES = [['1d', 'Today'], ['7d', '7D'], ['1m', '1M'], ['3m', '3M'], ['6m', '6M'], ['ytd', 'YTD'], ['1y', '1Y'], ['3y', '3Y'], ['5y', '5Y']]
const DETAIL_TABS = [['overview', 'Overview'], ['forecast', 'Forecast'], ['financials', 'Financials'], ['activity', 'Activity']]
const SCENARIOS = [
  { id: 'bull', label: 'Bull', bar: 'bg-emerald-500', head: 'bg-emerald-50 text-emerald-800' },
  { id: 'base', label: 'Base', bar: 'bg-blue-500', head: 'bg-blue-50 text-blue-800' },
  { id: 'bear', label: 'Bear', bar: 'bg-amber-500', head: 'bg-amber-50 text-amber-800' },
]
const inputClass = 'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20'

function sharesFor(row, filter) {
  if (filter === 'managed') return Number(row.managed_shares || 0)
  if (filter === 'track') return Number(row.track_shares || 0)
  if (filter === 'excluded') return Number(row.excluded_shares || 0)
  if (filter === 'active') return Number(row.managed_shares || 0) + Number(row.track_shares || 0)
  return Number(row.shares || 0)
}
function date(value) {
  if (!value) return '—'
  const parsed = new Date(`${value}T00:00:00`)
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}
function pointDate(point) {
  if (point?.timestamp != null) {
    const parsed = new Date(Number(point.timestamp) * 1000)
    if (!Number.isNaN(parsed.getTime())) return parsed
  }
  const parsed = new Date(`${point?.date}T00:00:00`)
  return parsed
}
function number(value, digits = 2) {
  return value == null || Number.isNaN(Number(value)) ? '—' : Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}
function percent(value, digits = 1) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(digits)}%`
}
function compactMoney(value) {
  return Number(value).toLocaleString(undefined, { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 })
}
function Sparkline({ points, positive }) {
  const values = (points || []).map((point) => Number(point?.close)).filter(Number.isFinite)
  if (values.length < 2) return <div className="h-9 rounded bg-slate-100/50" />
  const [width, height] = [100, 34]
  const min = Math.min(...values); const span = Math.max(...values) - min || 1
  const path = values.map((value, index) => `${index ? 'L' : 'M'} ${(index / (values.length - 1)) * width} ${height - 2 - ((value - min) / span) * (height - 4)}`).join(' ')
  return <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="h-9 w-full" aria-hidden="true"><path d={path} fill="none" stroke={positive ? '#10b981' : '#ef4444'} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
}

function Chart({ points, positive, signed = false, compact = false, fill = false, heightClass = 'h-72', label = 'Interactive price history chart', empty = 'No price history for this window.' }) {
  const [hoveredIndex, setHoveredIndex] = useState(null)
  // fill: draw at the container's actual size, so the chart uses all the
  // space it is given instead of keeping a fixed shape.
  const boxRef = useRef(null)
  const [box, setBox] = useState(null)
  const valid = (points || []).filter((point) => Number.isFinite(Number(point?.close)))
  const hasData = valid.length >= 2
  useEffect(() => {
    const element = boxRef.current
    if (!fill || !element) return undefined
    const observer = new ResizeObserver(([entry]) => setBox({ width: entry.contentRect.width, height: entry.contentRect.height }))
    observer.observe(element)
    return () => observer.disconnect()
  }, [fill, hasData])
  if (!hasData) return <div className={`flex ${heightClass} items-center justify-center text-sm text-slate-500`}>{empty}</div>
  if (fill && !box?.width) return <div ref={boxRef} className={`${heightClass} w-full`} />
  const [width, height, left, right, top, bottom] = fill ? [Math.round(box.width), Math.round(box.height), 56, 8, 10, 26] : [900, 310, 66, 18, 16, 40]
  const plotWidth = width - left - right; const plotHeight = height - top - bottom
  const values = valid.map((point) => Number(point.close)); const rawMin = Math.min(...values); const rawMax = Math.max(...values); const rawSpan = rawMax - rawMin || Math.max(rawMax * .05, 1)
  const min = signed ? rawMin - rawSpan * .06 : Math.max(0, rawMin - rawSpan * .06); const max = rawMax + rawSpan * .06; const span = max - min
  const x = (i) => left + (i / (valid.length - 1)) * plotWidth
  const y = (v) => top + ((max - v) / span) * plotHeight
  const line = values.map((v, i) => `${i ? 'L' : 'M'} ${x(i)} ${y(v)}`).join(' ')
  const base = signed ? y(Math.min(Math.max(0, min), max)) : top + plotHeight
  const area = `${line} L ${x(values.length - 1)} ${base} L ${left} ${base} Z`
  const color = positive ? '#059669' : '#dc2626'; const id = `${signed ? 'signed-' : ''}${positive ? 'gain-fill' : 'loss-fill'}`
  const yTicks = Array.from({ length: 5 }, (_, i) => ({ value: max - (span * i) / 4, y: top + (plotHeight * i) / 4 }))
  const xTickCount = Math.min(7, valid.length); const xTicks = Array.from({ length: xTickCount }, (_, i) => Math.round((i * (valid.length - 1)) / (xTickCount - 1)))
  const intraday = valid.some((point) => point?.timestamp != null)
  const firstYear = String(valid[0].date).slice(0, 4); const lastYear = String(valid.at(-1).date).slice(0, 4); const showYears = firstYear !== lastYear
  const axisDate = (point) => intraday
    ? pointDate(point).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
    : pointDate(point).toLocaleDateString(undefined, { month: 'short', day: showYears ? undefined : 'numeric', year: showYears ? '2-digit' : undefined })
  const tooltipDate = (point) => intraday
    ? pointDate(point).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
    : date(point.date)
  const hover = hoveredIndex == null ? null : valid[hoveredIndex]
  const hoverX = hover ? x(hoveredIndex) : 0; const hoverY = hover ? y(Number(hover.close)) : 0
  const tooltipX = hoverX > width - 160 ? hoverX - 146 : hoverX + 10; const tooltipY = Math.max(8, Math.min(height - 64, hoverY - 58))
  const onPointerMove = (event) => { const rect = event.currentTarget.getBoundingClientRect(); const ratio = (event.clientX - rect.left) / rect.width; setHoveredIndex(Math.max(0, Math.min(valid.length - 1, Math.round(ratio * (valid.length - 1))))) }
  const svg = <svg viewBox={`0 0 ${width} ${height}`} className={`${fill ? 'h-full' : heightClass} w-full touch-none select-none`} role="img" aria-label={label}>
    <defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity=".28" /><stop offset="100%" stopColor={color} stopOpacity="0" /></linearGradient></defs>
    {yTicks.map((tick) => <g key={tick.y}><line x1={left} x2={width - right} y1={tick.y} y2={tick.y} stroke="#e2e8f0" strokeDasharray="4 6" /><text x={left - 9} y={tick.y + 4} textAnchor="end" fontSize="11" fill="#64748b">{signed || compact ? compactMoney(tick.value) : formatMoney(tick.value)}</text></g>)}
    {xTicks.map((index, tickIndex) => <g key={`${valid[index].timestamp || valid[index].date}-${index}`}><line x1={x(index)} x2={x(index)} y1={top} y2={top + plotHeight} stroke="#f1f5f9" /><text x={x(index)} y={height - 10} textAnchor={tickIndex === 0 ? 'start' : tickIndex === xTicks.length - 1 ? 'end' : 'middle'} fontSize="11" fill="#64748b">{axisDate(valid[index])}</text></g>)}
    {signed && min < 0 && max > 0 && <line x1={left} x2={width - right} y1={y(0)} y2={y(0)} stroke="#94a3b8" />}
    <path d={area} fill={`url(#${id})`} /><path d={line} fill="none" stroke={color} strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />
    {hover && <g pointerEvents="none"><line x1={hoverX} x2={hoverX} y1={top} y2={top + plotHeight} stroke="#475569" strokeWidth="1" strokeDasharray="4 4" /><circle cx={hoverX} cy={hoverY} r="5" fill="white" stroke={color} strokeWidth="3" /><rect x={tooltipX} y={tooltipY} width="136" height="50" rx="8" fill="#0f172a" opacity=".96" /><text x={tooltipX + 10} y={tooltipY + 20} fontSize="12" fontWeight="700" fill="white">{signed && Number(hover.close) >= 0 ? '+' : ''}{formatMoney(hover.close)}</text><text x={tooltipX + 10} y={tooltipY + 38} fontSize="10" fill="#cbd5e1">{tooltipDate(hover)}</text></g>}
    <rect x={left} y={top} width={plotWidth} height={plotHeight} fill="transparent" onPointerMove={onPointerMove} onPointerLeave={() => setHoveredIndex(null)} />
  </svg>
  return fill ? <div ref={boxRef} className={`${heightClass} w-full`}>{svg}</div> : svg
}

function Metric({ label, value, large }) {
  return <div className="rounded-xl border border-slate-200 bg-slate-50/70 px-4 py-3"><div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</div><div className={`mt-1 font-semibold text-slate-900 ${large ? 'text-xl' : 'text-base'}`}>{value}</div></div>
}

function GrowthRow({ label, values, onChange }) {
  return <fieldset><legend className="text-xs font-medium text-slate-600">{label} (%)</legend><div className="mt-1 grid grid-cols-3 gap-2">{values.map((value, i) => <label key={i} className="text-[10px] font-semibold uppercase text-slate-400">Y{i + 1}<input aria-label={`${label} year ${i + 1}`} type="number" step="0.1" value={value} onChange={(event) => onChange(i, event.target.value)} className={`mt-1 ${inputClass}`} /></label>)}</div></fieldset>
}

function AssumptionsModal({ ticker, close, saved }) {
  const [form, setForm] = useState(null); const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false); const [error, setError] = useState(null)
  useEffect(() => {
    let cancelled = false
    fetchStockScenario(ticker).then((payload) => { if (!cancelled) setForm(apiToForm(payload?.scenario)) }).catch((err) => { if (!cancelled) setError(err.message) }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [ticker])
  useEffect(() => { const key = (event) => event.key === 'Escape' && close(); window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key) }, [close])
  const update = (scenario, field, value) => setForm((current) => ({ ...current, [scenario]: { ...current[scenario], [field]: value } }))
  const submit = async (event) => {
    event.preventDefault(); setSaving(true); setError(null)
    try { await saveStockScenario(ticker, formToApi(form)); clearPortfolioViewCache(); await saved(); close() }
    catch (err) { setError(err.message || 'Failed to save assumptions') }
    finally { setSaving(false) }
  }
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" onMouseDown={(event) => event.target === event.currentTarget && close()}><div className="max-h-[92vh] w-full max-w-5xl overflow-y-auto rounded-2xl bg-white shadow-2xl"><header className="sticky top-0 z-10 flex justify-between border-b border-slate-200 bg-white/95 px-6 py-5 backdrop-blur"><div><div className="text-xs font-bold uppercase tracking-[.18em] text-blue-600">{ticker}</div><h2 className="mt-1 text-xl font-bold text-slate-950">Growth assumptions</h2><p className="mt-1 text-sm text-slate-500">Adjust revenue, earnings, and durable growth for each scenario.</p></div><button type="button" onClick={close} className="rounded-full p-2 text-2xl leading-none text-slate-500 hover:bg-slate-100">×</button></header>{loading ? <div className="p-12 text-center text-slate-500">Loading assumptions…</div> : form ? <form onSubmit={submit} className="p-6">{error && <div className="mb-5 rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</div>}<div className="grid gap-4 lg:grid-cols-3">{SCENARIOS.map((scenario) => { const v = form[scenario.id]; return <section key={scenario.id} className="overflow-hidden rounded-xl border border-slate-200"><div className={`border-b border-slate-200 px-4 py-3 text-sm font-bold ${scenario.head}`}>{scenario.label} scenario</div><div className="space-y-4 p-4"><GrowthRow label="Revenue growth" values={[v.revGrowthY1, v.revGrowthY2, v.revGrowthY3]} onChange={(i, value) => update(scenario.id, `revGrowthY${i + 1}`, value)} /><GrowthRow label="Net income growth" values={[v.netIncomeGrowthY1, v.netIncomeGrowthY2, v.netIncomeGrowthY3]} onChange={(i, value) => update(scenario.id, `netIncomeGrowthY${i + 1}`, value)} /><label className="block text-xs font-medium text-slate-600">Durable growth view (%)<input type="number" step=".1" value={v.durableGrowthView} onChange={(event) => update(scenario.id, 'durableGrowthView', event.target.value)} className={`mt-1 ${inputClass}`} /></label><label className="block text-xs font-medium text-slate-600">Growth-view weight (%)<input type="number" min="0" max="100" value={v.growthWeightPct} onChange={(event) => update(scenario.id, 'growthWeightPct', event.target.value)} className={`mt-1 ${inputClass}`} /></label></div></section> })}</div><footer className="mt-6 flex justify-end gap-3 border-t border-slate-200 pt-5"><button type="button" onClick={close} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700">Cancel</button><button disabled={saving} className="rounded-lg bg-slate-950 px-5 py-2 text-sm font-semibold text-white disabled:opacity-60">{saving ? 'Saving…' : 'Save assumptions'}</button></footer></form> : <div className="p-12 text-center text-red-700">{error || 'Could not load assumptions.'}</div>}</div></div>
}

export default function Holdings() {
  const { authEnabled, user } = useAuth(); const full = !authEnabled || user?.role !== 'limited'
  const [searchParams, setSearchParams] = useSearchParams()
  const { view, setView, loading, error, lastUpdated, load } = usePortfolioView()
  const [filter, setFilter] = useState('managed')
  const [ticker, setTicker] = useState(''); const [range, setRange] = useState('1y'); const [history, setHistory] = useState({})
  const [loadingHistory, setLoadingHistory] = useState(false); const [modal, setModal] = useState(false)
  const [addOpen, setAddOpen] = useState(false); const [menu, setMenu] = useState(false); const [maintenanceMenu, setMaintenanceMenu] = useState(false)
  const [newTicker, setNewTicker] = useState(''); const [newShares, setNewShares] = useState(''); const [shareDraft, setShareDraft] = useState('')
  const [savingTicker, setSavingTicker] = useState(null); const [formError, setFormError] = useState(null); const [message, setMessage] = useState(null)
  const [syncing, setSyncing] = useState(false); const [refreshingEarnings, setRefreshingEarnings] = useState(false); const autoRefresh = useRef(false)
  const requestedTab = searchParams.get('tab')
  const [detailTab, setDetailTab] = useState(DETAIL_TABS.some(([id]) => id === requestedTab) ? requestedTab : 'overview')

  useEffect(() => {
    const nextTab = DETAIL_TABS.some(([id]) => id === requestedTab) && (full || requestedTab !== 'activity') ? requestedTab : 'overview'
    if (nextTab !== detailTab) setDetailTab(nextTab)
  }, [detailTab, full, requestedTab])

  useEffect(() => {
    if (!full || !view || autoRefresh.current) return
    autoRefresh.current = true; const key = 'stock-monitor-earnings-last-refresh'; const last = Number(localStorage.getItem(key) || 0)
    if (Date.now() - last < 3 * 86400000) return
    setRefreshingEarnings(true); refreshEarningsCalendar([], 120).then(() => { localStorage.setItem(key, String(Date.now())); clearPortfolioViewCache() }).catch((err) => setFormError(err.message)).finally(() => setRefreshingEarnings(false))
  }, [full, load, view])

  const rows = useMemo(() => {
    let source = filter === 'hidden'
      ? (view?.portfolio || []).filter((row) => row.show_in_holdings === false)
      : (view?.portfolio || []).filter((row) => row.show_in_holdings !== false)
    const requestedTicker = searchParams.get('ticker')?.trim().toUpperCase()
    const requestedWatchlistRow = (view?.watchlist || []).find((row) => row.ticker === requestedTicker)
    if (requestedWatchlistRow && !source.some((row) => row.ticker === requestedTicker)) {
      source = [{ ...requestedWatchlistRow, management_mode: 'watchlist' }, ...source]
    }
    if (!full) return source
    return source.map((row) => { const shares = sharesFor(row, filter); return { ...row, total_shares: row.shares, shares, market_value: Number.isFinite(Number(row.price)) ? shares * Number(row.price) : null } }).filter((row) => filter === 'all' || Number(row.shares) > 0 || Number(row.total_shares || 0) <= 0)
  }, [filter, full, searchParams, view])
  useEffect(() => {
    if (!rows.length) return setTicker('')
    if (rows.some((row) => row.ticker === ticker)) return
    const requestedTicker = searchParams.get('ticker')?.trim().toUpperCase()
    setTicker(rows.some((row) => row.ticker === requestedTicker) ? requestedTicker : rows[0].ticker)
  }, [rows, searchParams, ticker])
  useEffect(() => {
    if (!rows.length) return; let cancelled = false; setLoadingHistory(true)
    fetchPriceComparison(rows.map((row) => row.ticker), range).then((result) => { if (!cancelled) setHistory(result?.price_series || result?.series || {}) }).catch(() => { if (!cancelled) setHistory({}) }).finally(() => { if (!cancelled) setLoadingHistory(false) })
    return () => { cancelled = true }
  }, [range, rows])
  // For Today, gains start from the previous close rather than the first intraday sample.
  const [dailyHistory, setDailyHistory] = useState({})
  useEffect(() => {
    if (range !== '1d' || !rows.length) return; let cancelled = false
    fetchPriceComparison(rows.map((row) => row.ticker), '7d').then((result) => { if (!cancelled) setDailyHistory(result?.price_series || result?.series || {}) }).catch(() => { if (!cancelled) setDailyHistory({}) })
    return () => { cancelled = true }
  }, [range, rows])
  const gainHistory = useMemo(() => range === '1d' ? withPreviousClose(history, dailyHistory, localDate()) : history, [range, history, dailyHistory])
  const selected = rows.find((row) => row.ticker === ticker); const points = history[ticker] || []; const selectedGain = gainForRow(selected, gainHistory[ticker] || points, range)
  const selectTicker = (nextTicker) => {
    setTicker(nextTicker)
    const next = new URLSearchParams(searchParams)
    next.set('ticker', nextTicker)
    if (detailTab !== 'overview') next.set('tab', detailTab)
    setSearchParams(next, { replace: true })
  }
  const selectDetailTab = (nextTab) => {
    setDetailTab(nextTab)
    const next = new URLSearchParams(searchParams)
    if (ticker) next.set('ticker', ticker)
    if (nextTab === 'overview') next.delete('tab'); else next.set('tab', nextTab)
    setSearchParams(next, { replace: true })
  }
  useEffect(() => { setShareDraft(selected?.total_shares ?? selected?.shares ?? ''); setMenu(false) }, [selected])
  // Reload holdings after a save. The server drops its cached view on every
  // save, so a normal load returns the change in about a second; a forced
  // load would also re-fetch live quotes for every ticker (like "Update
  // prices"), which took ~40s and made saves look like they did nothing.
  const refresh = async () => { clearPortfolioViewCache(); await load(false) }
  const guard = async (work, success) => { setSavingTicker(ticker); setFormError(null); try { await work(); await refresh(); if (success) setMessage(success) } catch (err) { setFormError(err.message) } finally { setSavingTicker(null) } }
  const add = async (event) => { event.preventDefault(); const symbol = newTicker.trim().toUpperCase(); const shares = Number(newShares); if (!symbol || newShares === '' || !Number.isFinite(shares) || shares < 0) return setFormError('Enter a ticker and a non-negative share count.'); try { await addPortfolioTicker(symbol, shares); if (full) await syncSheetTickers().catch(() => null); setNewTicker(''); setNewShares(''); setAddOpen(false); await refresh(); setTicker(symbol); setMessage(`Added ${symbol} to the portfolio.`) } catch (err) { setFormError(err.message) } }
  const saveShares = () => { const value = Number(shareDraft); if (Number.isFinite(value) && value >= 0) guard(() => savePortfolioShares(ticker, value), `Updated ${ticker} shares.`) }
  const toggle = () => guard(() => savePortfolioControls([{ ticker, show_in_holdings: selected.show_in_holdings === false }]))
  const archive = () => guard(() => archiveTicker(ticker), `Archived ${ticker}.`)
  const remove = async () => {
    const removedTicker = ticker
    if (!window.confirm(`Remove ${removedTicker} from tracking?`)) return
    const previousView = view
    setMenu(false); setFormError(null); setSavingTicker(removedTicker)
    setView((current) => current ? { ...current, portfolio: (current.portfolio || []).filter((row) => row.ticker !== removedTicker) } : current)
    try {
      await removeTicker(removedTicker)
      clearPortfolioViewCache()
      setMessage(`Removed ${removedTicker}.`)
    } catch (err) {
      setView(previousView)
      setTicker(removedTicker)
      setFormError(err.message)
    } finally {
      setSavingTicker(null)
    }
  }
  const sync = async () => { setSyncing(true); try { const result = await syncSheetTickers(); setMessage(`Synced ${result.ticker_count ?? 0} tickers to Google Sheets.`) } catch (err) { setFormError(err.message) } finally { setSyncing(false) } }
  const refreshEarnings = async () => { setRefreshingEarnings(true); try { const result = await refreshEarningsCalendar(); await refresh(); setMessage(`Refreshed earnings dates for ${result?.refreshed?.length ?? 0} tickers.`) } catch (err) { setFormError(err.message) } finally { setRefreshingEarnings(false) } }
  const updatePrices = async () => {
    setFormError(null); setLoadingHistory(true); clearPortfolioViewCache()
    try {
      if (range === '1d') await refreshIntradayPrices()
      const [, result] = await Promise.all([
        load(true),
        rows.length ? fetchPriceComparison(rows.map((row) => row.ticker), range, true) : Promise.resolve(null),
      ])
      if (result) setHistory(result?.price_series || result?.series || {})
    } catch (err) { setFormError(err.message) }
    finally { setLoadingHistory(false) }
  }

  return <div className="mx-auto w-full max-w-[1600px] px-4 py-3 lg:px-6"><header className="mb-3 flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-bold tracking-tight text-slate-950">Holdings</h1><div className="flex items-center gap-2"><button type="button" onClick={updatePrices} disabled={loading || loadingHistory} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60">{loading || loadingHistory ? 'Updating…' : 'Update prices'}</button><button onClick={() => setAddOpen((v) => !v)} className="rounded-lg bg-slate-950 px-4 py-2 text-xs font-semibold text-white">+ Add holding</button><div className="relative"><button type="button" aria-label="More holdings actions" aria-expanded={maintenanceMenu} onClick={() => setMaintenanceMenu((v) => !v)} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-lg leading-none text-slate-700">…</button>{maintenanceMenu && <div className="absolute right-0 top-11 z-30 w-52 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-xl">{full && <button type="button" onClick={() => { setMaintenanceMenu(false); sync() }} disabled={syncing} className="block w-full px-4 py-2 text-left text-sm hover:bg-slate-50 disabled:opacity-60">{syncing ? 'Syncing sheet…' : 'Sync Google Sheet'}</button>}{full && <button type="button" onClick={() => { setMaintenanceMenu(false); refreshEarnings() }} disabled={refreshingEarnings} className="block w-full px-4 py-2 text-left text-sm hover:bg-slate-50 disabled:opacity-60">{refreshingEarnings ? 'Refreshing earnings…' : 'Refresh earnings'}</button>}</div>}</div></div></header>
    {full && rows.length > 0 && <HoldingsSummary rows={rows} history={gainHistory} range={range} setRange={setRange} filter={filter} view={view} />}
    {(error || formError) && <div role="alert" className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{formError || error}</div>}
    {message && <div role="status" className="mb-4 flex justify-between rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900"><span>{message}</span><button onClick={() => setMessage(null)}>×</button></div>}
    {addOpen && <form onSubmit={add} className="mb-4 flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"><label className="text-xs font-medium text-slate-600">Ticker<input autoFocus value={newTicker} onChange={(e) => setNewTicker(e.target.value.toUpperCase())} className={`mt-1 block w-32 ${inputClass}`} /></label><label className="text-xs font-medium text-slate-600">Shares<input type="number" min="0" step=".01" value={newShares} onChange={(e) => setNewShares(e.target.value)} className={`mt-1 block w-32 ${inputClass}`} /></label><button className="rounded-lg bg-slate-950 px-4 py-2 text-sm font-semibold text-white">Add</button><button type="button" onClick={() => setAddOpen(false)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm">Cancel</button></form>}
    <div className="grid min-h-[680px] overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm lg:grid-cols-[340px_minmax(0,1fr)]"><aside className="border-b border-slate-200 bg-slate-50/60 lg:border-b-0 lg:border-r"><div className="flex items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3"><div><h2 className="font-bold text-slate-950">Your stocks</h2><p className="text-xs text-slate-500">{rows.length} positions</p></div>{full && <select aria-label="Filter holdings" value={filter} onChange={(event) => setFilter(event.target.value)} className="max-w-40 rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs font-semibold text-slate-700">{FILTERS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>}</div><div className="max-h-[760px] overflow-y-auto">{loading && !rows.length ? <Empty text="Loading holdings…" /> : !rows.length ? <Empty text="No holdings match this filter." /> : rows.map((row) => { const rowPoints = history[row.ticker] || []; const rowGain = gainForRow(row, gainHistory[row.ticker] || rowPoints, range); const positive = (rowGain?.percent ?? 0) >= 0; const active = ticker === row.ticker; return <button key={row.ticker} onClick={() => selectTicker(row.ticker)} className={`grid w-full grid-cols-[minmax(0,1fr)_74px_66px] items-center gap-3 border-b border-slate-200 px-4 py-3 text-left ${active ? 'bg-slate-950 text-white' : 'bg-white hover:bg-slate-50'}`}><div className="min-w-0"><div className="font-bold">{row.ticker}</div><div className={`truncate text-xs ${active ? 'text-slate-400' : 'text-slate-500'}`}>{formatMoney(row.market_value)} · {number(row.shares)} sh</div></div><Sparkline points={rowPoints} positive={positive} /><div className="text-right"><div className={`text-xs font-bold ${rowGain ? positive ? 'text-emerald-500' : 'text-red-500' : 'text-slate-500'}`}>{loadingHistory && !rowPoints.length && !rowGain ? '…' : percent(rowGain?.percent)}</div><div className={`text-[10px] ${active ? 'text-slate-400' : 'text-slate-500'}`}>{rowGain ? `${rowGain.amount >= 0 ? '+' : ''}${formatMoney(rowGain.amount)}` : '—'}</div></div></button> })}</div>{lastUpdated && <div className="border-t border-slate-200 px-4 py-2 text-[11px] text-slate-500">Updated {lastUpdated.toLocaleTimeString()}</div>}</aside>
      <main className="min-w-0">{!selected ? <Empty text="Select a holding to see details." /> : <><header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-5 py-3 sm:px-7"><div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1"><h1 className="text-2xl font-black text-slate-950">{ticker}</h1><span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold text-slate-700">{selected.action || 'Needs input'}</span><span className="text-xl font-bold text-slate-950">{formatMoney(selected.price)}</span><span className={`text-sm font-bold ${selectedGain ? selectedGain.percent >= 0 ? 'text-emerald-600' : 'text-red-600' : 'text-slate-500'}`}>{percent(selectedGain?.percent, 2)} <span className="font-medium text-slate-500">· {number(selected.shares)} shares</span></span></div><div className="flex gap-2"><button onClick={() => setModal(true)} className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white">Edit assumptions</button><div className="relative"><button onClick={() => setMenu((v) => !v)} className="rounded-lg border border-slate-300 px-3 py-2 text-lg leading-none">…</button>{menu && <div className="absolute right-0 top-11 z-20 w-48 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-xl"><button onClick={toggle} className="block w-full px-4 py-2 text-left text-sm hover:bg-slate-50">{selected.show_in_holdings === false ? 'Show in holdings' : 'Hide from holdings'}</button>{full && <button onClick={archive} className="block w-full px-4 py-2 text-left text-sm hover:bg-slate-50">Archive ticker</button>}<button onClick={remove} className="block w-full px-4 py-2 text-left text-sm text-red-700 hover:bg-red-50">Remove ticker</button></div>}</div></div></header><DetailTabs value={detailTab} set={selectDetailTab} full={full} />{detailTab === 'overview' ? <><div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3 sm:px-7"><span className="text-xs font-semibold text-slate-500">Price history · {Object.fromEntries(RANGES)[range] || range}</span>{!full && <RangeButtons value={range} set={setRange} />}</div><div className="px-4 sm:px-7"><Chart points={points} positive={(selectedGain?.percent ?? 0) >= 0} /></div><div className="grid gap-4 px-5 pb-7 sm:px-7 xl:grid-cols-[minmax(0,1.35fr)_minmax(300px,.65fr)]"><section><div className="mb-3 flex items-center justify-between"><h2 className="text-sm font-bold uppercase tracking-wide text-slate-500">Position & valuation</h2>{full && <div className="flex gap-2"><input aria-label="Shares" type="number" min="0" step=".01" value={shareDraft} onChange={(e) => setShareDraft(e.target.value)} className="w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-sm" /><button onClick={saveShares} disabled={savingTicker === ticker} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold">Save shares</button></div>}</div><div className="grid grid-cols-2 gap-3 sm:grid-cols-3"><Metric label="Market value" value={formatMoney(selected.market_value)} large /><Metric label="Average cost" value={formatMoney(selected.average_cost_per_share)} /><Metric label="Current P/E" value={selected.current_pe == null ? '—' : `${number(selected.current_pe)}x`} /><Metric label="Bear target" value={formatMoney(selected.bear_price_y3)} /><Metric label="Base target" value={formatMoney(selected.base_price_y3)} /><Metric label="Bull target" value={formatMoney(selected.bull_price_y3)} /></div></section><section className="rounded-xl border border-slate-200 bg-slate-50/60 p-4"><div className="flex justify-between"><h2 className="text-sm font-bold text-slate-950">Three-year outlook</h2><button onClick={() => setModal(true)} className="text-xs font-semibold text-blue-700">Change</button></div><div className="mt-4 space-y-4">{SCENARIOS.map((scenario) => { const cagr = selected[`${scenario.id}_cagr_y3`]; const width = Math.max(4, Math.min(100, (Number(cagr || 0) * 100 + 20) * 2)); return <div key={scenario.id}><div className="mb-1 flex justify-between text-xs"><span className="font-semibold text-slate-600">{scenario.label}</span><span className="font-bold">{percent(cagr == null ? null : Number(cagr) * 100)}</span></div><div className="h-2 rounded-full bg-slate-200"><div className={`h-full rounded-full ${scenario.bar}`} style={{ width: `${width}%` }} /></div></div> })}</div><div className="mt-5 border-t border-slate-200 pt-4"><div className="text-[11px] font-semibold uppercase text-slate-500">Next earnings</div><div className="mt-1 font-semibold">{date(selected.next_earnings_date)}</div>{selected.next_earnings_eps_forecast && <div className="text-xs text-slate-500">EPS forecast {selected.next_earnings_eps_forecast}</div>}</div></section></div></> : <StockDetail key={ticker} embeddedTicker={ticker} embeddedSection={detailTab} />}</>}</main></div>
    {modal && selected && <AssumptionsModal ticker={ticker} close={() => setModal(false)} saved={refresh} />}
  </div>
}

// Total value of the listed positions and their gain or loss over the selected
// period: for each stock and day, the shares held that day times that day's
// price move (see periodGainFromHoldings). Trades in the period come from the
// transaction history, so buying and selling is never counted as gain. Follows
// the period buttons and the holdings filter.
function HoldingsSummary({ rows, history, range, setRange, filter, view }) {
  const stockRows = useMemo(() => rows.filter((row) => !NON_SECURITIES.has(row.ticker)), [rows])
  const stockValue = useMemo(() => summarizeHoldings(stockRows, history, range).totalValue, [stockRows, history, range])
  const modes = PERFORMANCE_MODES_FOR_FILTER[filter]
  const since = useMemo(() => historyStartDate(history), [history])
  const rowTickers = useMemo(() => rows.map((row) => row.ticker).join(','), [rows])
  const [state, setState] = useState({ key: '', loading: true, error: null, trades: null, cashStart: {}, soldHistory: {} })
  const key = `${since}|${filter}|${range}|${rowTickers}`
  useEffect(() => {
    if (!modes || !since) return undefined
    let cancelled = false
    setState({ key, loading: true, error: null, trades: null, cashStart: {}, soldHistory: {} })
    ;(async () => {
      const result = await fetchTransactionsWindow(since, modes)
      const trades = result?.trades || {}
      // Positions sold during the period still count while they were held.
      const held = new Set(rowTickers.split(','))
      const sold = Object.keys(trades).filter((ticker) => !held.has(ticker) && !NON_SECURITIES.has(ticker) && trades[ticker].some((trade) => SHARE_TRADES.has(trade.type)))
      let soldHistory = {}
      if (sold.length) {
        const prices = await fetchPriceComparison(sold, range === '1d' ? '7d' : range).catch(() => null)
        soldHistory = prices?.price_series || prices?.series || {}
        if (range === '1d') soldHistory = withPreviousClose(soldHistory, soldHistory, localDate())
      }
      if (!cancelled) setState({ key, loading: false, error: null, trades, cashStart: result?.cash_start || {}, soldHistory, loadedAt: Date.now() / 1000 })
    })().catch((err) => { if (!cancelled) setState({ key, loading: false, error: err.message, trades: null, cashStart: {}, soldHistory: {} }) })
    return () => { cancelled = true }
    // view: reload after holdings change (e.g. a share edit or import)
  }, [key, view]) // eslint-disable-line react-hooks/exhaustive-deps
  const ready = Boolean(modes && since && state.key === key && !state.loading && state.trades)
  const result = useMemo(() => {
    if (!ready) return null
    const soldRows = Object.keys(state.soldHistory).map((ticker) => ({ ticker, shares: 0, price: null }))
    const stocks = periodGainFromHoldings([...stockRows, ...soldRows], { ...state.soldHistory, ...history }, state.trades)
    return combinedPeriodGain(stocks, cashActivity(state.trades, state.cashStart, stocks?.startDate || since))
  }, [ready, stockRows, history, state.trades, state.cashStart, state.soldHistory, since])
  const chartPoints = useMemo(() => (result?.series || []).map((point) => {
    if (point.key === '~') return { date: localDate(), timestamp: range === '1d' ? state.loadedAt : null, close: point.value }
    const [day, stamp] = point.key.split('T')
    return { date: day, timestamp: stamp ? Number(stamp) : null, close: point.value }
  }), [result, range, state.loadedAt])
  const cash = result?.cashEnd ?? null
  const totalValue = stockValue + (cash || 0)
  const rangeLabel = Object.fromEntries(RANGES)[range] || range
  const filterLabel = Object.fromEntries(FILTERS)[filter] || 'All'
  const loading = Boolean(modes) && (!since || (state.key === key ? state.loading : true))
  const error = state.key === key ? state.error : null
  const change = result?.change ?? null
  const tone = change == null ? 'text-slate-500' : change >= 0 ? 'text-emerald-600' : 'text-red-600'
  const note = !modes ? 'Gain / loss is not available for hidden holdings.' : error ? `Gain / loss unavailable: ${error}` : null
  return <section aria-label="Holdings summary" className="mb-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
    <div className="mb-3 flex overflow-x-auto"><RangeButtons value={range} set={setRange} /></div>
    <div className="grid gap-x-6 gap-y-4 md:grid-cols-[230px_minmax(0,1fr)]">
    <div className="flex flex-col justify-center gap-5">
      <div><div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Total value</div><div className="text-2xl font-black text-slate-950">{formatMoney(totalValue)}</div>{cash != null && <div className="text-[11px] text-slate-500">incl. {formatMoney(cash)} cash</div>}</div>
      <div><div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Gain / loss · {rangeLabel}</div><div className={`text-2xl font-black ${tone}`}>{!modes ? '—' : loading ? '…' : change == null ? '—' : `${change >= 0 ? '+' : ''}${formatMoney(change)}`}<span className="ml-2 whitespace-nowrap text-base font-bold">{modes && !loading && result ? percent(result.percent, 2) : ''}</span></div></div>
      <div className="text-xs text-slate-500"><div>{filterLabel} · {rows.length} positions</div>{note && <div>{note}</div>}</div>
    </div>
    <div className="min-w-0">{loading ? <div className="flex h-64 items-center justify-center text-sm text-slate-500">Loading…</div> : <Chart points={chartPoints} positive={(change ?? 0) >= 0} compact fill heightClass="h-64" label="Portfolio value over the period" empty="No price history for this period." />}</div>
    </div>
  </section>
}

// Money market funds and cash entries: counted as cash, not as stocks.
// (Not USD: that is ProShares Ultra Semiconductors, a real holding.)
const NON_SECURITIES = new Set(['SPAXX', 'FDRXX', 'FZFXX', 'SPRXX', 'FCASH', 'CORE'])
const SHARE_TRADES = new Set(['buy', 'sell', 'transfer_in', 'transfer_out', 'adjustment'])

function RangeButtons({ value, set, compact = false }) {
  return <div className={`flex rounded-lg bg-slate-100 p-1 ${compact ? 'w-full justify-between' : ''}`}>{RANGES.map(([id, label]) => <button key={id} onClick={() => set(id)} className={`rounded-md font-bold ${compact ? 'px-1.5 py-1 text-[10px]' : 'px-3 py-1.5 text-xs'} ${value === id ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500'}`}>{label}</button>)}</div>
}
function DetailTabs({ value, set, full }) {
  return <div className="flex gap-1 overflow-x-auto border-b border-slate-200 bg-slate-50/70 px-5 pt-3 sm:px-7">{DETAIL_TABS.filter(([id]) => full || id !== 'activity').map(([id, label]) => <button key={id} type="button" onClick={() => set(id)} className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm font-semibold ${value === id ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-900'}`}>{label}</button>)}</div>
}
function Empty({ text }) { return <div className="flex min-h-40 items-center justify-center p-10 text-center text-sm text-slate-500">{text}</div> }
