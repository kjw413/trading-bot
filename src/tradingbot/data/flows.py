from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

FLOWS_DATA_VERSION = "1"
FLOWS_SOURCE = "pykrx"
FLOWS_DEFAULT_START = date(2015, 1, 1)

# Net buy value in KRW, per investor group.
FLOW_COLUMNS = ["foreign_net", "institution_net", "individual_net"]

# KRX column -> our column. Verified against pykrx output in Task 3 Step 1.
_COLUMN_MAP = {
    "외국인합계": "foreign_net",
    "기관합계": "institution_net",
    "개인": "individual_net",
}


def normalize_flows(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Reshape a pykrx investor-flow frame into the panel schema."""
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
    return frame[["date", "symbol"] + FLOW_COLUMNS].reset_index(drop=True)


def fetch_flows(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Daily investor net-buy values for one symbol."""
    from pykrx import stock

    raw = stock.get_market_trading_value_by_date(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), str(symbol)
    )
    return normalize_flows(raw, symbol)


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
    fetch_end = end or date.today()
    for symbol in symbols:
        last = store.last_date(symbol)
        fetch_start = last + timedelta(days=1) if last else (start or FLOWS_DEFAULT_START)
        if fetch_start > fetch_end:
            continue
        try:
            frame = fetcher(symbol, fetch_start, fetch_end)
        except Exception:
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
    return written
