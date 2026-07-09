from __future__ import annotations

from datetime import date

import pandas as pd


OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


def fetch_ohlcv(
    market: str,
    symbol: str,
    start: str | date,
    end: str | date | None = None,
) -> pd.DataFrame:
    market = market.upper()
    if market == "KR":
        return _fetch_kr(symbol, start, end)
    if market == "US":
        return _fetch_us(symbol, start, end)
    raise ValueError(f"Unsupported market: {market}")


def _fetch_kr(symbol: str, start: str | date, end: str | date | None) -> pd.DataFrame:
    try:
        import FinanceDataReader as fdr
    except ImportError as exc:
        raise RuntimeError("FinanceDataReader is required for KR data") from exc

    df = fdr.DataReader(symbol, start, end)
    return normalize_ohlcv(df)


def _fetch_us(symbol: str, start: str | date, end: str | date | None) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance is required for US data") from exc

    df = yf.download(
        symbol,
        start=str(start),
        end=str(end) if end else None,
        auto_adjust=True,
        progress=False,
        group_by="column",
    )
    return normalize_ohlcv(df)


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]

    normalized = df.rename(columns={col: str(col).lower().replace(" ", "_") for col in df.columns})
    rename_map = {
        "adj_close": "close",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
    }
    normalized = normalized.rename(columns=rename_map)

    missing = [col for col in OHLCV_COLUMNS if col not in normalized.columns]
    if missing:
        raise ValueError(f"Missing OHLCV columns: {missing}")

    normalized = normalized[OHLCV_COLUMNS].copy()
    normalized.index = pd.to_datetime(normalized.index).tz_localize(None).normalize()
    normalized = normalized.sort_index()
    normalized = normalized[~normalized.index.duplicated(keep="last")]
    return normalized.astype(
        {
            "open": "float64",
            "high": "float64",
            "low": "float64",
            "close": "float64",
            "volume": "float64",
        }
    )
