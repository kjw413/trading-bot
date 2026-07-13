from __future__ import annotations

import time
from io import StringIO
from pathlib import Path
from typing import Callable

import pandas as pd

from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

LISTING_TTL_SECONDS = 7 * 24 * 3600
LISTING_CACHE_VERSION = "3"

_KR_SOURCES = ["KRX", "ETF/KR"]
_US_LISTING_URLS = (
    ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol"),
    ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol"),
)


def _normalize(raw: pd.DataFrame) -> pd.DataFrame:
    code_col = next(col for col in ("Code", "Symbol") if col in raw.columns)
    df = raw[[code_col, "Name"]].rename(columns={code_col: "symbol", "Name": "name"})
    return df.dropna().astype(str)


def _fetch_us_listings(http_get=None) -> pd.DataFrame:
    """NASDAQ Trader 공식 디렉터리에서 미국 상장 주식·ETF 목록을 받는다."""
    if http_get is None:
        import truststore

        truststore.inject_into_ssl()
        import requests

        http_get = requests.get

    frames: list[pd.DataFrame] = []
    for url, symbol_column in _US_LISTING_URLS:
        response = http_get(url, timeout=30)
        response.raise_for_status()
        raw = pd.read_csv(StringIO(response.text), sep="|", dtype=str)
        # 파일 마지막의 생성 시각 행과 거래소 테스트 종목을 함께 제외한다.
        raw = raw.loc[raw["Test Issue"].eq("N"), [symbol_column, "Security Name"]]
        frames.append(raw.rename(columns={symbol_column: "symbol", "Security Name": "name"}))

    combined = pd.concat(frames, ignore_index=True)
    combined["symbol"] = combined["symbol"].str.strip()
    combined["name"] = combined["name"].str.strip()
    combined = combined.loc[combined["symbol"].ne("") & combined["name"].ne("")]
    combined = combined.drop_duplicates("symbol")
    return combined.sort_values("name", ignore_index=True)


def _default_fetcher(market: str) -> pd.DataFrame:
    market = market.upper()
    if market == "US":
        return _fetch_us_listings()
    if market != "KR":
        raise ValueError(f"Unsupported market: {market}")

    import FinanceDataReader as fdr

    frames = [_normalize(fdr.StockListing(source)) for source in _KR_SOURCES]
    df = pd.concat(frames, ignore_index=True).drop_duplicates("symbol")
    return df.sort_values("name", ignore_index=True)


class SymbolDirectory:
    """시장별 (코드, 종목명) 목록을 내려받아 CSV로 캐시하고 이름/코드 검색을 제공한다."""

    def __init__(self, cache_dir: str | Path, fetcher: Callable[[str], pd.DataFrame] | None = None) -> None:
        self.cache_dir = Path(cache_dir)
        self.fetcher = fetcher or _default_fetcher

    def path(self, market: str) -> Path:
        return self.cache_dir / "_listings" / f"{market.upper()}.csv"

    def version_path(self, market: str) -> Path:
        return self.cache_dir / "_listings" / f"{market.upper()}.version"

    def load(self, market: str, *, fetch: bool = True) -> pd.DataFrame:
        """종목 목록을 반환한다. columns: [symbol, name].

        캐시가 신선하면 캐시를 쓰고, 오래됐거나 검색 소스 버전이 바뀌었으면
        다시 내려받는다. fetch=False면 네트워크를 쓰지 않고 캐시가 없을 때 빈
        DataFrame을 반환한다.
        """
        path = self.path(market)
        cached = pd.read_csv(path, dtype=str) if path.exists() else None
        if cached is not None:
            version_path = self.version_path(market)
            current_version = version_path.read_text(encoding="utf-8").strip() if version_path.exists() else None
            fresh = (
                current_version == LISTING_CACHE_VERSION
                and time.time() - path.stat().st_mtime <= LISTING_TTL_SECONDS
            )
            if fresh or not fetch:
                return cached
        if not fetch:
            return pd.DataFrame(columns=["symbol", "name"])

        try:
            df = self.fetcher(market)
        except Exception:
            if cached is not None:
                LOGGER.exception("Symbol listing refresh failed for %s; using stale cache", market)
                return cached
            raise
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        self.version_path(market).write_text(LISTING_CACHE_VERSION, encoding="utf-8")
        return df

    def search(self, market: str, query: str, *, limit: int = 50) -> list[tuple[str, str]]:
        """종목명 또는 코드에 query가 포함된 종목을 (코드, 이름) 목록으로 반환한다."""
        term = query.strip().lower()
        if not term:
            return []
        df = self.load(market)
        symbols = df["symbol"].str.lower()
        names = df["name"].str.lower()
        matched = df.loc[names.str.contains(term, regex=False) | symbols.str.contains(term, regex=False)].copy()
        matched_symbols = matched["symbol"].str.lower()
        matched_names = matched["name"].str.lower()
        matched["_rank"] = 3
        matched.loc[matched_symbols.str.startswith(term) | matched_names.str.startswith(term), "_rank"] = 2
        matched.loc[matched_names.eq(term), "_rank"] = 1
        matched.loc[matched_symbols.eq(term), "_rank"] = 0
        matched = matched.sort_values(["_rank", "name", "symbol"], kind="stable")
        return list(matched[["symbol", "name"]].head(limit).itertuples(index=False, name=None))

    def name_map(self, market: str) -> dict[str, str]:
        """캐시된 목록만으로 코드 -> 종목명 매핑을 반환한다(네트워크 사용 없음)."""
        df = self.load(market, fetch=False)
        return dict(zip(df["symbol"], df["name"]))
