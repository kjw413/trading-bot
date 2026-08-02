"""Short-selling panels: balance outstanding and daily shorting volume.

Two KRX series, and the difference between them is the whole reason this
module exists separately from `flows.py`.

**Volume** — how much was sold short on day T — is published with T's close,
so it behaves like every other daily series here (usable at T+1).

**Balance** — how much remains sold short as of day T — is disclosed two
business days later. Tagging it with the ordinary one-day lag would let a
backtest read a balance two days before anyone could have known it. That bug
does not crash anything; it just makes the signal look prescient. The two
series therefore carry different `available_at` lags, and they are collected
into separate panels so one can never inherit the other's lag by accident.

`SHORT_BALANCE_LAG_DAYS` is the single place that number is written down.
Confirm it against real KRX disclosure timestamps before trusting any
measurement built on this panel — the design spec flags it as the highest
risk item in the redesign.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from tradingbot.data.credentials import MissingCredentialsError, krx_credentials
from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

SHORT_DATA_VERSION = "1"
SHORT_SOURCE = "pykrx"
SHORT_DEFAULT_START = date(2015, 1, 1)

# Trading days between the date a balance describes and the day it is public.
# 1 would mean "published with the close", which is true of volume and false
# of balance. See the module docstring.
SHORT_BALANCE_LAG_DAYS = 3
SHORT_VOLUME_LAG_DAYS = 1

BALANCE_COLUMNS = [
    "short_balance_qty",
    "shares_outstanding",
    "short_balance_value",
    "market_cap",
    "short_balance_ratio",
]
VOLUME_COLUMNS = ["short_volume", "total_volume", "short_volume_ratio"]

# KRX column -> our column, from pykrx's documented output:
#   get_shorting_balance_by_date -> 공매도잔고 상장주식수 공매도금액 시가총액 비중
#   get_shorting_volume_by_date  -> 공매도 매수 비중
_BALANCE_MAP = {
    "공매도잔고": "short_balance_qty",
    "상장주식수": "shares_outstanding",
    "공매도금액": "short_balance_value",
    "시가총액": "market_cap",
}
_VOLUME_MAP = {
    "공매도": "short_volume",
    "매수": "total_volume",
}


def _base_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(raw.index).tz_localize(None).normalize(),
            "symbol": str(symbol).upper(),
        }
    )


def _require(raw: pd.DataFrame, mapping: dict[str, str], label: str) -> None:
    missing = [column for column in mapping if column not in raw.columns]
    if missing:
        raise ValueError(
            f"{label} response is missing column(s) {missing}; got {list(raw.columns)}"
        )


def _ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Element-wise ratio where a non-positive denominator yields NaN.

    A zero denominator means the figure is unknown for that row, not that the
    ratio is zero — scoring a name on a fabricated 0.0 would rank it as the
    least-shorted stock in the universe.
    """
    safe = denominator.where(denominator > 0)
    return numerator / safe


def normalize_short_balance(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Reshape a pykrx short-balance frame into the panel schema.

    KRX supplies its own 비중 column, but it is recomputed here from quantity
    and shares outstanding: the published figure is rounded, and a ratio this
    small is the factor's entire signal.
    """
    if raw.empty:
        return pd.DataFrame(columns=["date", "symbol"] + BALANCE_COLUMNS)
    _require(raw, _BALANCE_MAP, "Short balance")

    frame = _base_frame(raw, symbol)
    for source_column, target_column in _BALANCE_MAP.items():
        frame[target_column] = raw[source_column].astype(float).to_numpy()
    frame["short_balance_ratio"] = _ratio(
        frame["short_balance_qty"], frame["shares_outstanding"]
    )
    return frame[["date", "symbol"] + BALANCE_COLUMNS].reset_index(drop=True)


def normalize_short_volume(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Reshape a pykrx short-volume frame into the panel schema.

    pykrx names the total-volume column 매수, which reads as "buy" but is the
    day's whole traded volume — the denominator, not the opposing side.
    """
    if raw.empty:
        return pd.DataFrame(columns=["date", "symbol"] + VOLUME_COLUMNS)
    _require(raw, _VOLUME_MAP, "Short volume")

    frame = _base_frame(raw, symbol)
    for source_column, target_column in _VOLUME_MAP.items():
        frame[target_column] = raw[source_column].astype(float).to_numpy()
    frame["short_volume_ratio"] = _ratio(frame["short_volume"], frame["total_volume"])
    return frame[["date", "symbol"] + VOLUME_COLUMNS].reset_index(drop=True)


def fetch_short_balance(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Daily short-balance outstanding for one symbol."""
    krx_credentials()

    from pykrx import stock

    raw = stock.get_shorting_balance_by_date(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), str(symbol)
    )
    return normalize_short_balance(raw, symbol)


def fetch_short_volume(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Daily short-sale volume for one symbol."""
    krx_credentials()

    from pykrx import stock

    raw = stock.get_shorting_volume_by_date(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), str(symbol)
    )
    return normalize_short_volume(raw, symbol)


def _update(
    store: PanelStore,
    *,
    symbols: Sequence[str],
    fetcher: Callable[..., pd.DataFrame],
    lag_days: int,
    label: str,
    start: date | None,
    end: date | None,
    backfill: bool,
) -> int:
    """Shared incremental/backfill loop for both short-selling series."""
    written = 0
    fetch_end = end or date.today()
    for symbol in symbols:
        if backfill:
            fetch_start = start or SHORT_DEFAULT_START
        else:
            last = store.last_date(symbol)
            fetch_start = last + timedelta(days=1) if last else (start or SHORT_DEFAULT_START)
        if fetch_start > fetch_end:
            continue
        try:
            frame = fetcher(symbol, fetch_start, fetch_end)
        except MissingCredentialsError:
            raise
        except Exception:
            LOGGER.exception("%s collection failed for %s; skipping this symbol", label, symbol)
            continue
        if frame.empty:
            continue
        tagged = attach_metadata(
            frame,
            source=SHORT_SOURCE,
            available_at=next_trading_day_availability(
                frame["date"], store.market, lag_days=lag_days
            ),
            data_version=SHORT_DATA_VERSION,
        )
        written += store.append(tagged)
    return written


def update_short_balance(
    store: PanelStore,
    *,
    symbols: Sequence[str],
    start: date | None = None,
    end: date | None = None,
    backfill: bool = False,
    fetcher: Callable[..., pd.DataFrame] = fetch_short_balance,
) -> int:
    """Collect short-balance history, tagged with the disclosure lag."""
    return _update(
        store,
        symbols=symbols,
        fetcher=fetcher,
        lag_days=SHORT_BALANCE_LAG_DAYS,
        label="Short balance",
        start=start,
        end=end,
        backfill=backfill,
    )


def update_short_volume(
    store: PanelStore,
    *,
    symbols: Sequence[str],
    start: date | None = None,
    end: date | None = None,
    backfill: bool = False,
    fetcher: Callable[..., pd.DataFrame] = fetch_short_volume,
) -> int:
    """Collect short-volume history, usable from the next trading day."""
    return _update(
        store,
        symbols=symbols,
        fetcher=fetcher,
        lag_days=SHORT_VOLUME_LAG_DAYS,
        label="Short volume",
        start=start,
        end=end,
        backfill=backfill,
    )
