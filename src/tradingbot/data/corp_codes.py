"""Map KRX stock codes to DART corp codes.

DART identifies companies by an 8-digit corp_code, while everything else in
this project — prices, flows, valuation ratios, themes — is keyed by the
6-digit KRX stock code. Without this bridge the fundamentals collector has
nothing to ask DART about.

The full mapping is published as a single ZIP of XML covering every
registered company, listed or not. It changes rarely (a new listing, a name
change), so it is cached on disk and refreshed monthly; a refresh failure
falls back to the stale cache rather than halting collection.
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from pathlib import Path
from typing import Callable, Iterable
from xml.etree import ElementTree

from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

CORP_CODE_ENDPOINT = "https://opendart.fss.or.kr/api/corpCode.xml"
CORP_CODE_TTL_SECONDS = 30 * 24 * 3600
CACHE_FILENAME = "corp_codes.csv"


def parse_corp_code_xml(xml_bytes: bytes) -> dict[str, str]:
    """Stock code -> corp code, for listed companies only.

    Entries with a blank stock_code are unlisted companies: there is no ticker
    to reach them by, so they are dropped rather than stored unreachable.
    """
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise ValueError(f"corp code XML could not be parsed: {exc}") from exc

    mapping: dict[str, str] = {}
    for entry in root.iter("list"):
        stock_code = (entry.findtext("stock_code") or "").strip()
        corp_code = (entry.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            mapping[stock_code] = corp_code
    return mapping


def extract_corp_code_xml(payload: bytes) -> bytes:
    """Pull the XML document out of DART's ZIP response."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        # DART answers a rejected key with a bare XML error document, not a ZIP.
        preview = payload[:200].decode("utf-8", errors="replace")
        raise ValueError(f"corp code response is not a ZIP: {preview}") from exc

    names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
    if not names:
        raise ValueError(f"corp code ZIP has no xml member: {archive.namelist()}")
    return archive.read(names[0])


def download_corp_codes(timeout: float = 60.0) -> bytes:
    """Fetch the corp code ZIP from DART. Network call; injected in tests."""
    import requests

    from tradingbot.data.fundamentals_panel import dart_api_key

    response = requests.get(
        CORP_CODE_ENDPOINT, params={"crtfc_key": dart_api_key()}, timeout=timeout
    )
    response.raise_for_status()
    return response.content


class CorpCodeStore:
    """Disk-cached stock code -> corp code mapping."""

    def __init__(
        self, cache_dir: str | Path, downloader: Callable[[], bytes] | None = None
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.downloader = downloader or download_corp_codes
        self._mapping: dict[str, str] | None = None

    @property
    def path(self) -> Path:
        return self.cache_dir / "_listings" / CACHE_FILENAME

    def _read_cache(self) -> dict[str, str] | None:
        if not self.path.exists():
            return None
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            return {row["symbol"]: row["corp_code"] for row in csv.DictReader(handle)}

    def _write_cache(self, mapping: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["symbol", "corp_code"])
            writer.writerows(sorted(mapping.items()))

    def _is_fresh(self) -> bool:
        return (
            self.path.exists()
            and time.time() - self.path.stat().st_mtime <= CORP_CODE_TTL_SECONDS
        )

    def load(self, *, fetch: bool = True) -> dict[str, str]:
        """Return the mapping, refreshing from DART when the cache is stale.

        With `fetch=False` the network is never touched and a missing cache
        yields an empty mapping. A failed refresh keeps the stale cache: a
        month-old mapping is still almost entirely correct, and discarding it
        over a transient outage would stop fundamentals collection for no gain.
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
            mapping = parse_corp_code_xml(extract_corp_code_xml(self.downloader()))
        except Exception:
            if cached is not None:
                LOGGER.exception("Corp code refresh failed; using stale cache")
                self._mapping = cached
                return cached
            raise

        self._write_cache(mapping)
        self._mapping = mapping
        LOGGER.info("Corp code map refreshed: %s listed companies", len(mapping))
        return mapping

    def corp_code(self, symbol: str, *, fetch: bool = True) -> str | None:
        """Corp code for one stock code, or None when it is not listed."""
        return self.load(fetch=fetch).get(str(symbol).strip())

    def corp_code_for(self, symbols: Iterable[str], *, fetch: bool = True) -> dict[str, str]:
        """Corp codes for the symbols that have one; unknown symbols are omitted."""
        mapping = self.load(fetch=fetch)
        found = {}
        for symbol in symbols:
            key = str(symbol).strip()
            if key in mapping:
                found[key] = mapping[key]
            else:
                LOGGER.warning("No DART corp_code for %s", key)
        return found
