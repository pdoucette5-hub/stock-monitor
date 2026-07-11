export const FORECAST_YEARS = 3
export const SCENARIO_KEYS = ['bull', 'base', 'bear']

const BILLION = 1_000_000_000

export function dollarsToBillions(value) {
  if (value == null || value === '' || Number.isNaN(Number(value))) return ''
  return Number((Number(value) / BILLION).toFixed(4))
}

export function billionsToDollars(value) {
  if (value === '' || value == null) return null
  const parsed = Number(value)
  if (Number.isNaN(parsed)) return null
  return parsed * BILLION
}

export function parseNumber(value, fallback = null) {
  if (value === '' || value == null) return fallback
  const parsed = Number(value)
  return Number.isNaN(parsed) ? fallback : parsed
}

export function defaultScenarioAssumptions(kind) {
  const presets = {
    bull: {
      rev_growth_rates: [9.6, 9.6, 9.6],
      net_income_growth_rates: [12, 12, 12],
      durable_growth_view: 20,
      growth_weight_pct: 75,
    },
    base: {
      rev_growth_rates: [8, 8, 8],
      net_income_growth_rates: [10, 10, 10],
      durable_growth_view: 12,
      growth_weight_pct: 60,
    },
    bear: {
      rev_growth_rates: [6.4, 6.4, 6.4],
      net_income_growth_rates: [8, 8, 8],
      durable_growth_view: 5,
      growth_weight_pct: 35,
    },
  }
  return structuredClone(presets[kind] ?? presets.base)
}

export function defaultFormState() {
  return {
    latestQuarterRevenueB: '',
    latestQuarterNetIncomeB: '',
    sharesOutstandingB: '',
    actualsSourcePreference: 'manual',
    notes: '',
    bull: defaultScenarioAssumptions('bull'),
    base: defaultScenarioAssumptions('base'),
    bear: defaultScenarioAssumptions('bear'),
  }
}

function scenarioToFormSlice(scenario) {
  const rev = scenario?.rev_growth_rates ?? []
  const ni = scenario?.net_income_growth_rates ?? []
  return {
    revGrowthY1: rev[0] ?? '',
    revGrowthY2: rev[1] ?? '',
    revGrowthY3: rev[2] ?? '',
    netIncomeGrowthY1: ni[0] ?? '',
    netIncomeGrowthY2: ni[1] ?? '',
    netIncomeGrowthY3: ni[2] ?? '',
    durableGrowthView: scenario?.durable_growth_view ?? '',
    growthWeightPct: scenario?.growth_weight_pct ?? '',
  }
}

export function apiToForm(scenario) {
  if (!scenario) return defaultFormState()

  return {
    latestQuarterRevenueB: dollarsToBillions(scenario.latest_quarter_revenue),
    latestQuarterNetIncomeB: dollarsToBillions(scenario.latest_quarter_net_income),
    sharesOutstandingB: dollarsToBillions(scenario.shares_outstanding),
    actualsSourcePreference: scenario.actuals_source_preference ?? 'manual',
    notes: scenario.notes ?? '',
    bull: scenarioToFormSlice(scenario.bull),
    base: scenarioToFormSlice(scenario.base),
    bear: scenarioToFormSlice(scenario.bear),
    _meta: {
      redistribution_rules: scenario.redistribution_rules,
      display_rules: scenario.display_rules,
      trade_rules: scenario.trade_rules,
    },
  }
}

function formSliceToScenario(slice) {
  return {
    rev_growth_rates: [
      parseNumber(slice.revGrowthY1, 0),
      parseNumber(slice.revGrowthY2, 0),
      parseNumber(slice.revGrowthY3, 0),
    ],
    net_income_growth_rates: [
      parseNumber(slice.netIncomeGrowthY1, 0),
      parseNumber(slice.netIncomeGrowthY2, 0),
      parseNumber(slice.netIncomeGrowthY3, 0),
    ],
    durable_growth_view: parseNumber(slice.durableGrowthView, 0),
    growth_weight_pct: parseNumber(slice.growthWeightPct, 0),
  }
}

export function formToApi(form) {
  const redistribution_rules = form._meta?.redistribution_rules ?? {
    include_in_redistribution: false,
    eligible_redistribution_shares: null,
  }
  const display_rules = form._meta?.display_rules ?? {
    show_in_holdings: true,
  }

  const payload = {
    latest_quarter_revenue: billionsToDollars(form.latestQuarterRevenueB),
    latest_quarter_net_income: billionsToDollars(form.latestQuarterNetIncomeB),
    shares_outstanding: billionsToDollars(form.sharesOutstandingB),
    actuals_source_preference:
      form.actualsSourcePreference === 'reported' ? 'reported' : 'manual',
    notes: form.notes ?? '',
    redistribution_rules,
    display_rules,
    bull: formSliceToScenario(form.bull),
    base: formSliceToScenario(form.base),
    bear: formSliceToScenario(form.bear),
  }

  if (form._meta?.trade_rules != null) {
    payload.trade_rules = form._meta.trade_rules
  }

  return payload
}
