"""The candidate pool: US common stocks, from the NASDAQ symbol directory.

`symbols.py` already downloads these files, but keeps only symbol and name for
the GUI's search box. Building a research universe needs the columns it drops —
the ETF flag above all — so this fetches them separately rather than widening a
module whose job is autocomplete.

What this pool is *not* is survivorship-free. It lists what trades today. A
company that delisted in 2018 is absent, and no filter here can bring it back;
`research/survivorship.py` measures how much is missing instead of pretending
otherwise.
"""

from __future__ import annotations

import csv
import time
from io import StringIO
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

LISTINGS_CACHE_FILENAME = "us_common_stocks.csv"
LISTINGS_TTL_SECONDS = 7 * 24 * 3600

# (url, symbol column). NASDAQ publishes its own listings and everything else
# separately, with different names for the same column.
_US_LISTING_URLS = (
    ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol"),
    ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol"),
)

# Instruments that are not common stock and do not report earnings on their
# own. Matched against the security name, which is where the directory says so.
_EXCLUDED_NAME_MARKERS = (
    "warrant",
    "right",
    "unit",
    "preferred",
    "depositary",
    "debenture",
    "% note",
    "convertible note",
    "subordinated",
    "when issued",
    "when-issued",
)

# CQS uses `$` and `.` inside a symbol for preferred series and share-class
# suffixes on non-common lines. A plain common share never needs one.
_EXCLUDED_SYMBOL_CHARS = ("$", "^")


def _looks_like_common_stock(symbol: str, name: str) -> bool:
    lowered = (name or "").lower()
    if any(marker in lowered for marker in _EXCLUDED_NAME_MARKERS):
        return False
    return not any(char in (symbol or "") for char in _EXCLUDED_SYMBOL_CHARS)


def parse_listing_file(text: str, symbol_column: str) -> pd.DataFrame:
    """Parse one pipe-delimited NASDAQ directory file.

    The last line of these files is a `File Creation Time` footer rather than a
    listing; it has no Test Issue value and drops out with the filter.
    """
    raw = pd.read_csv(StringIO(text), sep="|", dtype=str)
    for column in (symbol_column, "Security Name", "Test Issue", "ETF"):
        if column not in raw.columns:
            raise ValueError(f"Listing file is missing column {column}: {list(raw.columns)}")

    frame = raw.loc[
        raw["Test Issue"].eq("N") & raw["ETF"].eq("N"), [symbol_column, "Security Name"]
    ].rename(columns={symbol_column: "symbol", "Security Name": "name"})
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    frame["name"] = frame["name"].astype(str).str.strip()
    return frame.loc[frame["symbol"].ne("") & frame["name"].ne("")].reset_index(drop=True)


def fetch_us_common_stocks(http_get: Callable[..., object] | None = None) -> list[str]:
    """Every US common stock ticker the directory currently lists."""
    if http_get is None:
        import truststore

        truststore.inject_into_ssl()
        import requests

        http_get = requests.get

    frames = []
    for url, symbol_column in _US_LISTING_URLS:
        response = http_get(url, timeout=30)
        response.raise_for_status()
        frames.append(parse_listing_file(response.text, symbol_column))

    combined = pd.concat(frames, ignore_index=True).drop_duplicates("symbol")
    keep = [
        symbol
        for symbol, name in zip(combined["symbol"], combined["name"])
        if _looks_like_common_stock(symbol, name)
    ]
    return sorted(set(keep))


class UsCommonStockListing:
    """Disk-cached candidate pool, refreshed weekly.

    Same terms as the CIK and corp-code stores: a failed refresh keeps the
    stale list, because a week-old pool is almost entirely correct and losing
    it over a transient outage would stop collection for no gain.
    """

    def __init__(
        self, cache_dir: str | Path, fetcher: Callable[[], list[str]] | None = None
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.fetcher = fetcher or fetch_us_common_stocks
        self._symbols: list[str] | None = None

    @property
    def path(self) -> Path:
        return self.cache_dir / "_listings" / LISTINGS_CACHE_FILENAME

    def _read_cache(self) -> list[str] | None:
        if not self.path.exists():
            return None
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            return [row["symbol"] for row in csv.DictReader(handle)]

    def _write_cache(self, symbols: Iterable[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["symbol"])
            writer.writerows([[symbol] for symbol in symbols])

    def _is_fresh(self) -> bool:
        return (
            self.path.exists()
            and time.time() - self.path.stat().st_mtime <= LISTINGS_TTL_SECONDS
        )

    def load(self, *, fetch: bool = True) -> list[str]:
        if self._symbols is not None and self._is_fresh():
            return self._symbols

        cached = self._read_cache()
        if cached is not None and (self._is_fresh() or not fetch):
            self._symbols = cached
            return cached
        if not fetch:
            return []

        try:
            symbols = self.fetcher()
        except Exception:
            if cached is not None:
                LOGGER.exception("US listing refresh failed; using stale cache")
                self._symbols = cached
                return cached
            raise

        self._write_cache(symbols)
        self._symbols = symbols
        LOGGER.info("US common stock pool refreshed: %s tickers", len(symbols))
        return symbols
