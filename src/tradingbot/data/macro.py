from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from tradingbot.data.credentials import MissingCredentialsError
from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

MACRO_DATA_VERSION = "1"
MACRO_SOURCE = "financedatareader"
MACRO_DEFAULT_START = date(2010, 1, 1)

# Market -> {series name: FinanceDataReader symbol}. Used as regime filters and
# risk context, not as per-stock factors. The panel storage path is
# market-scoped (macro/{MARKET}/), so one market's panel must never be filled
# with another market's indices.
#
# kr_treasury_3y (KR3YT=RR) was dropped: the Yahoo-backed ticker 404s and no
# equivalent 3-year KR treasury yield was found in FinanceDataReader.
#
# us_treasury_10y uses ^TNX, not US10YT=X: the latter 404s against the
# Yahoo-backed endpoint FinanceDataReader falls back to for that symbol.
MACRO_SERIES_BY_MARKET: dict[str, dict[str, str]] = {
    "KR": {
        "kospi": "KS11",
        "kosdaq": "KQ11",
        "usdkrw": "USD/KRW",
        "vix": "VIX",
    },
    "US": {
        "sp500": "US500",
        "nasdaq": "IXIC",
        "us_treasury_10y": "^TNX",
        "vix": "VIX",
    },
}

def _build_symbol_lookup() -> dict[str, str]:
    """Flatten every market's series into one name -> symbol lookup.

    A name reused across markets must resolve to the same symbol (vix does).
    A conflicting redefinition would silently repoint every market's lookup
    at the last one defined, so it fails here instead.
    """
    lookup: dict[str, str] = {}
    for market, series in MACRO_SERIES_BY_MARKET.items():
        for name, symbol in series.items():
            existing = lookup.get(name)
            if existing is not None and existing != symbol:
                raise ValueError(
                    f"Macro series {name!r} is defined as {existing!r} and as {symbol!r} "
                    f"(in {market}); one name must mean one symbol across markets."
                )
            lookup[name] = symbol
    return lookup


# 전 시장 심볼의 합집합. fetch_macro_series가 이름으로 심볼을 찾을 때 쓴다.
MACRO_SERIES: dict[str, str] = _build_symbol_lookup()


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
    """Incrementally collect macro series into the panel store.

    Without an explicit `series`, collects the ones defined for the store's
    own market — a US panel must not be filled with Korean indices.
    """
    if series is not None:
        names = list(series)
    else:
        try:
            names = list(MACRO_SERIES_BY_MARKET[store.market])
        except KeyError as exc:
            available = ", ".join(sorted(MACRO_SERIES_BY_MARKET))
            raise ValueError(
                f"No macro series defined for market {store.market}. Available: {available}"
            ) from exc
    unknown = [name for name in names if name not in MACRO_SERIES]
    if unknown:
        available = ", ".join(sorted(MACRO_SERIES))
        raise ValueError(f"Unknown macro series: {', '.join(unknown)}. Available: {available}")

    written = 0
    fetch_end = end or date.today()
    for name in names:
        last = store.last_date(name)
        fetch_start = last + timedelta(days=1) if last else (start or MACRO_DEFAULT_START)
        if fetch_start > fetch_end:
            # Already current. Asking anyway sends an inverted range upstream:
            # Yahoo answers `period1 > period2` with a 400, and the run ends in
            # a logged traceback that reads like an outage. Every other
            # collector skips this; macro was the one that did not.
            continue
        try:
            frame = fetcher(name, fetch_start, end)
        except MissingCredentialsError:
            raise
        except Exception:
            LOGGER.exception("Macro collection failed for %s; skipping this series", name)
            continue
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
