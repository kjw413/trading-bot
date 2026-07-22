"""Market regime from a macro index.

Macro series are not per-stock factors — they say nothing about which stock
to prefer. They are used to decide how much equity exposure to carry at all:
a theme's best names still fall in a broad drawdown.
"""

from __future__ import annotations

from datetime import date, timedelta

from tradingbot.data.store import PanelDataStore

BULL = "bull"
BEAR = "bear"
UNKNOWN = "unknown"


def market_regime(
    data_store: PanelDataStore,
    dt: date,
    *,
    series: str = "kospi",
    ma_days: int = 200,
) -> str:
    """Compare the index to its own moving average as of `dt`.

    Returns UNKNOWN when there is not enough history to judge — the caller
    must decide what to do with that rather than have it silently read as
    bearish.
    """
    if ma_days <= 0:
        raise ValueError("ma_days must be positive")

    start = dt - timedelta(days=int(ma_days * 2.0) + 30)
    panel = data_store.panel("macro", dt, [series], start=start)
    if panel.empty:
        return UNKNOWN

    closes = panel.sort_values("date")["close"].dropna()
    if len(closes) < ma_days:
        return UNKNOWN

    window = closes.tail(ma_days)
    return BULL if float(window.iloc[-1]) > float(window.mean()) else BEAR


def equity_exposure(regime_state: str, *, bull: float = 1.0, bear: float = 0.5) -> float:
    """Target equity exposure for a regime.

    UNKNOWN keeps full exposure: an unmeasurable regime is not evidence of a
    downturn, and defaulting to defensive would strand the strategy in cash
    whenever the macro panel is briefly missing.
    """
    return bear if regime_state == BEAR else bull
