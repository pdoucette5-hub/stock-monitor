
import json
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from data_ingest import get_live_market_data

st.set_page_config(page_title="Stock Monitor", layout="wide")

BASE_DIR = Path(__file__).resolve().parent.parent
TICKERS_FILE = BASE_DIR / "config" / "tickers.yaml"
BULK_IMPORT_FILE = BASE_DIR / "config" / "assumptions_bulk_import.csv"
SCENARIO_STATE_FILE = BASE_DIR / "cache" / "scenario_inputs.json"
GLOBAL_SETTINGS_FILE = BASE_DIR / "cache" / "global_settings.json"
SCENARIO_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
GLOBAL_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

FORECAST_YEARS = 3
QUARTERS_PER_YEAR = 4
SCENARIO_NAMES = ["bear", "base", "bull"]

DEFAULT_GROWTH_PE_TABLE = [
    {"growth": 0.0, "pe": 9.0},
    {"growth": 3.0, "pe": 11.0},
    {"growth": 5.0, "pe": 14.0},
    {"growth": 8.0, "pe": 18.0},
    {"growth": 10.0, "pe": 22.0},
    {"growth": 12.0, "pe": 26.0},
    {"growth": 15.0, "pe": 32.0},
    {"growth": 18.0, "pe": 38.0},
    {"growth": 20.0, "pe": 43.0},
    {"growth": 25.0, "pe": 55.0},
    {"growth": 30.0, "pe": 68.0},
]

DEFAULT_GLOBAL_SETTINGS = {
    "target_cagr_threshold_pct": 15.0,
    "strong_buy_base_premium_pct": 15.0,
    "buy_base_premium_pct": 3.0,
    "buy_bear_buffer_pct": 5.0,
    "risk_base_shortfall_pct": 5.0,
    "speculative_bull_premium_pct": 50.0,
    "portfolio_table_height": 800,
    "watchlist_table_height": 650,
    "growth_pe_table": DEFAULT_GROWTH_PE_TABLE,
    "bottom_pinned_tickers": [],
    "buy_hurdle_pct": 15.0,
    "max_position_weight_pct": 10.0,
    "min_position_weight_pct": 0.0,
    "rebalance_band_pct": 0.75,
    "min_trade_dollars": 1000.0,
}

DEFAULT_REDISTRIBUTION_RULES = {
    "include_in_redistribution": False,
    "eligible_redistribution_shares": None,
}


def load_yaml_file(path: Path, default_data):
    if not path.exists():
        return default_data
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return data if data is not None else default_data


def load_json_file(path: Path, default_data):
    if not path.exists():
        return default_data
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, type(default_data)) else default_data
    except Exception:
        return default_data


def save_json_file(path: Path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def load_persisted_scenarios():
    return load_json_file(SCENARIO_STATE_FILE, {})


def save_persisted_scenarios():
    save_json_file(SCENARIO_STATE_FILE, st.session_state.get("scenario_inputs", {}))


def load_global_settings():
    saved = load_json_file(GLOBAL_SETTINGS_FILE, {})
    settings = dict(DEFAULT_GLOBAL_SETTINGS)
    if isinstance(saved, dict):
        settings.update(saved)

    if not isinstance(settings.get("growth_pe_table"), list):
        settings["growth_pe_table"] = DEFAULT_GROWTH_PE_TABLE

    pinned = settings.get("bottom_pinned_tickers", [])
    if not isinstance(pinned, list):
        pinned = []
    settings["bottom_pinned_tickers"] = sorted(
        {str(x).strip().upper() for x in pinned if str(x).strip()}
    )
    return settings


def save_global_settings():
    save_json_file(
        GLOBAL_SETTINGS_FILE,
        st.session_state.get("global_settings", DEFAULT_GLOBAL_SETTINGS),
    )


def safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_portfolio(portfolio_raw):
    if not portfolio_raw:
        return pd.DataFrame(columns=["ticker", "shares"])

    normalized = []
    for item in portfolio_raw:
        if isinstance(item, str):
            normalized.append({"ticker": item.strip().upper(), "shares": None})
        elif isinstance(item, dict):
            normalized.append(
                {
                    "ticker": str(item.get("ticker", "")).strip().upper(),
                    "shares": safe_float(item.get("shares")),
                }
            )

    df = pd.DataFrame(normalized)
    if "ticker" not in df.columns:
        df["ticker"] = None
    if "shares" not in df.columns:
        df["shares"] = None

    df = df[df["ticker"].notna() & (df["ticker"] != "")]
    return df.reset_index(drop=True)


def normalize_watchlist(watchlist_raw, portfolio_set):
    items = []
    seen = set()

    for item in watchlist_raw or []:
        t = str(item).strip().upper()
        if not t or t in portfolio_set or t in seen:
            continue
        seen.add(t)
        items.append(t)

    return items


def fmt_money(value):
    if value is None or pd.isna(value):
        return "—"
    return f"${float(value):,.2f}"


def fmt_billions(value):
    if value is None or pd.isna(value):
        return "—"
    return f"${float(value) / 1_000_000_000:,.2f}B"


def fmt_pct(value):
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.2f}%"


def fmt_num(value):
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):,.2f}"


def annualized_return(current_price, future_price, years):
    current_price = safe_float(current_price)
    future_price = safe_float(future_price)
    years = safe_float(years)

    if current_price is None or future_price is None or years is None:
        return None
    if current_price <= 0 or future_price <= 0 or years <= 0:
        return None

    return (future_price / current_price) ** (1 / years) - 1


def annual_to_quarterly_growth(annual_growth_pct):
    annual_growth = safe_float(annual_growth_pct)
    if annual_growth is None:
        return None
    annual_decimal = annual_growth / 100.0
    if annual_decimal <= -1.0:
        return -1.0
    return (1.0 + annual_decimal) ** (1.0 / QUARTERS_PER_YEAR) - 1.0


def growth_to_pe_lookup(durable_growth_view_pct, growth_pe_table):
    g = safe_float(durable_growth_view_pct)
    if g is None:
        return None

    points = []
    for item in growth_pe_table or []:
        if not isinstance(item, dict):
            continue
        growth = safe_float(item.get("growth"))
        pe = safe_float(item.get("pe"))
        if growth is not None and pe is not None:
            points.append((growth, pe))

    if not points:
        points = [(x["growth"], x["pe"]) for x in DEFAULT_GROWTH_PE_TABLE]

    points = sorted(points, key=lambda x: x[0])

    if g <= points[0][0]:
        return points[0][1]
    if g >= points[-1][0]:
        return points[-1][1]

    for i in range(1, len(points)):
        g0, pe0 = points[i - 1]
        g1, pe1 = points[i]
        if g0 <= g <= g1:
            if g1 == g0:
                return pe1
            weight = (g - g0) / (g1 - g0)
            return pe0 + weight * (pe1 - pe0)
    return None


def confidence_flag(rev_growth_rates, net_income_growth_rates):
    rev_rates = [safe_float(x) for x in rev_growth_rates]
    ni_rates = [safe_float(x) for x in net_income_growth_rates]

    if any(v is None for v in rev_rates + ni_rates):
        return "Missing assumptions"

    aggressive_years = 0
    for rev_g, ni_g in zip(rev_rates, ni_rates):
        if (ni_g - rev_g) > 15.0:
            aggressive_years += 1

    if aggressive_years >= 2:
        return "Aggressive earnings vs revenue"

    return "OK"


def classify_action(
    bear_cagr,
    base_cagr,
    bull_cagr,
    threshold_pct,
    strong_buy_base_premium_pct,
    buy_base_premium_pct,
    buy_bear_buffer_pct,
    risk_base_shortfall_pct,
    speculative_bull_premium_pct,
):
    bear = safe_float(bear_cagr)
    base = safe_float(base_cagr)
    bull = safe_float(bull_cagr)
    threshold = safe_float(threshold_pct)

    if bear is None or base is None or bull is None or threshold is None:
        return "Needs Input"

    threshold_dec = threshold / 100.0
    strong_buy_base_cutoff = (threshold + safe_float(strong_buy_base_premium_pct, 15.0)) / 100.0
    buy_base_cutoff = (threshold + safe_float(buy_base_premium_pct, 3.0)) / 100.0
    buy_bear_cutoff = (threshold - safe_float(buy_bear_buffer_pct, 5.0)) / 100.0
    risk_base_cutoff = (threshold - safe_float(risk_base_shortfall_pct, 5.0)) / 100.0
    speculative_bull_cutoff = (threshold + safe_float(speculative_bull_premium_pct, 50.0)) / 100.0

    if bear >= threshold_dec and base >= strong_buy_base_cutoff:
        return "Strong Buy"
    if base >= buy_base_cutoff and bear >= buy_bear_cutoff:
        return "Buy"
    if bull >= speculative_bull_cutoff and base > 0:
        return "Speculative Buy"
    if bear > 0 and base > threshold_dec:
        return "Hold / Watch"
    if bear < 0 and base <= risk_base_cutoff:
        return "High Risk / Review"
    if bear < 0 and base < threshold_dec:
        return "Consider Trim"
    return "Hold / Watch"


def action_color(val):
    if val == "Strong Buy":
        return "background-color: #064E3B; color: #A7F3D0; font-weight: 800;"
    if val == "Buy":
        return "background-color: #123524; color: #7CFC9A; font-weight: 700;"
    if val == "Speculative Buy":
        return "background-color: #1E3A8A; color: #BFDBFE; font-weight: 700;"
    if val == "Hold / Watch":
        return "background-color: #1F2937; color: #E5E7EB; font-weight: 700;"
    if val == "Consider Trim":
        return "background-color: #3A1717; color: #FF8A8A; font-weight: 700;"
    if val == "High Risk / Review":
        return "background-color: #4A1D1D; color: #FCA5A5; font-weight: 800;"
    return "background-color: #3A3417; color: #FDE68A; font-weight: 700;"


def confidence_color(val):
    if val == "OK":
        return "background-color: #123524; color: #7CFC9A; font-weight: 700;"
    if val == "Aggressive earnings vs revenue":
        return "background-color: #3A3417; color: #FDE68A; font-weight: 700;"
    return "background-color: #3A1717; color: #FF8A8A; font-weight: 700;"


def action_sort_rank(val):
    rank_map = {
        "Strong Buy": 0,
        "Buy": 1,
        "Speculative Buy": 2,
        "Hold / Watch": 3,
        "High Risk / Review": 4,
        "Consider Trim": 5,
        "Needs Input": 6,
    }
    return rank_map.get(val, 999)


def weighted_cagr(bear_cagr, base_cagr, bull_cagr):
    bear = safe_float(bear_cagr)
    base = safe_float(base_cagr)
    bull = safe_float(bull_cagr)
    if bear is None or base is None or bull is None:
        return None
    return 0.10 * bear + 0.80 * base + 0.10 * bull


def is_bottom_pinned_ticker(ticker, bottom_pinned_tickers):
    ticker = str(ticker).strip().upper()
    return ticker in {str(x).strip().upper() for x in (bottom_pinned_tickers or [])}


def get_default_redistribution_rules():
    return dict(DEFAULT_REDISTRIBUTION_RULES)


def ensure_redistribution_rules(existing_rules):
    rules = get_default_redistribution_rules()
    if isinstance(existing_rules, dict):
        rules.update(existing_rules)
    rules["include_in_redistribution"] = bool(rules.get("include_in_redistribution", False))
    eligible = safe_float(rules.get("eligible_redistribution_shares"))
    if eligible is not None and eligible < 0:
        eligible = 0.0
    rules["eligible_redistribution_shares"] = eligible
    return rules


def display_number_input(col, label, raw_value, key, scale=1.0, help_text=None):
    actual = safe_float(raw_value)
    if actual is None:
        actual = 0.0
    entered = col.number_input(
        label,
        value=float(actual / scale),
        step=0.01,
        format="%.2f",
        key=key,
        help=help_text,
    )
    return entered * scale


def quarterly_run_rate_to_annual(value):
    value = safe_float(value)
    if value is None:
        return None
    return value * QUARTERS_PER_YEAR


def build_scenario_matrix(
    current_price,
    latest_quarter_revenue,
    latest_quarter_net_income,
    shares_outstanding,
    rev_growth_rates,
    net_income_growth_rates,
    durable_growth_view,
    growth_weight_pct,
    growth_pe_table,
):
    current_price = safe_float(current_price)
    latest_quarter_revenue = safe_float(latest_quarter_revenue)
    latest_quarter_net_income = safe_float(latest_quarter_net_income)
    shares_outstanding = safe_float(shares_outstanding)

    if latest_quarter_revenue is None or latest_quarter_net_income is None or shares_outstanding is None:
        return None, None
    if latest_quarter_revenue <= 0 or shares_outstanding <= 0:
        return None, None

    column_labels = ["Current", "Year 1", "Year 2", "Year 3"]

    current_annual_revenue = quarterly_run_rate_to_annual(latest_quarter_revenue)
    current_annual_net_income = quarterly_run_rate_to_annual(latest_quarter_net_income)
    current_margin = ((current_annual_net_income / current_annual_revenue) * 100.0) if current_annual_revenue else None
    current_eps = current_annual_net_income / shares_outstanding if shares_outstanding else None

    revenue_by_year = [current_annual_revenue]
    net_income_by_year = [current_annual_net_income]
    margin_by_year = [current_margin]
    eps_by_year = [current_eps]

    quarter_revenue = latest_quarter_revenue
    quarter_net_income = latest_quarter_net_income

    for year_idx in range(FORECAST_YEARS):
        rev_q_growth = annual_to_quarterly_growth(rev_growth_rates[year_idx])
        ni_q_growth = annual_to_quarterly_growth(net_income_growth_rates[year_idx])

        projected_rev_quarters = []
        projected_ni_quarters = []
        for _ in range(QUARTERS_PER_YEAR):
            quarter_revenue = quarter_revenue * (1 + rev_q_growth)
            quarter_net_income = quarter_net_income * (1 + ni_q_growth)
            projected_rev_quarters.append(quarter_revenue)
            projected_ni_quarters.append(quarter_net_income)

        annual_revenue = sum(projected_rev_quarters)
        annual_net_income = sum(projected_ni_quarters)
        annual_margin = ((annual_net_income / annual_revenue) * 100.0) if annual_revenue else None
        annual_eps = annual_net_income / shares_outstanding if shares_outstanding else None

        revenue_by_year.append(annual_revenue)
        net_income_by_year.append(annual_net_income)
        margin_by_year.append(annual_margin)
        eps_by_year.append(annual_eps)

    current_pe = None
    if current_price is not None and current_eps is not None and current_eps > 0:
        current_pe = current_price / current_eps

    growth_implied_pe = growth_to_pe_lookup(durable_growth_view, growth_pe_table)

    weight_pct = safe_float(growth_weight_pct, 0.0)
    weight_pct = min(max(weight_pct, 0.0), 100.0)
    growth_weight = weight_pct / 100.0
    current_pe_weight = 1.0 - growth_weight

    blended_future_pe = None
    if growth_implied_pe is not None and current_pe is not None:
        blended_future_pe = growth_weight * growth_implied_pe + current_pe_weight * current_pe
    elif growth_implied_pe is not None:
        blended_future_pe = growth_implied_pe
    elif current_pe is not None:
        blended_future_pe = current_pe

    price_y3 = eps_by_year[-1] * blended_future_pe if eps_by_year[-1] is not None and blended_future_pe is not None else None
    cagr_y3 = annualized_return(current_price, price_y3, FORECAST_YEARS)

    df = pd.DataFrame(
        {
            "Current": [revenue_by_year[0], None, net_income_by_year[0], None, margin_by_year[0], eps_by_year[0], current_pe, None, None, None, None, None, None],
            "Year 1": [revenue_by_year[1], safe_float(rev_growth_rates[0]), net_income_by_year[1], safe_float(net_income_growth_rates[0]), margin_by_year[1], eps_by_year[1], None, None, None, None, None, None, None],
            "Year 2": [revenue_by_year[2], safe_float(rev_growth_rates[1]), net_income_by_year[2], safe_float(net_income_growth_rates[1]), margin_by_year[2], eps_by_year[2], None, None, None, None, None, None, None],
            "Year 3": [revenue_by_year[3], safe_float(rev_growth_rates[2]), net_income_by_year[3], safe_float(net_income_growth_rates[2]), margin_by_year[3], eps_by_year[3], None, durable_growth_view, growth_weight_pct, growth_implied_pe, current_pe, blended_future_pe, price_y3],
        },
        index=[
            "Revenue", "Rev Growth", "Net Income", "Net Inc. / EPS Growth", "Net Margin",
            "EPS", "Current PE", "Durable Growth View", "Weight on Growth View",
            "Growth-Implied Future PE", "Current PE Anchor", "Blended Future PE", "Price (Y3)",
        ],
    )
    cagr_row = pd.DataFrame({"Current": [None], "Year 1": [None], "Year 2": [None], "Year 3": [cagr_y3]}, index=["CAGR (3Y)"])
    df = pd.concat([df, cagr_row])

    summary = {
        "price_y3": price_y3,
        "cagr_y3": cagr_y3,
        "growth_implied_pe": growth_implied_pe,
        "current_pe": current_pe,
        "blended_future_pe": blended_future_pe,
        "confidence_flag": confidence_flag(rev_growth_rates, net_income_growth_rates),
        "annualized_revenue": current_annual_revenue,
        "annualized_net_income": current_annual_net_income,
        "annualized_eps": current_eps,
    }
    return df, summary


def format_matrix(df):
    formatted = df.copy()
    money_rows = {"Revenue", "Net Income"}
    pct_rows = {"Rev Growth", "Net Inc. / EPS Growth", "Net Margin", "Durable Growth View", "Weight on Growth View"}
    price_rows = {"EPS", "Price (Y3)"}
    num_rows = {"Current PE", "Growth-Implied Future PE", "Current PE Anchor", "Blended Future PE"}
    cagr_rows = {"CAGR (3Y)"}

    for idx in formatted.index:
        if idx in money_rows:
            formatted.loc[idx] = formatted.loc[idx].apply(fmt_billions)
        elif idx in pct_rows:
            formatted.loc[idx] = formatted.loc[idx].apply(lambda x: "—" if x is None or pd.isna(x) else f"{float(x):.2f}%")
        elif idx in price_rows:
            formatted.loc[idx] = formatted.loc[idx].apply(fmt_money)
        elif idx in num_rows:
            formatted.loc[idx] = formatted.loc[idx].apply(fmt_num)
        elif idx in cagr_rows:
            formatted.loc[idx] = formatted.loc[idx].apply(lambda x: "—" if x is None or pd.isna(x) else f"{float(x) * 100:.2f}%")
    return formatted


def ensure_scenario_defaults(existing, defaults):
    scenario = dict(existing or {})
    scenario["rev_growth_rates"] = list(scenario.get("rev_growth_rates", defaults["rev_growth_rates"]))[:FORECAST_YEARS]
    scenario["net_income_growth_rates"] = list(scenario.get("net_income_growth_rates", defaults["net_income_growth_rates"]))[:FORECAST_YEARS]

    while len(scenario["rev_growth_rates"]) < FORECAST_YEARS:
        scenario["rev_growth_rates"].append(defaults["rev_growth_rates"][-1])
    while len(scenario["net_income_growth_rates"]) < FORECAST_YEARS:
        scenario["net_income_growth_rates"].append(defaults["net_income_growth_rates"][-1])

    scenario["durable_growth_view"] = safe_float(scenario.get("durable_growth_view", scenario.get("terminal_growth")), defaults["durable_growth_view"])
    scenario["growth_weight_pct"] = safe_float(scenario.get("growth_weight_pct"), defaults["growth_weight_pct"])
    scenario.pop("terminal_growth", None)
    return scenario


def get_scenario_defaults(fetched_revenue_growth, fetched_net_income_growth):
    base_rev_growth = fetched_revenue_growth if fetched_revenue_growth is not None else 8.00
    base_net_income_growth = fetched_net_income_growth if fetched_net_income_growth is not None else 10.00
    bull_rev_growth = base_rev_growth * 1.20
    bull_net_income_growth = base_net_income_growth * 1.20
    bear_rev_growth = base_rev_growth * 0.80
    bear_net_income_growth = base_net_income_growth * 0.80

    return {
        "bull": {"rev_growth_rates": [bull_rev_growth] * FORECAST_YEARS, "net_income_growth_rates": [bull_net_income_growth] * FORECAST_YEARS, "durable_growth_view": 20.00, "growth_weight_pct": 75.00},
        "base": {"rev_growth_rates": [base_rev_growth] * FORECAST_YEARS, "net_income_growth_rates": [base_net_income_growth] * FORECAST_YEARS, "durable_growth_view": 12.00, "growth_weight_pct": 60.00},
        "bear": {"rev_growth_rates": [bear_rev_growth] * FORECAST_YEARS, "net_income_growth_rates": [bear_net_income_growth] * FORECAST_YEARS, "durable_growth_view": 5.00, "growth_weight_pct": 35.00},
    }


def init_ticker_state(ticker, market_row):
    fetched_ttm_revenue = safe_float(market_row.get("ttm_revenue"))
    fetched_ttm_net_income = safe_float(market_row.get("net_income_ttm"))
    fetched_shares = safe_float(market_row.get("shares_outstanding"))
    fetched_revenue_growth = safe_float(market_row.get("revenue_growth_pct"))
    fetched_net_income_growth = safe_float(market_row.get("net_income_growth_pct"))

    latest_quarter_revenue = fetched_ttm_revenue / 4.0 if fetched_ttm_revenue is not None else None
    latest_quarter_net_income = fetched_ttm_net_income / 4.0 if fetched_ttm_net_income is not None else None

    if "scenario_inputs" not in st.session_state:
        st.session_state["scenario_inputs"] = {}

    scenario_defaults = get_scenario_defaults(fetched_revenue_growth, fetched_net_income_growth)

    if ticker not in st.session_state["scenario_inputs"]:
        st.session_state["scenario_inputs"][ticker] = {
            "latest_quarter_revenue": latest_quarter_revenue,
            "latest_quarter_net_income": latest_quarter_net_income,
            "shares_outstanding": fetched_shares,
            "notes": "",
            "redistribution_rules": get_default_redistribution_rules(),
            "bull": scenario_defaults["bull"],
            "base": scenario_defaults["base"],
            "bear": scenario_defaults["bear"],
        }
        save_persisted_scenarios()

    state = st.session_state["scenario_inputs"][ticker]

    if safe_float(state.get("latest_quarter_revenue")) is None and latest_quarter_revenue is not None:
        state["latest_quarter_revenue"] = latest_quarter_revenue
    if safe_float(state.get("latest_quarter_net_income")) is None and latest_quarter_net_income is not None:
        state["latest_quarter_net_income"] = latest_quarter_net_income
    if safe_float(state.get("shares_outstanding")) is None and fetched_shares is not None:
        state["shares_outstanding"] = fetched_shares

    if safe_float(state.get("latest_quarter_revenue")) is None and safe_float(state.get("current_revenue")) is not None:
        state["latest_quarter_revenue"] = safe_float(state.get("current_revenue")) / 4.0
    if safe_float(state.get("latest_quarter_net_income")) is None and safe_float(state.get("current_net_income")) is not None:
        state["latest_quarter_net_income"] = safe_float(state.get("current_net_income")) / 4.0

    state.pop("current_revenue", None)
    state.pop("current_net_income", None)
    state["redistribution_rules"] = ensure_redistribution_rules(state.get("redistribution_rules"))

    for scenario_name in SCENARIO_NAMES:
        state[scenario_name] = ensure_scenario_defaults(state.get(scenario_name), scenario_defaults[scenario_name])

    save_persisted_scenarios()


def load_bulk_import_dataframe(path: Path):
    expected_cols = [
        "ticker", "scenario",
        "latest_quarter_revenue_b", "latest_quarter_net_income_b", "shares_outstanding_b",
        "rev_y1", "rev_y2", "rev_y3", "ni_y1", "ni_y2", "ni_y3",
        "durable_growth_view", "growth_weight_pct", "notes",
    ]
    if not path.exists():
        return pd.DataFrame(columns=expected_cols)

    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=expected_cols)

    for col in expected_cols:
        if col not in df.columns:
            df[col] = None

    df = df[expected_cols].copy()
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["scenario"] = df["scenario"].astype(str).str.strip().str.lower()

    numeric_cols = [
        "latest_quarter_revenue_b", "latest_quarter_net_income_b", "shares_outstanding_b",
        "rev_y1", "rev_y2", "rev_y3", "ni_y1", "ni_y2", "ni_y3",
        "durable_growth_view", "growth_weight_pct",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df[df["ticker"] != ""]
    df = df[df["scenario"].isin(SCENARIO_NAMES)]
    return df.reset_index(drop=True)


def apply_bulk_import_overrides(df, valid_tickers):
    if df.empty:
        return 0

    applied = 0
    scenario_inputs = st.session_state.get("scenario_inputs", {})

    for _, row in df.iterrows():
        ticker = str(row["ticker"]).strip().upper()
        scenario_name = str(row["scenario"]).strip().lower()

        if ticker not in valid_tickers or ticker not in scenario_inputs or scenario_name not in SCENARIO_NAMES:
            continue

        state = scenario_inputs[ticker]

        latest_q_rev_b = safe_float(row.get("latest_quarter_revenue_b"))
        latest_q_ni_b = safe_float(row.get("latest_quarter_net_income_b"))
        shares_b = safe_float(row.get("shares_outstanding_b"))

        existing_latest_q_rev = safe_float(state.get("latest_quarter_revenue"))
        existing_latest_q_ni = safe_float(state.get("latest_quarter_net_income"))
        existing_shares = safe_float(state.get("shares_outstanding"))

        state["latest_quarter_revenue"] = existing_latest_q_rev if latest_q_rev_b is None else latest_q_rev_b * 1_000_000_000
        state["latest_quarter_net_income"] = existing_latest_q_ni if latest_q_ni_b is None else latest_q_ni_b * 1_000_000_000
        state["shares_outstanding"] = existing_shares if shares_b is None else shares_b * 1_000_000_000

        notes = row.get("notes")
        if isinstance(notes, str) and notes.strip():
            state["notes"] = notes.strip()

        scenario = state[scenario_name]
        rev_vals = [safe_float(row.get("rev_y1")), safe_float(row.get("rev_y2")), safe_float(row.get("rev_y3"))]
        ni_vals = [safe_float(row.get("ni_y1")), safe_float(row.get("ni_y2")), safe_float(row.get("ni_y3"))]

        if any(v is not None for v in rev_vals):
            scenario["rev_growth_rates"] = [scenario["rev_growth_rates"][i] if rev_vals[i] is None else rev_vals[i] for i in range(FORECAST_YEARS)]
        if any(v is not None for v in ni_vals):
            scenario["net_income_growth_rates"] = [scenario["net_income_growth_rates"][i] if ni_vals[i] is None else ni_vals[i] for i in range(FORECAST_YEARS)]

        durable_growth_view = safe_float(row.get("durable_growth_view"))
        growth_weight_pct = safe_float(row.get("growth_weight_pct"))
        if durable_growth_view is not None:
            scenario["durable_growth_view"] = durable_growth_view
        if growth_weight_pct is not None:
            scenario["growth_weight_pct"] = growth_weight_pct

        applied += 1

    st.session_state["scenario_inputs"] = scenario_inputs
    save_persisted_scenarios()
    return applied


def compute_target_weights(portfolio_summary, settings):
    df = portfolio_summary.copy()
    max_weight = safe_float(settings.get("max_position_weight_pct"), 10.0) / 100.0
    min_weight = safe_float(settings.get("min_position_weight_pct"), 0.0) / 100.0
    buy_hurdle = safe_float(settings.get("buy_hurdle_pct"), 15.0) / 100.0

    base_scores = {
        "Strong Buy": 5.0,
        "Buy": 3.0,
        "Speculative Buy": 2.0,
        "Hold / Watch": 1.0,
        "High Risk / Review": 0.5,
        "Consider Trim": 0.25,
        "Needs Input": 0.0,
    }

    include_mask = df["include_in_redistribution"].fillna(False).astype(bool)
    scores = []
    for _, row in df.iterrows():
        action = row.get("action")
        wcagr = safe_float(row.get("weighted_cagr_y3"))
        confidence = str(row.get("confidence", ""))
        include = bool(row.get("include_in_redistribution", False))

        if not include or confidence != "OK" or wcagr is None:
            scores.append(0.0)
            continue

        base_score = base_scores.get(action, 0.0)
        if wcagr < buy_hurdle and action in {"Strong Buy", "Buy", "Speculative Buy"}:
            base_score = max(base_score - 1.0, 0.0)
        rank_boost = max(wcagr, 0.0) * 2.0
        scores.append(max(base_score + rank_boost, 0.0))

    df["target_score"] = scores
    df["target_weight"] = 0.0

    total_score = df.loc[include_mask, "target_score"].sum()
    if total_score > 0:
        df.loc[include_mask, "target_weight"] = df.loc[include_mask, "target_score"] / total_score
        df.loc[include_mask, "target_weight"] = df.loc[include_mask, "target_weight"].clip(lower=min_weight, upper=max_weight)
        clipped_sum = df.loc[include_mask, "target_weight"].sum()
        if clipped_sum > 0:
            df.loc[include_mask, "target_weight"] = df.loc[include_mask, "target_weight"] / clipped_sum

    df.loc[~include_mask, "target_weight"] = 0.0
    return df


def build_action_queue(portfolio_summary, settings):
    df = portfolio_summary.copy()
    if df.empty:
        return df, {}

    df["market_value"] = df["market_value"].fillna(0.0)
    total_portfolio_value = df["market_value"].sum()

    if total_portfolio_value <= 0:
        return pd.DataFrame(), {
            "total_portfolio_value": 0.0,
            "redistribution_pool_value": 0.0,
            "total_buy_dollars": 0.0,
            "total_trim_dollars": 0.0,
            "review_count": 0,
            "action_count": 0,
        }

    df["current_weight"] = df["market_value"] / total_portfolio_value
    df["eligible_redistribution_shares"] = df.apply(
        lambda r: min(
            max(safe_float(r["eligible_redistribution_shares"], safe_float(r["shares"], 0.0)), 0.0),
            max(safe_float(r["shares"], 0.0), 0.0),
        ),
        axis=1,
    )
    df["eligible_redistribution_value"] = df.apply(
        lambda r: safe_float(r["eligible_redistribution_shares"], 0.0) * safe_float(r["price"], 0.0),
        axis=1,
    )

    included_mask = df["include_in_redistribution"].fillna(False).astype(bool)
    redistribution_pool_value = df.loc[included_mask, "eligible_redistribution_value"].sum()

    df = compute_target_weights(df, settings)

    rebalance_band = safe_float(settings.get("rebalance_band_pct"), 0.75) / 100.0
    min_trade_dollars = safe_float(settings.get("min_trade_dollars"), 1000.0)

    df["current_eligible_weight"] = 0.0
    if redistribution_pool_value > 0:
        df.loc[included_mask, "current_eligible_weight"] = df.loc[included_mask, "eligible_redistribution_value"] / redistribution_pool_value

    df["target_eligible_value"] = df["target_weight"] * redistribution_pool_value
    df["dollar_trade_unconstrained"] = df["target_eligible_value"] - df["eligible_redistribution_value"]
    df["shares_trade_unconstrained"] = df.apply(
        lambda r: (r["dollar_trade_unconstrained"] / r["price"]) if safe_float(r["price"]) not in (None, 0) else None,
        axis=1,
    )

    action_list = []
    reason_list = []
    shares_trade_list = []
    dollar_trade_list = []
    target_weight_effective_list = []

    for _, row in df.iterrows():
        include = bool(row.get("include_in_redistribution", False))
        confidence = str(row.get("confidence", ""))
        wcagr = safe_float(row.get("weighted_cagr_y3"))
        price = safe_float(row.get("price"))
        eligible_shares = safe_float(row.get("eligible_redistribution_shares"), 0.0)
        current_eligible_weight = safe_float(row.get("current_eligible_weight"), 0.0)
        target_weight = safe_float(row.get("target_weight"), 0.0)
        desired_shares_trade = safe_float(row.get("shares_trade_unconstrained"), 0.0)

        if not include:
            action_list.append("Excluded")
            reason_list.append("Not included in redistribution")
            shares_trade_list.append(0.0)
            dollar_trade_list.append(0.0)
            target_weight_effective_list.append(None)
            continue

        if confidence != "OK":
            action_list.append("Review")
            reason_list.append("Confidence not OK")
            shares_trade_list.append(0.0)
            dollar_trade_list.append(0.0)
            target_weight_effective_list.append(current_eligible_weight)
            continue

        if price is None or wcagr is None or redistribution_pool_value <= 0:
            action_list.append("Review")
            reason_list.append("Missing valuation inputs")
            shares_trade_list.append(0.0)
            dollar_trade_list.append(0.0)
            target_weight_effective_list.append(current_eligible_weight)
            continue

        if abs(target_weight - current_eligible_weight) < rebalance_band:
            action_list.append("Hold")
            reason_list.append("Within rebalance band")
            shares_trade_list.append(0.0)
            dollar_trade_list.append(0.0)
            target_weight_effective_list.append(current_eligible_weight)
            continue

        constrained_shares_trade = desired_shares_trade
        capped_reason = None
        if constrained_shares_trade < -eligible_shares:
            constrained_shares_trade = -eligible_shares
            capped_reason = "Limited by eligible redistribution shares"

        constrained_dollars = constrained_shares_trade * price
        effective_weight = current_eligible_weight + (constrained_dollars / redistribution_pool_value if redistribution_pool_value > 0 else 0.0)

        if abs(constrained_dollars) < min_trade_dollars:
            action_list.append("Hold")
            reason_list.append("Below minimum trade size" if not capped_reason else f"{capped_reason}; below minimum trade size")
            shares_trade_list.append(0.0)
            dollar_trade_list.append(0.0)
            target_weight_effective_list.append(current_eligible_weight)
            continue

        if constrained_shares_trade > 0:
            action = "Add"
            reason = "Below target redistribution weight"
        elif constrained_shares_trade < 0:
            action = "Trim"
            reason = "Above target redistribution weight"
        else:
            action = "Hold"
            reason = "No action"

        if capped_reason:
            reason = f"{reason}; {capped_reason}"

        action_list.append(action)
        reason_list.append(reason)
        shares_trade_list.append(constrained_shares_trade)
        dollar_trade_list.append(constrained_dollars)
        target_weight_effective_list.append(effective_weight)

    df["recommended_action"] = action_list
    df["reason"] = reason_list
    df["shares_trade"] = shares_trade_list
    df["dollar_trade"] = dollar_trade_list
    df["target_weight_effective"] = target_weight_effective_list

    action_rank_map = {"Add": 0, "Trim": 1, "Review": 2, "Excluded": 3, "Hold": 4}
    df["recommended_action_rank"] = df["recommended_action"].map(action_rank_map).fillna(99)

    action_queue = df[
        [
            "ticker", "price", "shares", "market_value", "current_weight",
            "eligible_redistribution_shares", "eligible_redistribution_value",
            "current_eligible_weight", "target_weight", "target_weight_effective",
            "dollar_trade", "shares_trade", "weighted_cagr_y3",
            "recommended_action", "recommended_action_rank", "reason",
            "action", "confidence", "include_in_redistribution",
        ]
    ].copy()

    action_queue = action_queue.sort_values(
        by=["recommended_action_rank", "weighted_cagr_y3", "dollar_trade"],
        ascending=[True, False, False],
        na_position="last",
    ).drop(columns=["recommended_action_rank"])

    summary = {
        "total_portfolio_value": total_portfolio_value,
        "redistribution_pool_value": redistribution_pool_value,
        "total_buy_dollars": action_queue[action_queue["recommended_action"] == "Add"]["dollar_trade"].sum(),
        "total_trim_dollars": -action_queue[action_queue["recommended_action"] == "Trim"]["dollar_trade"].sum(),
        "review_count": int((action_queue["recommended_action"] == "Review").sum()),
        "action_count": int((action_queue["recommended_action"].isin(["Add", "Trim"])).sum()),
    }
    return action_queue, summary


def render_growth_pe_table_editor(settings):
    with st.expander("Growth-to-P/E Lookup Table", expanded=False):
        st.caption(
            "This table maps your durable growth view to a growth-implied future P/E. "
            "The final future P/E is blended with the current PE anchor."
        )
        table_df = pd.DataFrame(settings.get("growth_pe_table", DEFAULT_GROWTH_PE_TABLE))
        if table_df.empty or "growth" not in table_df.columns or "pe" not in table_df.columns:
            table_df = pd.DataFrame(DEFAULT_GROWTH_PE_TABLE)

        edited = st.data_editor(
            table_df,
            num_rows="dynamic",
            width="stretch",
            hide_index=True,
            column_config={
                "growth": st.column_config.NumberColumn("Durable Growth View %", step=0.5, format="%.2f"),
                "pe": st.column_config.NumberColumn("Growth-Implied Future P/E", step=1.0, format="%.2f"),
            },
            key="growth_pe_table_editor",
        )

        cleaned = []
        for _, row in edited.iterrows():
            growth = safe_float(row.get("growth"))
            pe = safe_float(row.get("pe"))
            if growth is not None and pe is not None:
                cleaned.append({"growth": growth, "pe": pe})

        if cleaned:
            settings["growth_pe_table"] = sorted(cleaned, key=lambda x: x["growth"])

        if st.button("Reset lookup table to defaults", use_container_width=True):
            settings["growth_pe_table"] = DEFAULT_GROWTH_PE_TABLE
            st.session_state["global_settings"] = settings
            save_global_settings()
            st.rerun()


def render_action_settings():
    settings = st.session_state["global_settings"]

    with st.expander("Action Settings", expanded=False):
        st.caption("These settings control ranking and redistribution actions.")

        c1, c2, c3 = st.columns(3)
        settings["target_cagr_threshold_pct"] = c1.number_input("Target CAGR Threshold (%)", value=float(safe_float(settings.get("target_cagr_threshold_pct"), 15.0)), step=0.5, format="%.2f")
        settings["strong_buy_base_premium_pct"] = c2.number_input("Strong Buy Base Premium (%)", value=float(safe_float(settings.get("strong_buy_base_premium_pct"), 15.0)), step=0.5, format="%.2f")
        settings["buy_base_premium_pct"] = c3.number_input("Buy Base Premium (%)", value=float(safe_float(settings.get("buy_base_premium_pct"), 3.0)), step=0.5, format="%.2f")

        c4, c5, c6 = st.columns(3)
        settings["buy_bear_buffer_pct"] = c4.number_input("Buy Bear Buffer (%)", value=float(safe_float(settings.get("buy_bear_buffer_pct"), 5.0)), step=0.5, format="%.2f")
        settings["risk_base_shortfall_pct"] = c5.number_input("Risk Base Shortfall (%)", value=float(safe_float(settings.get("risk_base_shortfall_pct"), 5.0)), step=0.5, format="%.2f")
        settings["speculative_bull_premium_pct"] = c6.number_input("Speculative Bull Premium (%)", value=float(safe_float(settings.get("speculative_bull_premium_pct"), 50.0)), step=1.0, format="%.2f")

        c7, c8, c9 = st.columns(3)
        settings["buy_hurdle_pct"] = c7.number_input("Buy Hurdle (%)", value=float(safe_float(settings.get("buy_hurdle_pct"), 15.0)), step=0.5, format="%.2f")
        settings["rebalance_band_pct"] = c8.number_input("Rebalance Band (%)", value=float(safe_float(settings.get("rebalance_band_pct"), 0.75)), step=0.25, format="%.2f")
        settings["min_trade_dollars"] = c9.number_input("Minimum Trade Size ($)", value=float(safe_float(settings.get("min_trade_dollars"), 1000.0)), step=500.0, format="%.2f")

        c10, c11 = st.columns(2)
        settings["max_position_weight_pct"] = c10.number_input("Max Position Weight (%)", value=float(safe_float(settings.get("max_position_weight_pct"), 10.0)), step=0.5, format="%.2f")
        settings["portfolio_table_height"] = int(c11.number_input("Portfolio Table Height (px)", value=int(safe_float(settings.get("portfolio_table_height"), 800)), step=50, min_value=300, max_value=1400))

        available_for_pinning = sorted(all_tickers) if "all_tickers" in globals() else []
        settings["bottom_pinned_tickers"] = st.multiselect(
            "Send these tickers to the bottom",
            available_for_pinning,
            default=[x for x in settings.get("bottom_pinned_tickers", []) if x in available_for_pinning],
        )

    render_growth_pe_table_editor(settings)
    st.session_state["global_settings"] = settings
    save_global_settings()


def render_bulk_import_section():
    st.markdown('<div class="section-title">Bulk Import</div>', unsafe_allow_html=True)
    st.caption(f"Expected file: `{BULK_IMPORT_FILE}`")

    if bulk_import_df.empty:
        st.info("No bulk import file found, or the file is empty.")
        return

    st.caption("Expected columns: ticker, scenario, latest_quarter_revenue_b, latest_quarter_net_income_b, shares_outstanding_b, rev_y1, rev_y2, rev_y3, ni_y1, ni_y2, ni_y3, durable_growth_view, growth_weight_pct, notes")
    st.caption("Blank latest-quarter revenue, net income, or shares fields will preserve the existing saved values instead of clearing them.")
    st.dataframe(bulk_import_df, width="stretch", hide_index=True, height=280)

    st.warning("Applying the file will overwrite imported fields for matching ticker/scenario rows.")
    if st.button("Apply bulk import file", use_container_width=True):
        applied = apply_bulk_import_overrides(bulk_import_df, set(all_tickers))
        st.session_state["bulk_import_message"] = f"Applied {applied} imported scenario row(s)."
        st.rerun()


def render_reset_menu(ticker=None, context_key="global"):
    with st.expander("Advanced actions", expanded=False):
        if ticker:
            st.warning(f"Resetting saved inputs for {ticker} cannot be undone.")
            confirm = st.checkbox(f"I understand and want to reset {ticker}", key=f"confirm_reset_{context_key}_{ticker}")
            if st.button(
                f"Reset saved inputs for {ticker}",
                key=f"reset_{context_key}_{ticker}",
                use_container_width=True,
                disabled=not confirm,
            ):
                if ticker in st.session_state.get("scenario_inputs", {}):
                    del st.session_state["scenario_inputs"][ticker]
                    save_persisted_scenarios()
                    st.rerun()


def render_scenario_block(title, key_prefix, color_class, current_price):
    settings = st.session_state["global_settings"]
    growth_pe_table = settings.get("growth_pe_table", DEFAULT_GROWTH_PE_TABLE)

    state = st.session_state["scenario_inputs"][st.session_state["selected_ticker"]]
    scenario = state[key_prefix]

    st.markdown(f'<div class="scenario-header {color_class}">{title}</div>', unsafe_allow_html=True)
    input_col, table_col = st.columns([1.0, 1.8])

    with input_col:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        growth_years = [f"Year {i}" for i in range(1, FORECAST_YEARS + 1)]

        st.markdown("**Revenue Growth (%)**")
        rg_cols = st.columns(FORECAST_YEARS)
        rev_growth_rates = []
        for i in range(FORECAST_YEARS):
            rev_growth_rates.append(
                rg_cols[i].number_input(
                    growth_years[i],
                    value=float(safe_float(scenario["rev_growth_rates"][i], 0.00)),
                    step=0.01,
                    format="%.2f",
                    key=f"{key_prefix}_rev_{i}",
                )
            )

        st.markdown("**Net Income / EPS Growth (%)**")
        nig_cols = st.columns(FORECAST_YEARS)
        net_income_growth_rates = []
        for i in range(FORECAST_YEARS):
            net_income_growth_rates.append(
                nig_cols[i].number_input(
                    growth_years[i],
                    value=float(safe_float(scenario["net_income_growth_rates"][i], 0.00)),
                    step=0.01,
                    format="%.2f",
                    key=f"{key_prefix}_net_income_growth_{i}",
                )
            )

        st.markdown("**Future Multiple Assumptions**")
        durable_growth_view = st.number_input(
            "Durable Growth View (%)",
            value=float(safe_float(scenario.get("durable_growth_view"), 0.00)),
            step=0.01,
            format="%.2f",
            key=f"{key_prefix}_durable_growth_view",
            help="Maps to a growth-implied future P/E using the lookup table.",
        )
        growth_weight_pct = st.number_input(
            "Weight on Growth View (%)",
            value=float(safe_float(scenario.get("growth_weight_pct"), 0.00)),
            step=1.0,
            min_value=0.0,
            max_value=100.0,
            format="%.2f",
            key=f"{key_prefix}_growth_weight_pct",
            help="0% means use current PE only. 100% means use growth-implied PE only.",
        )

        growth_implied_pe_preview = growth_to_pe_lookup(durable_growth_view, growth_pe_table)
        latest_q_ni = safe_float(state.get("latest_quarter_net_income"))
        shares = safe_float(state.get("shares_outstanding"))
        current_annual_ni = quarterly_run_rate_to_annual(latest_q_ni)
        current_eps = current_annual_ni / shares if current_annual_ni is not None and shares not in (None, 0) else None
        current_pe = current_price / current_eps if current_price is not None and current_eps not in (None, 0) else None

        blended_preview = None
        if growth_implied_pe_preview is not None and current_pe is not None:
            w = safe_float(growth_weight_pct, 0.0) / 100.0
            blended_preview = w * growth_implied_pe_preview + (1 - w) * current_pe
        elif growth_implied_pe_preview is not None:
            blended_preview = growth_implied_pe_preview
        elif current_pe is not None:
            blended_preview = current_pe

        st.caption(f"Growth-implied future P/E: {fmt_num(growth_implied_pe_preview)}x")
        st.caption(f"Current PE anchor: {fmt_num(current_pe)}x")
        st.caption(f"Blended future P/E: {fmt_num(blended_preview)}x")
        st.caption("Behind the scenes, revenue and earnings compound quarter by quarter from the latest quarter input.")
        st.markdown("</div>", unsafe_allow_html=True)

    with table_col:
        matrix, summary = build_scenario_matrix(
            current_price=current_price,
            latest_quarter_revenue=state["latest_quarter_revenue"],
            latest_quarter_net_income=state["latest_quarter_net_income"],
            shares_outstanding=state["shares_outstanding"],
            rev_growth_rates=rev_growth_rates,
            net_income_growth_rates=net_income_growth_rates,
            durable_growth_view=durable_growth_view,
            growth_weight_pct=growth_weight_pct,
            growth_pe_table=growth_pe_table,
        )

        st.markdown('<div class="card">', unsafe_allow_html=True)
        if matrix is None:
            st.warning("Base inputs incomplete. Enter latest quarter revenue, latest quarter net income, and shares outstanding.")
        else:
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Price (Y3)", fmt_money(summary["price_y3"]))
            s2.metric("CAGR (3Y)", fmt_pct(summary["cagr_y3"] * 100 if summary["cagr_y3"] is not None else None))
            s3.metric("Blended Future P/E", fmt_num(summary["blended_future_pe"]))
            s4.metric("Confidence", summary["confidence_flag"])
            st.dataframe(format_matrix(matrix), width="stretch")
        st.markdown("</div>", unsafe_allow_html=True)

    scenario["rev_growth_rates"] = rev_growth_rates
    scenario["net_income_growth_rates"] = net_income_growth_rates
    scenario["durable_growth_view"] = durable_growth_view
    scenario["growth_weight_pct"] = growth_weight_pct
    save_persisted_scenarios()


st.markdown(
    """
    <style>
    .stApp {
        background: linear-gradient(180deg, #0f1117 0%, #141b27 100%);
        color: #f5f7fb;
    }
    .main-title {
        font-size: 2.35rem;
        font-weight: 800;
        margin-bottom: 0.10rem;
    }
    .subtle {
        color: #d2d9e6;
        margin-bottom: 1.00rem;
        line-height: 1.4;
    }
    .section-title {
        font-size: 1.25rem;
        font-weight: 700;
        margin: 1.00rem 0 0.50rem 0;
    }
    .card {
        background: #171c25;
        border: 1px solid #2a3140;
        border-radius: 18px;
        padding: 16px 16px 8px 16px;
        margin-bottom: 14px;
        box-shadow: 0 10px 24px rgba(0,0,0,0.25);
    }
    .scenario-header {
        border-radius: 10px;
        padding: 8px 14px;
        font-size: 1.15rem;
        font-weight: 800;
        text-align: center;
        margin: 12px 0 8px 0;
        color: white;
    }
    .bull { background: linear-gradient(90deg, #0a9d41, #1cc95c); }
    .base { background: linear-gradient(90deg, #1c41ff, #4c6fff); }
    .bear { background: linear-gradient(90deg, #8a2a00, #d26a00); }
    .chip-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin: 0.75rem 0 1rem 0;
    }
    .chip {
        background: #171c25;
        border: 1px solid #2a3140;
        border-radius: 999px;
        padding: 8px 12px;
        font-size: 0.95rem;
        color: #f5f7fb;
    }
    .fetched-label {
        color: #9fb0c8;
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 0.35rem;
    }
    .input-section-title {
        font-size: 1.05rem;
        font-weight: 700;
        margin-bottom: 0.75rem;
    }
    div[data-testid="stMetric"] {
        background: #171c25;
        border: 1px solid #2a3140;
        padding: 10px 14px;
        border-radius: 16px;
    }
    div[data-testid="stMetricLabel"] {
        color: #d8deea !important;
        opacity: 1 !important;
    }
    div[data-testid="stDataFrame"] {
        border-radius: 16px;
        overflow: hidden;
    }
    label, .stNumberInput label, .stSelectbox label, .stCheckbox label {
        color: #f5f7fb !important;
        font-weight: 600 !important;
        opacity: 1 !important;
    }
    div[data-baseweb="select"] * {
        color: #111827 !important;
    }
    input, textarea {
        color: #111827 !important;
    }
    button[kind="secondary"], button[kind="primary"] {
        border-radius: 12px !important;
        font-weight: 700 !important;
    }
    .stButton > button {
        background: #1f2937 !important;
        color: #f9fafb !important;
        border: 1px solid #475569 !important;
    }
    .stButton > button:hover {
        background: #273449 !important;
        color: #ffffff !important;
    }
    div[data-testid="stDataFrame"] table {
        font-size: 0.95rem !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if "global_settings" not in st.session_state:
    st.session_state["global_settings"] = load_global_settings()

settings = st.session_state["global_settings"]

tickers_config = load_yaml_file(TICKERS_FILE, {"portfolio": [], "watchlist": []})
portfolio_df = normalize_portfolio(tickers_config.get("portfolio", []))
portfolio_tickers = portfolio_df["ticker"].tolist()
portfolio_set = set(portfolio_tickers)
watchlist = normalize_watchlist(tickers_config.get("watchlist", []), portfolio_set)
all_tickers = sorted(set(portfolio_tickers).union(set(watchlist)))
bulk_import_df = load_bulk_import_dataframe(BULK_IMPORT_FILE)

st.markdown('<div class="main-title">Stock Monitor</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtle">Latest-quarter-input model with quarter-by-quarter compounding, a blended future P/E using growth view + current PE anchor, optional CSV bulk import, and redistribution controls.</div>',
    unsafe_allow_html=True,
)

top_a, top_b, _ = st.columns([1, 1, 4])
with top_a:
    if st.button("Refresh Data", use_container_width=True):
        st.session_state["force_refresh"] = True
        st.rerun()
with top_b:
    st.caption("Cache: 12h")

if not all_tickers:
    st.warning("No tickers found in config/tickers.yaml")
    st.stop()

if "scenario_inputs" not in st.session_state:
    st.session_state["scenario_inputs"] = load_persisted_scenarios()
if "selected_ticker" not in st.session_state:
    st.session_state["selected_ticker"] = None
if "force_refresh" not in st.session_state:
    st.session_state["force_refresh"] = False

market_df = get_live_market_data(
    all_tickers,
    force_refresh=st.session_state["force_refresh"],
    max_age_hours=12,
).copy()
st.session_state["force_refresh"] = False

for ticker in all_tickers:
    row_match = market_df[market_df["ticker"] == ticker]
    row_dict = row_match.iloc[0].to_dict() if not row_match.empty else {}
    init_ticker_state(ticker, row_dict)

portfolio_shares_map = dict(zip(portfolio_df["ticker"], portfolio_df["shares"]))

summary_rows = []
growth_pe_table = settings.get("growth_pe_table", DEFAULT_GROWTH_PE_TABLE)
bottom_pinned_tickers = settings.get("bottom_pinned_tickers", [])

for ticker in all_tickers:
    row_match = market_df[market_df["ticker"] == ticker]
    row = row_match.iloc[0].to_dict() if not row_match.empty else {}
    state = st.session_state["scenario_inputs"][ticker]

    scenario_summaries = {}
    for scenario_name in SCENARIO_NAMES:
        scenario = state[scenario_name]
        _, scenario_summary = build_scenario_matrix(
            current_price=safe_float(row.get("price")),
            latest_quarter_revenue=state["latest_quarter_revenue"],
            latest_quarter_net_income=state["latest_quarter_net_income"],
            shares_outstanding=state["shares_outstanding"],
            rev_growth_rates=scenario["rev_growth_rates"],
            net_income_growth_rates=scenario["net_income_growth_rates"],
            durable_growth_view=scenario["durable_growth_view"],
            growth_weight_pct=scenario["growth_weight_pct"],
            growth_pe_table=growth_pe_table,
        )
        scenario_summaries[scenario_name] = scenario_summary or {}

    bear_summary = scenario_summaries["bear"]
    base_summary = scenario_summaries["base"]
    bull_summary = scenario_summaries["bull"]

    action = classify_action(
        bear_cagr=bear_summary.get("cagr_y3"),
        base_cagr=base_summary.get("cagr_y3"),
        bull_cagr=bull_summary.get("cagr_y3"),
        threshold_pct=settings.get("target_cagr_threshold_pct"),
        strong_buy_base_premium_pct=settings.get("strong_buy_base_premium_pct"),
        buy_base_premium_pct=settings.get("buy_base_premium_pct"),
        buy_bear_buffer_pct=settings.get("buy_bear_buffer_pct"),
        risk_base_shortfall_pct=settings.get("risk_base_shortfall_pct"),
        speculative_bull_premium_pct=settings.get("speculative_bull_premium_pct"),
    )

    confidence_values = [bear_summary.get("confidence_flag"), base_summary.get("confidence_flag"), bull_summary.get("confidence_flag")]
    confidence = "OK" if all(x == "OK" for x in confidence_values) else "Review Assumptions"

    redistribution_rules = ensure_redistribution_rules(state.get("redistribution_rules"))
    owned_shares = safe_float(portfolio_shares_map.get(ticker), 0.0)
    eligible_shares = safe_float(redistribution_rules.get("eligible_redistribution_shares"), owned_shares)
    eligible_shares = min(max(eligible_shares, 0.0), owned_shares)

    summary_rows.append(
        {
            "ticker": ticker,
            "price": safe_float(row.get("price")),
            "shares": owned_shares,
            "bear_price_y3": bear_summary.get("price_y3"),
            "base_price_y3": base_summary.get("price_y3"),
            "bull_price_y3": bull_summary.get("price_y3"),
            "bear_cagr_y3": bear_summary.get("cagr_y3"),
            "base_cagr_y3": base_summary.get("cagr_y3"),
            "bull_cagr_y3": bull_summary.get("cagr_y3"),
            "weighted_cagr_y3": weighted_cagr(bear_summary.get("cagr_y3"), base_summary.get("cagr_y3"), bull_summary.get("cagr_y3")),
            "confidence": confidence,
            "action": action,
            "action_rank": action_sort_rank(action),
            "bottom_pinned": is_bottom_pinned_ticker(ticker, bottom_pinned_tickers),
            "status": row.get("status"),
            "cache_source": row.get("cache_source"),
            "include_in_redistribution": redistribution_rules["include_in_redistribution"],
            "eligible_redistribution_shares": eligible_shares,
        }
    )

summary_df = pd.DataFrame(summary_rows)
failed_df = summary_df[~summary_df["status"].astype(str).str.startswith("OK")].copy()


def format_cagr_value(value):
    return "—" if value is None or pd.isna(value) else f"{float(value) * 100:.2f}%"


def open_stock_detail(ticker):
    ticker = str(ticker).strip().upper()
    if not ticker:
        return
    st.session_state["selected_ticker"] = ticker
    st.session_state["active_tab"] = "Stock Detail"
    st.session_state["nav_version"] = st.session_state.get("nav_version", 0) + 1
    st.rerun()


def get_selected_row_from_event(event):
    if event is None:
        return None
    selection = getattr(event, "selection", None)
    rows = getattr(selection, "rows", None) if selection is not None else None
    if rows is None and isinstance(event, dict):
        rows = event.get("selection", {}).get("rows")
    if rows:
        return rows[0]
    return None


def render_clickable_dataframe(view_df, styled_df, height, key):
    try:
        event = st.dataframe(
            styled_df,
            width="stretch",
            hide_index=True,
            height=height,
            key=key,
            on_select="rerun",
            selection_mode="single-row",
        )
        selected_idx = get_selected_row_from_event(event)
        if selected_idx is not None and 0 <= selected_idx < len(view_df):
            return view_df.iloc[selected_idx].get("Ticker")
    except TypeError:
        st.dataframe(styled_df, width="stretch", hide_index=True, height=height, key=key)
    return None


def build_portfolio_summary():
    if not portfolio_tickers:
        return None

    portfolio_summary = portfolio_df.merge(
        summary_df.drop(columns=["shares"], errors="ignore"),
        on="ticker",
        how="left",
    )

    portfolio_summary["market_value"] = portfolio_summary.apply(
        lambda r: safe_float(r["shares"]) * safe_float(r["price"])
        if safe_float(r["shares"]) is not None and safe_float(r["price"]) is not None
        else None,
        axis=1,
    )

    portfolio_summary = portfolio_summary.sort_values(
        by=["bottom_pinned", "action_rank", "weighted_cagr_y3"],
        ascending=[True, True, False],
        na_position="last",
    )
    return portfolio_summary


def build_portfolio_view():
    portfolio_summary = build_portfolio_summary()
    if portfolio_summary is None:
        return None

    return pd.DataFrame(
        {
            "Ticker": portfolio_summary["ticker"],
            "Shares": portfolio_summary["shares"].apply(fmt_num),
            "Price": portfolio_summary["price"].apply(fmt_money),
            "Total Value": portfolio_summary["market_value"].apply(fmt_money),
            "Bear Price (Y3)": portfolio_summary["bear_price_y3"].apply(fmt_money),
            "Base Price (Y3)": portfolio_summary["base_price_y3"].apply(fmt_money),
            "Bull Price (Y3)": portfolio_summary["bull_price_y3"].apply(fmt_money),
            "Bear CAGR (3Y)": portfolio_summary["bear_cagr_y3"].apply(format_cagr_value),
            "Base CAGR (3Y)": portfolio_summary["base_cagr_y3"].apply(format_cagr_value),
            "Bull CAGR (3Y)": portfolio_summary["bull_cagr_y3"].apply(format_cagr_value),
            "Confidence": portfolio_summary["confidence"],
            "Action": portfolio_summary["action"],
        }
    )


def build_watchlist_view():
    if not watchlist:
        return None

    watchlist_summary = summary_df[summary_df["ticker"].isin(watchlist)].copy()
    watchlist_summary = watchlist_summary.sort_values(
        by=["bottom_pinned", "action_rank", "weighted_cagr_y3"],
        ascending=[True, True, False],
        na_position="last",
    )

    return pd.DataFrame(
        {
            "Ticker": watchlist_summary["ticker"],
            "Price": watchlist_summary["price"].apply(fmt_money),
            "Bear Price (Y3)": watchlist_summary["bear_price_y3"].apply(fmt_money),
            "Base Price (Y3)": watchlist_summary["base_price_y3"].apply(fmt_money),
            "Bull Price (Y3)": watchlist_summary["bull_price_y3"].apply(fmt_money),
            "Bear CAGR (3Y)": watchlist_summary["bear_cagr_y3"].apply(format_cagr_value),
            "Base CAGR (3Y)": watchlist_summary["base_cagr_y3"].apply(format_cagr_value),
            "Bull CAGR (3Y)": watchlist_summary["bull_cagr_y3"].apply(format_cagr_value),
            "Confidence": watchlist_summary["confidence"],
            "Action": watchlist_summary["action"],
        }
    )


def render_action_queue():
    portfolio_summary = build_portfolio_summary()
    if portfolio_summary is None or portfolio_summary.empty:
        return

    action_queue, summary = build_action_queue(portfolio_summary, settings)

    st.markdown('<div class="section-title">Action Queue</div>', unsafe_allow_html=True)

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Redistribution Pool $", fmt_money(summary["redistribution_pool_value"]))
    s2.metric("Total Buy $", fmt_money(summary["total_buy_dollars"]))
    s3.metric("Total Trim $", fmt_money(summary["total_trim_dollars"]))
    s4.metric("Trade Actions", fmt_num(summary["action_count"]))

    queue_view = pd.DataFrame(
        {
            "Ticker": action_queue["ticker"],
            "Recommended Action": action_queue["recommended_action"],
            "Reason": action_queue["reason"],
            "Eligible Shares": action_queue["eligible_redistribution_shares"].apply(fmt_num),
            "Current Redist Weight": action_queue["current_eligible_weight"].apply(lambda x: fmt_pct(x * 100 if x is not None else None)),
            "Target Redist Weight": action_queue["target_weight_effective"].apply(lambda x: fmt_pct(x * 100 if x is not None else None)),
            "Dollar Trade": action_queue["dollar_trade"].apply(fmt_money),
            "Est. Shares": action_queue["shares_trade"].apply(fmt_num),
            "Current Action": action_queue["action"],
            "Confidence": action_queue["confidence"],
        }
    )

    def queue_action_color(val):
        if val == "Add":
            return "background-color: #064E3B; color: #A7F3D0; font-weight: 800;"
        if val == "Trim":
            return "background-color: #4A1D1D; color: #FCA5A5; font-weight: 800;"
        if val == "Review":
            return "background-color: #3A3417; color: #FDE68A; font-weight: 800;"
        if val == "Excluded":
            return "background-color: #312E81; color: #C7D2FE; font-weight: 800;"
        return "background-color: #1F2937; color: #E5E7EB; font-weight: 700;"

    styled_queue = (
        queue_view.style
        .map(queue_action_color, subset=["Recommended Action"])
        .map(action_color, subset=["Current Action"])
        .map(confidence_color, subset=["Confidence"])
    )
    st.dataframe(styled_queue, width="stretch", hide_index=True, height=420)


def render_redistribution_tab():
    st.markdown('<div class="section-title">Redistribution</div>', unsafe_allow_html=True)
    if not portfolio_tickers:
        st.info("No portfolio positions found.")
        return

    rows = []
    for ticker in portfolio_tickers:
        state = st.session_state["scenario_inputs"][ticker]
        rules = ensure_redistribution_rules(state.get("redistribution_rules"))
        owned = safe_float(portfolio_shares_map.get(ticker), 0.0)
        eligible = safe_float(rules.get("eligible_redistribution_shares"), owned)
        eligible = min(max(eligible, 0.0), owned)

        summary_row = summary_df[summary_df["ticker"] == ticker]
        weighted = None if summary_row.empty else safe_float(summary_row.iloc[0]["weighted_cagr_y3"])
        confidence = "—" if summary_row.empty else summary_row.iloc[0]["confidence"]
        action = "—" if summary_row.empty else summary_row.iloc[0]["action"]

        rows.append(
            {
                "Ticker": ticker,
                "Shares Owned": owned,
                "Include in Redistribution": bool(rules.get("include_in_redistribution", False)),
                "Eligible Redistribution Shares": eligible,
                "Locked Shares": max(owned - eligible, 0.0),
                "Weighted CAGR (3Y)": None if weighted is None else weighted * 100.0,
                "Current Action": action,
                "Confidence": confidence,
            }
        )

    table_df = pd.DataFrame(rows)

    c1, c2, c3 = st.columns(3)
    if c1.button("Exclude All", use_container_width=True):
        for ticker in portfolio_tickers:
            st.session_state["scenario_inputs"][ticker]["redistribution_rules"] = ensure_redistribution_rules(
                {
                    "include_in_redistribution": False,
                    "eligible_redistribution_shares": safe_float(portfolio_shares_map.get(ticker), 0.0),
                }
            )
        save_persisted_scenarios()
        st.rerun()

    if c2.button("Include All", use_container_width=True):
        for ticker in portfolio_tickers:
            st.session_state["scenario_inputs"][ticker]["redistribution_rules"] = ensure_redistribution_rules(
                {
                    "include_in_redistribution": True,
                    "eligible_redistribution_shares": safe_float(portfolio_shares_map.get(ticker), 0.0),
                }
            )
        save_persisted_scenarios()
        st.rerun()

    if c3.button("Set Eligible = Owned", use_container_width=True):
        for ticker in portfolio_tickers:
            rules = ensure_redistribution_rules(st.session_state["scenario_inputs"][ticker].get("redistribution_rules"))
            if rules["include_in_redistribution"]:
                rules["eligible_redistribution_shares"] = safe_float(portfolio_shares_map.get(ticker), 0.0)
                st.session_state["scenario_inputs"][ticker]["redistribution_rules"] = ensure_redistribution_rules(rules)
        save_persisted_scenarios()
        st.rerun()

    edited = st.data_editor(
        table_df,
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        key="redistribution_editor",
        column_config={
            "Ticker": st.column_config.TextColumn(disabled=True),
            "Shares Owned": st.column_config.NumberColumn(disabled=True, format="%.2f"),
            "Include in Redistribution": st.column_config.CheckboxColumn(),
            "Eligible Redistribution Shares": st.column_config.NumberColumn(format="%.2f", min_value=0.0, step=1.0),
            "Locked Shares": st.column_config.NumberColumn(disabled=True, format="%.2f"),
            "Weighted CAGR (3Y)": st.column_config.NumberColumn(disabled=True, format="%.2f%%"),
            "Current Action": st.column_config.TextColumn(disabled=True),
            "Confidence": st.column_config.TextColumn(disabled=True),
        },
    )

    if st.button("Save Redistribution Table", use_container_width=True):
        for _, row in edited.iterrows():
            ticker = str(row["Ticker"]).strip().upper()
            owned = safe_float(row["Shares Owned"], 0.0)
            include = bool(row["Include in Redistribution"])
            eligible = min(max(safe_float(row["Eligible Redistribution Shares"], 0.0), 0.0), owned)

            current = st.session_state["scenario_inputs"][ticker]
            current["redistribution_rules"] = ensure_redistribution_rules(
                {
                    "include_in_redistribution": include,
                    "eligible_redistribution_shares": eligible,
                }
            )
        save_persisted_scenarios()
        st.success("Redistribution settings saved.")
        st.rerun()

    st.caption("Unchecked rows are ignored by the redistribution engine. Eligible shares are the only shares allowed to participate.")


def render_portfolio_tab():
    st.markdown('<div class="section-title">Portfolio</div>', unsafe_allow_html=True)
    if not portfolio_tickers:
        st.info("No portfolio positions found.")
        return

    render_action_queue()

    portfolio_view = build_portfolio_view()
    styled_portfolio = portfolio_view.style.map(action_color, subset=["Action"]).map(confidence_color, subset=["Confidence"])

    clicked_ticker = render_clickable_dataframe(
        portfolio_view,
        styled_portfolio,
        height=int(safe_float(settings.get("portfolio_table_height"), 800)),
        key="portfolio_clickable_table",
    )
    if clicked_ticker:
        open_stock_detail(clicked_ticker)

    p1, p2 = st.columns([4, 1])
    selected_portfolio_ticker = p1.selectbox("Open portfolio ticker", options=portfolio_tickers, index=0, key="portfolio_select")
    if p2.button("Open", key="open_portfolio_ticker", use_container_width=True):
        open_stock_detail(selected_portfolio_ticker)

    render_reset_menu(selected_portfolio_ticker, context_key="portfolio")


def render_watchlist_tab():
    st.markdown('<div class="section-title">Watchlist</div>', unsafe_allow_html=True)
    if not watchlist:
        st.info("No watchlist names remain after removing portfolio duplicates.")
        return

    watchlist_view = build_watchlist_view()
    styled_watchlist = watchlist_view.style.map(action_color, subset=["Action"]).map(confidence_color, subset=["Confidence"])

    clicked_ticker = render_clickable_dataframe(
        watchlist_view,
        styled_watchlist,
        height=int(safe_float(settings.get("watchlist_table_height"), 650)),
        key="watchlist_clickable_table",
    )
    if clicked_ticker:
        open_stock_detail(clicked_ticker)

    w1, w2 = st.columns([4, 1])
    selected_watchlist_ticker = w1.selectbox("Open watchlist ticker", options=watchlist, index=0, key="watchlist_select")
    if w2.button("Open", key="open_watchlist_ticker", use_container_width=True):
        open_stock_detail(selected_watchlist_ticker)

    render_reset_menu(selected_watchlist_ticker, context_key="watchlist")


def render_stock_detail_tab():
    st.markdown('<div class="section-title">Stock Detail</div>', unsafe_allow_html=True)
    if not all_tickers:
        st.warning("No tickers found in config/tickers.yaml")
        return

    selected_current = st.session_state.get("selected_ticker")
    if selected_current not in all_tickers:
        selected_current = all_tickers[0]
        st.session_state["selected_ticker"] = selected_current

    ticker_selector_col, _ = st.columns([1.5, 4])
    ticker = ticker_selector_col.selectbox("Selected stock", options=all_tickers, index=all_tickers.index(selected_current), key="stock_detail_ticker_select")

    if ticker != st.session_state.get("selected_ticker"):
        st.session_state["selected_ticker"] = ticker
        st.rerun()

    market_match = market_df[market_df["ticker"] == ticker]
    market_row = market_match.iloc[0].to_dict() if not market_match.empty else {}
    state = st.session_state["scenario_inputs"][ticker]

    annualized_revenue = quarterly_run_rate_to_annual(state.get("latest_quarter_revenue"))
    annualized_net_income = quarterly_run_rate_to_annual(state.get("latest_quarter_net_income"))
    annualized_eps = annualized_net_income / safe_float(state.get("shares_outstanding")) if safe_float(state.get("shares_outstanding")) not in (None, 0) and annualized_net_income is not None else None
    current_price = safe_float(market_row.get("price"))
    current_pe = current_price / annualized_eps if current_price is not None and annualized_eps not in (None, 0) else None
    current_position_shares = safe_float(portfolio_shares_map.get(ticker), 0.0)

    st.markdown(f'<div class="main-title">{ticker}</div>', unsafe_allow_html=True)
    st.markdown('<div class="fetched-label">Reference Data</div>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="chip-row">
            <div class="chip">Price: {fmt_money(market_row.get("price"))}</div>
            <div class="chip">Finnhub TTM Revenue: {fmt_billions(market_row.get("ttm_revenue"))}</div>
            <div class="chip">Finnhub TTM Net Income: {fmt_billions(market_row.get("net_income_ttm"))}</div>
            <div class="chip">Finnhub Rev Growth: {fmt_pct(market_row.get("revenue_growth_pct"))}</div>
            <div class="chip">Finnhub NI Growth: {fmt_pct(market_row.get("net_income_growth_pct"))}</div>
            <div class="chip">Current PE: {fmt_num(current_pe)}x</div>
            <div class="chip">Position Shares: {fmt_num(current_position_shares)}</div>
            <div class="chip">Status: {market_row.get("status", "—")}</div>
            <div class="chip">Cache: {market_row.get("cache_source", "—")}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if state.get("notes"):
        st.caption(f"Notes: {state.get('notes')}")

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="input-section-title">Starting Point: Latest Quarter Input</div>', unsafe_allow_html=True)

    base_cols = st.columns(3)
    state["latest_quarter_revenue"] = display_number_input(base_cols[0], "Latest Quarter Revenue ($B)", state["latest_quarter_revenue"], key=f"{ticker}_latest_q_rev_b", scale=1_000_000_000)
    state["latest_quarter_net_income"] = display_number_input(base_cols[1], "Latest Quarter Net Income ($B)", state["latest_quarter_net_income"], key=f"{ticker}_latest_q_ni_b", scale=1_000_000_000)
    state["shares_outstanding"] = display_number_input(base_cols[2], "Shares Outstanding (B)", state["shares_outstanding"], key=f"{ticker}_shares_outstanding_b", scale=1_000_000_000)
    save_persisted_scenarios()

    annualized_revenue = quarterly_run_rate_to_annual(state.get("latest_quarter_revenue"))
    annualized_net_income = quarterly_run_rate_to_annual(state.get("latest_quarter_net_income"))
    annualized_eps = annualized_net_income / safe_float(state.get("shares_outstanding")) if safe_float(state.get("shares_outstanding")) not in (None, 0) and annualized_net_income is not None else None

    st.markdown(
        f"""
        <div class="chip-row">
            <div class="chip">Annualized Revenue: {fmt_billions(annualized_revenue)}</div>
            <div class="chip">Annualized Net Income: {fmt_billions(annualized_net_income)}</div>
            <div class="chip">Annualized EPS: {fmt_money(annualized_eps)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)

    render_reset_menu(ticker, context_key="selected")

    if (
        safe_float(state["latest_quarter_revenue"]) is None
        or safe_float(state["latest_quarter_net_income"]) is None
        or safe_float(state["shares_outstanding"]) is None
        or safe_float(state["latest_quarter_revenue"]) <= 0
        or safe_float(state["shares_outstanding"]) <= 0
    ):
        st.warning("Base inputs incomplete. Enter latest quarter revenue, latest quarter net income, and shares outstanding to run scenarios.")

    current_price = safe_float(market_row.get("price"), 100.0)
    render_scenario_block("Bull Case", "bull", "bull", current_price)
    render_scenario_block("Base Case", "base", "base", current_price)
    render_scenario_block("Bear Case", "bear", "bear", current_price)


def render_settings_tab():
    st.markdown('<div class="section-title">Settings</div>', unsafe_allow_html=True)
    render_action_settings()
    render_bulk_import_section()

    message = st.session_state.pop("bulk_import_message", None)
    if message:
        st.success(message)

    if not failed_df.empty:
        st.markdown('<div class="section-title">Data Issues</div>', unsafe_allow_html=True)
        issue_view = pd.DataFrame({"Ticker": failed_df["ticker"], "Status": failed_df["status"], "Cache": failed_df["cache_source"]})
        st.dataframe(issue_view, width="stretch", hide_index=True, height=300)


col1, col2, col3, col4 = st.columns(4)
col1.metric("Portfolio Positions", fmt_num(len(portfolio_tickers)))
col2.metric("Watchlist Names", fmt_num(len(watchlist)))
col3.metric("Tracked Names", fmt_num(len(summary_df)))
col4.metric("Data Issues", fmt_num(len(failed_df)))

TAB_OPTIONS = ["Portfolio", "Watchlist", "Redistribution", "Stock Detail", "Settings"]
if "active_tab" not in st.session_state:
    st.session_state["active_tab"] = "Portfolio"
if st.session_state["active_tab"] not in TAB_OPTIONS:
    st.session_state["active_tab"] = "Portfolio"
if "nav_version" not in st.session_state:
    st.session_state["nav_version"] = 0

nav_index = TAB_OPTIONS.index(st.session_state["active_tab"])
selected_tab = st.radio("View", TAB_OPTIONS, index=nav_index, horizontal=True, key=f"main_nav_{st.session_state['nav_version']}", label_visibility="collapsed")
st.session_state["active_tab"] = selected_tab

if selected_tab == "Portfolio":
    render_portfolio_tab()
elif selected_tab == "Watchlist":
    render_watchlist_tab()
elif selected_tab == "Redistribution":
    render_redistribution_tab()
elif selected_tab == "Stock Detail":
    render_stock_detail_tab()
elif selected_tab == "Settings":
    render_settings_tab()
