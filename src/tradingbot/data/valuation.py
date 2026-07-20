from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from tradingbot.data.credentials import MissingCredentialsError, krx_credentials
from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

VALUATION_DATA_VERSION = "1"
VALUATION_SOURCE = "pykrx"
VALUATION_DEFAULT_START = date(2015, 1, 1)

VALUATION_COLUMNS = ["per", "pbr", "eps", "bps", "div_yield"]

# KRX publishes these daily from the latest disclosed financials, so they are
# point-in-time correct as observed — no restatement backfill to undo.
_COLUMN_MAP = {"PER": "per", "PBR": "pbr", "EPS": "eps", "BPS": "bps", "DIV": "div_yield"}

# KRX reports 0 (not null) when a ratio is undefined, e.g. PER for a
# loss-making company. Left as 0 these would rank as "cheapest".
_ZERO_MEANS_MISSING = ["per", "pbr"]


def normalize_valuation(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Reshape a pykrx fundamental frame into the panel schema."""
    if raw.empty:
        return pd.DataFrame(columns=["date", "symbol"] + VALUATION_COLUMNS)

    missing = [column for column in _COLUMN_MAP if column not in raw.columns]
    if missing:
        raise ValueError(
            f"Valuation response is missing column(s) {missing}; got {list(raw.columns)}"
        )

    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(raw.index).tz_localize(None).normalize(),
            "symbol": str(symbol).upper(),
        }
    )
    for source_column, target_column in _COLUMN_MAP.items():
        frame[target_column] = raw[source_column].astype(float).to_numpy()
    for column in _ZERO_MEANS_MISSING:
        frame[column] = frame[column].replace(0.0, float("nan"))
    return frame[["date", "symbol"] + VALUATION_COLUMNS].reset_index(drop=True)


def fetch_valuation(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Daily valuation ratios for one symbol."""
    krx_credentials()

    from pykrx import stock

    raw = stock.get_market_fundamental(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), str(symbol)
    )
    return normalize_valuation(raw, symbol)


def update_valuation(
    store: PanelStore,
    *,
    symbols: Sequence[str],
    start: date | None = None,
    end: date | None = None,
    fetcher: Callable[..., pd.DataFrame] = fetch_valuation,
) -> int:
    """Incrementally collect valuation ratios, skipping failing symbols."""
    written = 0
    fetch_end = end or date.today()
    for symbol in symbols:
        last = store.last_date(symbol)
        fetch_start = last + timedelta(days=1) if last else (start or VALUATION_DEFAULT_START)
        if fetch_start > fetch_end:
            continue
        try:
            frame = fetcher(symbol, fetch_start, fetch_end)
        except MissingCredentialsError:
            raise
        except Exception:
            LOGGER.exception("Valuation collection failed for %s; skipping this symbol", symbol)
            continue
        if frame.empty:
            continue
        tagged = attach_metadata(
            frame,
            source=VALUATION_SOURCE,
            available_at=next_trading_day_availability(frame["date"], store.market),
            data_version=VALUATION_DATA_VERSION,
        )
        written += store.append(tagged)
    return written
