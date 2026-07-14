from __future__ import annotations

from datetime import date

import pandas as pd


OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
OHLCV_DTYPES = {column: "float64" for column in OHLCV_COLUMNS}


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
    # yfinance의 curl_cffi는 일부 Windows 프록시/보안 프로그램이 설치한 시스템
    # 인증서를 읽지 못한다. FinanceDataReader + truststore 경로는 SSL 검증을
    # 유지하면서 Windows 인증서 저장소를 사용한다.
    try:
        import truststore

        truststore.inject_into_ssl()
        import FinanceDataReader as fdr
    except ImportError as exc:
        raise RuntimeError("FinanceDataReader and truststore are required for US data") from exc

    df = fdr.DataReader(symbol, start, end)
    return normalize_ohlcv(df)


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        empty = pd.DataFrame(columns=OHLCV_COLUMNS, index=pd.DatetimeIndex([], name=df.index.name))
        return empty.astype(OHLCV_DTYPES)

    if pd.api.types.is_numeric_dtype(df.index.dtype):
        raise ValueError("OHLCV data must use a date index")

    normalized = df.copy()
    if isinstance(normalized.columns, pd.MultiIndex):
        normalized.columns = [col[0] for col in normalized.columns]

    normalized = normalized.rename(columns={col: str(col).lower().replace(" ", "_") for col in normalized.columns})
    if "adj_close" in normalized.columns and "close" in normalized.columns:
        adjustment = normalized["adj_close"] / normalized["close"].replace(0, float("nan"))
        for column in ("open", "high", "low", "close"):
            if column in normalized.columns:
                normalized[column] = normalized[column] * adjustment
        normalized = normalized.drop(columns=["adj_close"])
    elif "adj_close" in normalized.columns:
        normalized = normalized.rename(columns={"adj_close": "close"})

    missing = [col for col in OHLCV_COLUMNS if col not in normalized.columns]
    if missing:
        raise ValueError(f"Missing OHLCV columns: {missing}")

    normalized = normalized[OHLCV_COLUMNS].copy()
    normalized.index = pd.to_datetime(normalized.index).tz_localize(None).normalize()
    normalized = normalized.sort_index()
    normalized = normalized[~normalized.index.duplicated(keep="last")]
    return normalized.astype(OHLCV_DTYPES)
