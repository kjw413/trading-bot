"""Ticker to SEC CIK mapping, the US counterpart of `data/corp_codes.py`.

EDGAR keys everything by CIK, while the rest of this project keys by ticker.
SEC publishes the crosswalk as one small JSON file, so it is cached to disk on
the same terms as the DART corp-code map: refreshed monthly, and a failed
refresh keeps whatever is already there.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CIK_CACHE_FILENAME = "sec_ciks.csv"
CIK_TTL_SECONDS = 30 * 24 * 3600


def parse_company_tickers(payload: dict[str, Any]) -> dict[str, int]:
    """Map ticker -> CIK from SEC's company_tickers.json.

    The file is a dict keyed by row index, not a list. CIKs are kept as ints:
    the submissions URL zero-pads to ten digits itself, and carrying padding
    here would double it.
    """
    mapping: dict[str, int] = {}
    for row in (payload or {}).values():
        ticker = str((row or {}).get("ticker", "")).strip().upper()
        if not ticker:
            continue
        try:
            mapping[ticker] = int(row["cik_str"])
        except (KeyError, TypeError, ValueError):
            LOGGER.warning("SEC ticker row for %s has no usable cik_str; skipping", ticker)
    return mapping


def download_company_tickers(user_agent: str | None = None, timeout: float = 30.0) -> dict:
    """Real network download. Imported lazily so tests never need requests."""
    import requests

    from tradingbot.data.edgar import user_agent_from_env

    agent = user_agent or user_agent_from_env()
    response = requests.get(
        COMPANY_TICKERS_URL, headers={"User-Agent": agent}, timeout=timeout
    )
    response.raise_for_status()
    return response.json()


class CikStore:
    """Disk-cached ticker -> CIK mapping."""

    def __init__(
        self, cache_dir: str | Path, downloader: Callable[[], dict] | None = None
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.downloader = downloader or download_company_tickers
        self._mapping: dict[str, int] | None = None

    @property
    def path(self) -> Path:
        return self.cache_dir / "_listings" / CIK_CACHE_FILENAME

    def _read_cache(self) -> dict[str, int] | None:
        if not self.path.exists():
            return None
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            return {row["ticker"]: int(row["cik"]) for row in csv.DictReader(handle)}

    def _write_cache(self, mapping: dict[str, int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["ticker", "cik"])
            writer.writerows(sorted(mapping.items()))

    def _is_fresh(self) -> bool:
        return self.path.exists() and time.time() - self.path.stat().st_mtime <= CIK_TTL_SECONDS

    def load(self, *, fetch: bool = True) -> dict[str, int]:
        """Return the mapping, refreshing from SEC when the cache is stale.

        With `fetch=False` the network is never touched and a missing cache
        yields an empty mapping. A failed refresh keeps the stale cache — a
        month-old crosswalk is still almost entirely correct, and discarding it
        over a transient outage would stop collection for no gain.
        """
        if self._mapping is not None and self._is_fresh():
            return self._mapping

        cached = self._read_cache()
        if cached is not None and (self._is_fresh() or not fetch):
            self._mapping = cached
            return cached
        if not fetch:
            return {}

        try:
            mapping = parse_company_tickers(self.downloader())
        except Exception:
            if cached is not None:
                LOGGER.exception("SEC CIK refresh failed; using stale cache")
                self._mapping = cached
                return cached
            raise

        self._write_cache(mapping)
        self._mapping = mapping
        LOGGER.info("SEC CIK map refreshed: %s tickers", len(mapping))
        return mapping

    def cik(self, ticker: str, *, fetch: bool = True) -> int | None:
        """CIK for one ticker, or None when SEC does not list it."""
        return self.load(fetch=fetch).get(str(ticker).strip().upper())

    def cik_for(self, tickers: Iterable[str], *, fetch: bool = True) -> dict[str, int]:
        """CIKs for the tickers that have one; unknown tickers are omitted."""
        mapping = self.load(fetch=fetch)
        found: dict[str, int] = {}
        for ticker in tickers:
            key = str(ticker).strip().upper()
            if key in mapping:
                found[key] = mapping[key]
        return found
