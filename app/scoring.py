import pandas as pd


FORECAST_YEARS = 3


def _safe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _compound_eps(current_eps, growth_rates):
    eps = _safe_float(current_eps)
    if eps is None or eps <= 0:
        return None

    for g in growth_rates:
        g = _safe_float(g)
        if g is None:
            return None
        eps *= (1 + g)

    return eps


def _annualized_return(current_price, fair_value_today, years=FORECAST_YEARS):
    """
    Annualized return to fair value today is mostly useful if fair_value_today is
    treated as the future price. For this terminal-growth model, the cleaner
    signal is based on whether fair value today is above/below current price.
    This function is retained for compatibility.
    """
    current_price = _safe_float(current_price)
    fair_value_today = _safe_float(fair_value_today)
    years = _safe_float(years)

    if current_price is None or fair_value_today is None or years is None:
        return None
    if current_price <= 0 or fair_value_today <= 0 or years <= 0:
        return None

    return (fair_value_today / current_price) ** (1 / years) - 1


def _terminal_value_at_y3(eps_y3, terminal_growth, required_return):
    eps_y3 = _safe_float(eps_y3)
    terminal_growth = _safe_float(terminal_growth)
    required_return = _safe_float(required_return)

    if eps_y3 is None or terminal_growth is None or required_return is None:
        return None
    if eps_y3 <= 0:
        return None
    if required_return <= terminal_growth:
        return None

    eps_y4 = eps_y3 * (1 + terminal_growth)
    return eps_y4 / (required_return - terminal_growth)


def _fair_value_today_from_terminal(eps_y3, terminal_growth, required_return, years=FORECAST_YEARS):
    terminal_value = _terminal_value_at_y3(eps_y3, terminal_growth, required_return)
    required_return = _safe_float(required_return)
    years = _safe_float(years)

    if terminal_value is None or required_return is None or years is None:
        return None
    if required_return <= 0 or years <= 0:
        return None

    return terminal_value / ((1 + required_return) ** years)


def _upside_downside(current_price, fair_value_today):
    current_price = _safe_float(current_price)
    fair_value_today = _safe_float(fair_value_today)

    if current_price is None or fair_value_today is None:
        return None
    if current_price <= 0:
        return None

    return (fair_value_today / current_price) - 1


def _confidence_flag(row):
    rev_rates = [
        _safe_float(row.get("rev_g_y1")),
        _safe_float(row.get("rev_g_y2")),
        _safe_float(row.get("rev_g_y3")),
    ]
    eps_rates = [
        _safe_float(row.get("eps_g_y1")),
        _safe_float(row.get("eps_g_y2")),
        _safe_float(row.get("eps_g_y3")),
    ]

    terminal_growth_low = _safe_float(row.get("terminal_growth_low"))
    terminal_growth_high = _safe_float(row.get("terminal_growth_high"))
    required_return = _safe_float(row.get("required_return"))

    if any(v is None for v in rev_rates + eps_rates):
        return "Missing assumptions"

    if terminal_growth_low is None or terminal_growth_high is None or required_return is None:
        return "Missing terminal assumptions"

    if required_return <= terminal_growth_high:
        return "Invalid terminal growth"

    aggressive_years = 0
    for rev_g, eps_g in zip(rev_rates, eps_rates):
        if eps_g - rev_g > 0.15:
            aggressive_years += 1

    if aggressive_years >= 2:
        return "Aggressive EPS vs revenue"

    if terminal_growth_high >= 0.08:
        return "High terminal growth assumption"

    return "OK"


def classify_fair_value(current_price, fair_value_low, fair_value_high, margin_of_safety=0.10):
    current_price = _safe_float(current_price)
    fair_value_low = _safe_float(fair_value_low)
    fair_value_high = _safe_float(fair_value_high)
    margin_of_safety = _safe_float(margin_of_safety)

    if current_price is None or fair_value_low is None or fair_value_high is None:
        return "Hold / Watch"
    if current_price <= 0:
        return "Hold / Watch"

    buy_price = fair_value_low * (1 - margin_of_safety)
    trim_price = fair_value_high * (1 + margin_of_safety)

    if current_price <= buy_price:
        return "Consider Buy"
    if current_price >= trim_price:
        return "Consider Trim"
    return "Hold / Watch"


def classify_projected_return(implied_return, hurdle_rate):
    """
    Kept for compatibility with any older imports.
    The new model should generally use classify_fair_value().
    """
    implied_return = _safe_float(implied_return)
    hurdle_rate = _safe_float(hurdle_rate)

    if implied_return is None or hurdle_rate is None:
        return "Hold / Watch"

    buy_cutoff = hurdle_rate + 0.05
    hold_cutoff = hurdle_rate - 0.02

    if implied_return >= buy_cutoff:
        return "Consider Buy"
    if implied_return < hold_cutoff:
        return "Consider Trim"
    return "Hold / Watch"


def build_forecast_view(market_df: pd.DataFrame, forecasts_df: pd.DataFrame) -> pd.DataFrame:
    """
    Expected forecast columns:
      ticker
      current_eps
      rev_g_y1, rev_g_y2, rev_g_y3      # decimals, e.g. 0.20 for 20%
      eps_g_y1, eps_g_y2, eps_g_y3      # decimals, e.g. 0.25 for 25%
      terminal_growth_low               # decimal, e.g. 0.03
      terminal_growth_high              # decimal, e.g. 0.06
      required_return                   # decimal, e.g. 0.12
      optional: margin_of_safety         # decimal, e.g. 0.10

    Returns one row per ticker with projected EPS, terminal values,
    fair values today, upside/downside, confidence flag, and signal.
    """
    if forecasts_df.empty:
        return pd.DataFrame()

    market_cols = [c for c in ["ticker", "price", "status"] if c in market_df.columns]
    merged = forecasts_df.merge(
        market_df[market_cols],
        on="ticker",
        how="left",
    )

    projected_eps_y3 = []
    terminal_value_low_y3 = []
    terminal_value_high_y3 = []
    fair_value_low_today = []
    fair_value_high_today = []
    upside_low = []
    upside_high = []
    confidence_flags = []
    signals = []

    for _, row in merged.iterrows():
        growth_rates = [
            row.get("eps_g_y1"),
            row.get("eps_g_y2"),
            row.get("eps_g_y3"),
        ]

        eps_y3 = _compound_eps(row.get("current_eps"), growth_rates)
        required_return = _safe_float(row.get("required_return"))
        terminal_growth_low = _safe_float(row.get("terminal_growth_low"))
        terminal_growth_high = _safe_float(row.get("terminal_growth_high"))
        margin_of_safety = _safe_float(row.get("margin_of_safety"), 0.10)

        tv_low = _terminal_value_at_y3(eps_y3, terminal_growth_low, required_return)
        tv_high = _terminal_value_at_y3(eps_y3, terminal_growth_high, required_return)

        fv_low = _fair_value_today_from_terminal(
            eps_y3, terminal_growth_low, required_return, years=FORECAST_YEARS
        )
        fv_high = _fair_value_today_from_terminal(
            eps_y3, terminal_growth_high, required_return, years=FORECAST_YEARS
        )

        up_low = _upside_downside(row.get("price"), fv_low)
        up_high = _upside_downside(row.get("price"), fv_high)

        flag = _confidence_flag(row)
        signal = classify_fair_value(
            row.get("price"),
            fv_low,
            fv_high,
            margin_of_safety=margin_of_safety,
        )

        projected_eps_y3.append(eps_y3)
        terminal_value_low_y3.append(tv_low)
        terminal_value_high_y3.append(tv_high)
        fair_value_low_today.append(fv_low)
        fair_value_high_today.append(fv_high)
        upside_low.append(up_low)
        upside_high.append(up_high)
        confidence_flags.append(flag)
        signals.append(signal)

    merged["projected_eps_y3"] = projected_eps_y3
    merged["terminal_value_low_y3"] = terminal_value_low_y3
    merged["terminal_value_high_y3"] = terminal_value_high_y3
    merged["fair_value_low_today"] = fair_value_low_today
    merged["fair_value_high_today"] = fair_value_high_today
    merged["upside_low"] = upside_low
    merged["upside_high"] = upside_high
    merged["confidence_flag"] = confidence_flags
    merged["valuation_signal"] = signals

    return merged
