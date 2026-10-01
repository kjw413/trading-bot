from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from tradingbot.data.credentials import MissingCredentialsError, krx_credentials
from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

FLOWS_DATA_VERSION = "2"
FLOWS_SOURCE = "pykrx"
FLOWS_DEFAULT_START = date(2015, 1, 1)

# Investor trading values in KRW.  The gross individual legs are deliberately
# retained: individual_net can be zero even when retail accounts dominate both
# sides of trading, so it cannot measure retail participation.
FLOW_COLUMNS = [
    "foreign_net",
    "institution_net",
    "individual_net",
    "individual_buy",
    "individual_sell",
    "traded_value",
]

# KRX column -> our column. Verified against pykrx output in Task 3 Step 1.
_COLUMN_MAP = {
    "외국인합계": "foreign_net",
    "기관합계": "institution_net",
    "개인": "individual_net",
}


def normalize_flows(
    raw: pd.DataFrame,
    symbol: str,
    *,
    buys: pd.DataFrame | None = None,
    sells: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Reshape pykrx investor trading-value frames into the panel schema.

    ``raw`` is the net-buy response. ``buys`` and ``sells`` are optional to
    keep old cached/test callers readable; missing gross data is represented
    by NaN rather than incorrectly treating net buying as participation.
    """
    if raw.empty:
        return pd.DataFrame(columns=["date", "symbol"] + FLOW_COLUMNS)

    missing = [column for column in _COLUMN_MAP if column not in raw.columns]
    if missing:
        raise ValueError(f"Flow response is missing column(s) {missing}; got {list(raw.columns)}")

    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(raw.index).tz_localize(None).normalize(),
            "symbol": str(symbol).upper(),
        }
    )
    for source_column, target_column in _COLUMN_MAP.items():
        frame[target_column] = raw[source_column].astype(float).to_numpy()
    frame["individual_buy"] = float("nan")
    frame["individual_sell"] = float("nan")
    frame["traded_value"] = float("nan")

    gross_frames = ((buys, "individual_buy"), (sells, "individual_sell"))
    for gross, target in gross_frames:
        if gross is None:
            continue
        missing_gross = [column for column in ("개인", "전체") if column not in gross.columns]
        if missing_gross:
            raise ValueError(
                f"Flow {target} response is missing column(s) {missing_gross}; "
                f"got {list(gross.columns)}"
            )
        values = gross[["개인", "전체"]].copy()
        values.index = pd.to_datetime(values.index).tz_localize(None).normalize()
        keyed = values[~values.index.duplicated(keep="last")]
        frame[target] = frame["date"].map(keyed["개인"]).astype(float)
        total = frame["date"].map(keyed["전체"]).astype(float)
        if frame["traded_value"].isna().all():
            frame["traded_value"] = total
        else:
            # Buy and sell totals should agree. Averaging is robust to the
            # occasional one-won rounding difference in source responses.
            frame["traded_value"] = (frame["traded_value"] + total) / 2.0
    return frame[["date", "symbol"] + FLOW_COLUMNS].reset_index(drop=True)


def fetch_flows(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Daily investor net-buy values for one symbol."""
    krx_credentials()

    from pykrx import stock

    args = (start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), str(symbol))
    raw = stock.get_market_trading_value_by_date(*args)
    buys = stock.get_market_trading_value_by_date(*args, on="매수")
    sells = stock.get_market_trading_value_by_date(*args, on="매도")
    return normalize_flows(raw, symbol, buys=buys, sells=sells)


def update_flows(
    store: PanelStore,
    *,
    symbols: Sequence[str],
    start: date | None = None,
    end: date | None = None,
    fetcher: Callable[..., pd.DataFrame] = fetch_flows,
) -> int:
    """Incrementally collect investor flows. One symbol's failure is logged
    and skipped so a single bad ticker cannot abort the batch."""
    written = 0
    attempted = 0
    failed = 0
    last_error: Exception | None = None
    fetch_end = end or date.today()
    for symbol in symbols:
        existing = store.read(symbols=[symbol])
        last = None if existing.empty else existing["date"].max().date()
        gross_columns = ["individual_buy", "individual_sell", "traded_value"]
        needs_gross_backfill = not existing.empty and (
            any(column not in existing.columns for column in gross_columns)
            or existing[gross_columns].isna().all(axis=None)
        )
        if needs_gross_backfill:
            fetch_start = start or FLOWS_DEFAULT_START
        elif last:
            fetch_start = last + timedelta(days=1)
        else:
            fetch_start = start or FLOWS_DEFAULT_START
        if fetch_start > fetch_end:
            continue
        attempted += 1
        try:
            frame = fetcher(symbol, fetch_start, fetch_end)
        except MissingCredentialsError:
            raise
        except Exception as exc:
            failed += 1
            last_error = exc
            LOGGER.exception("Flow collection failed for %s; skipping this symbol", symbol)
            continue
        if frame.empty:
            continue
        tagged = attach_metadata(
            frame,
            source=FLOWS_SOURCE,
            available_at=next_trading_day_availability(frame["date"], store.market),
            data_version=FLOWS_DATA_VERSION,
        )
        written += store.append(tagged)
    if attempted and failed == attempted:
        raise RuntimeError(
            f"Flow collection failed: {attempted} symbols were attempted and all failed"
        ) from last_error
    return written
