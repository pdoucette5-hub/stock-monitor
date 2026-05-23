import pandas as pd


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


def _annualized_return(current_price, future_price, years=4):
    current_price = _safe_float(current_price)
    future_price = _safe_float(future_price)

    if current_price is None or future_price is None:
        return None
    if current_price <= 0 or future_price <= 0:
        return None

    return (future_price / current_price) ** (1 / years) - 1


def _confidence_flag(row):
    rev_rates = [
        _safe_float(row.get("rev_g_y1")),
        _safe_float(row.get("rev_g_y2")),
        _safe_float(row.get("rev_g_y3")),
        _safe_float(row.get("rev_g_y4")),
    ]
    eps_rates = [
        _safe_float(row.get("eps_g_y1")),
        _safe_float(row.get("eps_g_y2")),
        _safe_float(row.get("eps_g_y3")),
        _safe_float(row.get("eps_g_y4")),
    ]

    if any(v is None for v in rev_rates + eps_rates):
        return "Missing assumptions"

    aggressive_years = 0
    for rev_g, eps_g in zip(rev_rates, eps_rates):
        if eps_g - rev_g > 0.15:
            aggressive_years += 1

    if aggressive_years >= 2:
        return "Aggressive EPS vs revenue"

    return "OK"


def classify_projected_return(implied_return, hurdle_rate):
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
    if forecasts_df.empty:
        return pd.DataFrame()

    merged = forecasts_df.merge(
        market_df[["ticker", "price", "status"]],
        on="ticker",
        how="left"
    )

    projected_eps = []
    projected_price = []
    implied_returns = []
    confidence_flags = []
    signals = []

    for _, row in merged.iterrows():
        growth_rates = [
            row.get("eps_g_y1"),
            row.get("eps_g_y2"),
            row.get("eps_g_y3"),
            row.get("eps_g_y4"),
        ]

        eps_4 = _compound_eps(row.get("current_eps"), growth_rates)
        pe_target = _safe_float(row.get("target_pe"))

        if eps_4 is not None and pe_target is not None:
            price_4 = eps_4 * pe_target
        else:
            price_4 = None

        implied = _annualized_return(row.get("price"), price_4, years=4)
        flag = _confidence_flag(row)
        signal = classify_projected_return(implied, row.get("hurdle_rate"))

        projected_eps.append(eps_4)
        projected_price.append(price_4)
        implied_returns.append(implied)
        confidence_flags.append(flag)
        signals.append(signal)

    merged["projected_eps_y4"] = projected_eps
    merged["projected_price_y4"] = projected_price
    merged["implied_4y_annual_return"] = implied_returns
    merged["confidence_flag"] = confidence_flags
    merged["valuation_signal"] = signals

    return merged