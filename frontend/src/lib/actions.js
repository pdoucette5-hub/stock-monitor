/** Mirrors backend logic.classify_action; CAGRs are decimals (0.15 = 15%). */

export function classifyAction(bearCagr, baseCagr, bullCagr, settings) {
  const bear = safeDecimal(bearCagr)
  const base = safeDecimal(baseCagr)
  const bull = safeDecimal(bullCagr)
  const threshold = safeDecimal(
    settings?.target_cagr_threshold_pct != null
      ? settings.target_cagr_threshold_pct / 100
      : null,
  )

  if (bear == null || base == null || bull == null || threshold == null) {
    return 'Needs Input'
  }

  const strongBuyBaseCutoff =
    (settings.target_cagr_threshold_pct +
      (settings.strong_buy_base_premium_pct ?? 15)) /
    100
  const buyBaseCutoff =
    (settings.target_cagr_threshold_pct + (settings.buy_base_premium_pct ?? 3)) /
    100
  const buyBearCutoff =
    (settings.target_cagr_threshold_pct - (settings.buy_bear_buffer_pct ?? 5)) /
    100
  const riskBaseCutoff =
    (settings.target_cagr_threshold_pct -
      (settings.risk_base_shortfall_pct ?? 5)) /
    100
  const speculativeBullCutoff =
    (settings.target_cagr_threshold_pct +
      (settings.speculative_bull_premium_pct ?? 50)) /
    100

  if (bear >= threshold && base >= strongBuyBaseCutoff) return 'Strong Buy'
  if (base >= buyBaseCutoff && bear >= buyBearCutoff) return 'Buy'
  if (bull >= speculativeBullCutoff && base > 0) return 'Speculative Buy'
  if (bear > 0 && base > threshold) return 'Hold / Watch'
  if (bear < 0 && base <= riskBaseCutoff) return 'High Risk / Review'
  if (bear < 0 && base < threshold) return 'Consider Trim'
  return 'Hold / Watch'
}

function safeDecimal(value) {
  if (value == null || value === '' || Number.isNaN(Number(value))) return null
  return Number(value)
}

export function confidenceBadgeClass(confidence) {
  if (confidence === 'OK') {
    return 'bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200'
  }
  if (confidence === 'Review Assumptions') {
    return 'bg-amber-50 text-amber-900 ring-1 ring-amber-200'
  }
  return 'bg-red-50 text-red-800 ring-1 ring-red-200'
}

export function queueActionBadgeClass(action) {
  switch (action) {
    case 'Add':
      return 'bg-emerald-100 text-emerald-900 ring-1 ring-emerald-300 font-semibold'
    case 'Trim':
      return 'bg-red-100 text-red-900 ring-1 ring-red-400 font-semibold'
    case 'Review':
      return 'bg-amber-50 text-amber-900 ring-1 ring-amber-200'
    case 'Excluded':
      return 'bg-indigo-50 text-indigo-800 ring-1 ring-indigo-200'
    default:
      return 'bg-slate-100 text-slate-700 ring-1 ring-slate-200'
  }
}

/** Light-theme badge styles — soft backgrounds, strong text for contrast on white. */
export function actionBadgeClass(action) {
  switch (action) {
    case 'Strong Buy':
      return 'bg-emerald-100 text-emerald-900 ring-1 ring-emerald-300'
    case 'Buy':
      return 'bg-green-50 text-green-800 ring-1 ring-green-200'
    case 'Speculative Buy':
      return 'bg-sky-50 text-sky-800 ring-1 ring-sky-200'
    case 'Hold / Watch':
      return 'bg-slate-100 text-slate-700 ring-1 ring-slate-200'
    case 'Consider Trim':
      return 'bg-red-50 text-red-800 ring-1 ring-red-300'
    case 'High Risk / Review':
      return 'bg-rose-100 text-rose-900 ring-1 ring-rose-300'
    case 'Trim':
      return 'bg-red-100 text-red-900 ring-1 ring-red-400 font-semibold'
    case 'Add':
      return 'bg-emerald-100 text-emerald-900 ring-1 ring-emerald-300 font-semibold'
    default:
      return 'bg-amber-50 text-amber-900 ring-1 ring-amber-200'
  }
}

export function isBuyAction(action) {
  return ['Strong Buy', 'Buy', 'Speculative Buy', 'Add'].includes(action)
}

export function isTrimAction(action) {
  return ['Consider Trim', 'High Risk / Review', 'Trim'].includes(action)
}
