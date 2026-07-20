from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

MACRO_DATA_VERSION = "1"
MACRO_SOURCE = "financedatareader"
MACRO_DEFAULT_START = date(2010, 1, 1)

# Series name -> FinanceDataReader symbol. Used as regime filters and risk
# context, not as per-stock factors.
MACRO_SERIES: dict[str, str] = {
    "kospi": "KS11",
    "kosdaq": "KQ11",
    "usdkrw": "USD/KRW",
    "kr_treasury_3y": "KR3YT=RR",
    "vix": "VIX",
}


def fetch_macro_series(series: str, start: date, end: date | None = None) -> pd.DataFrame:
    """Daily close for one macro series, normalized to the panel shape."""
    try:
        symbol = MACRO_SERIES[series]
    except KeyError as exc:
        available = ", ".join(sorted(MACRO_SERIES))
        raise ValueError(f"Unknown macro series: {series}. Available: {available}") from exc

    import FinanceDataReader as fdr

    raw = fdr.DataReader(symbol, start, end)
    if raw.empty:
        return pd.DataFrame(columns=["date", "symbol", "close"])

    columns = {str(column).lower(): column for column in raw.columns}
    if "close" not in columns:
        raise ValueError(f"Macro series {series} response has no close column: {list(raw.columns)}")

    return pd.DataFrame(
        {
            "date": pd.to_datetime(raw.index).tz_localize(None).normalize(),
            "symbol": series,
            "close": raw[columns["close"]].astype(float).to_numpy(),
        }
    )


def update_macro(
    store: PanelStore,
    *,
    series: Sequence[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    fetcher: Callable[..., pd.DataFrame] = fetch_macro_series,
) -> int:
    """Incrementally collect macro series into the panel store."""
    names = list(series) if series is not None else list(MACRO_SERIES)
    unknown = [name for name in names if name not in MACRO_SERIES]
    if unknown:
        available = ", ".join(sorted(MACRO_SERIES))
        raise ValueError(f"Unknown macro series: {', '.join(unknown)}. Available: {available}")

    written = 0
    for name in names:
        last = store.last_date(name)
        fetch_start = last + timedelta(days=1) if last else (start or MACRO_DEFAULT_START)
        frame = fetcher(name, fetch_start, end)
        if frame.empty:
            LOGGER.info("Macro series %s returned no new rows from %s", name, fetch_start)
            continue
        tagged = attach_metadata(
            frame,
            source=MACRO_SOURCE,
            available_at=next_trading_day_availability(frame["date"], store.market),
            data_version=MACRO_DATA_VERSION,
        )
        written += store.append(tagged)
    return written
