from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from tradingbot.symbols import (
    LISTING_CACHE_VERSION,
    _US_LISTING_URLS,
    SymbolDirectory,
    _default_fetcher,
    _fetch_us_listings,
)


def make_fetcher(calls: list[str]):
    def fetcher(market: str) -> pd.DataFrame:
        calls.append(market)
        return pd.DataFrame(
            {
                "symbol": ["005930", "000660", "035420"],
                "name": ["삼성전자", "SK하이닉스", "NAVER"],
            }
        )

    return fetcher


def test_search_by_name_and_code(tmp_path):
    directory = SymbolDirectory(tmp_path, fetcher=make_fetcher([]))

    assert directory.search("KR", "삼성") == [("005930", "삼성전자")]
    assert directory.search("KR", "000660") == [("000660", "SK하이닉스")]
    assert directory.search("KR", "naver") == [("035420", "NAVER")]
    assert directory.search("KR", "없는종목") == []
    assert directory.search("KR", "  ") == []

def test_exact_symbol_match_is_ranked_first(tmp_path):
    def fetcher(market: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "symbol": ["SPYT", "SPY", "YSPY"],
                "name": ["Defiance S&P 500 Target Income ETF", "SPDR S&P 500 ETF Trust", "Yield SPY ETF"],
            }
        )

    directory = SymbolDirectory(tmp_path, fetcher=fetcher)

    assert directory.search("US", "SPY")[0] == ("SPY", "SPDR S&P 500 ETF Trust")


def test_listing_is_cached_after_first_fetch(tmp_path):
    calls: list[str] = []
    directory = SymbolDirectory(tmp_path, fetcher=make_fetcher(calls))

    directory.search("KR", "삼성")
    directory.search("KR", "하이닉스")

    assert calls == ["KR"]
    assert directory.path("KR").exists()
    assert directory.version_path("KR").read_text(encoding="utf-8") == LISTING_CACHE_VERSION


def test_default_fetcher_combines_kr_stocks_and_etfs(monkeypatch):
    calls: list[str] = []
    listings = {
        "KRX": pd.DataFrame({"Code": ["005930"], "Name": ["삼성전자"]}),
        "ETF/KR": pd.DataFrame({"Symbol": ["069500"], "Name": ["KODEX 200"]}),
    }

    def stock_listing(source: str) -> pd.DataFrame:
        calls.append(source)
        return listings[source]

    monkeypatch.setitem(sys.modules, "FinanceDataReader", SimpleNamespace(StockListing=stock_listing))

    result = _default_fetcher("KR")

    assert calls == ["KRX", "ETF/KR"]
    assert sorted(result["symbol"].tolist()) == ["005930", "069500"]


def test_fetch_us_listings_combines_exchange_files_and_filters_test_symbols():
    calls: list[tuple[str, int]] = []
    payloads = {
        _US_LISTING_URLS[0][0]: (
            "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
            "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
            "QQQ|Invesco QQQ Trust|G|N|N|100|Y|N\n"
            "ZTEST|NASDAQ Test Stock|Q|Y|N|100|N|N\n"
            "File Creation Time: 0713202621:32|||||||\n"
        ),
        _US_LISTING_URLS[1][0]: (
            "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
            "IBM|International Business Machines Corporation|N|IBM|N|100|N|IBM\n"
            "SPY|State Street SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY\n"
            "File Creation Time: 0713202621:32|||||||\n"
        ),
    }

    class Response:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            pass

    def fake_get(url: str, timeout: int):
        calls.append((url, timeout))
        return Response(payloads[url])

    result = _fetch_us_listings(fake_get)

    assert calls == [(url, 30) for url, _column in _US_LISTING_URLS]
    assert sorted(result["symbol"].tolist()) == ["AAPL", "IBM", "QQQ", "SPY"]
    assert result.loc[result["symbol"].eq("AAPL"), "name"].iloc[0] == "Apple Inc. - Common Stock"


def test_default_fetcher_routes_us_to_nasdaq_trader(monkeypatch):
    expected = pd.DataFrame({"symbol": ["AAPL"], "name": ["Apple Inc."]})
    monkeypatch.setattr("tradingbot.symbols._fetch_us_listings", lambda: expected)

    assert _default_fetcher("US").equals(expected)


def test_legacy_cache_without_version_is_refreshed(tmp_path):
    calls: list[str] = []
    directory = SymbolDirectory(tmp_path, fetcher=make_fetcher(calls))
    directory.path("KR").parent.mkdir(parents=True)
    pd.DataFrame({"symbol": ["069500"], "name": ["오래된 이름"]}).to_csv(directory.path("KR"), index=False)

    result = directory.load("KR")

    assert calls == ["KR"]
    assert result.iloc[0]["name"] == "삼성전자"
    assert directory.version_path("KR").read_text(encoding="utf-8") == LISTING_CACHE_VERSION


def test_symbol_codes_keep_leading_zeros_after_cache_roundtrip(tmp_path):
    directory = SymbolDirectory(tmp_path, fetcher=make_fetcher([]))
    directory.load("KR")

    reloaded = SymbolDirectory(tmp_path, fetcher=make_fetcher([]))
    assert reloaded.search("KR", "삼성전자") == [("005930", "삼성전자")]


def test_name_map_does_not_fetch(tmp_path):
    calls: list[str] = []
    directory = SymbolDirectory(tmp_path, fetcher=make_fetcher(calls))

    assert directory.name_map("KR") == {}
    assert calls == []

    directory.load("KR")
    assert directory.name_map("KR")["005930"] == "삼성전자"
    assert calls == ["KR"]


def test_stale_cache_used_when_refresh_fails(tmp_path):
    directory = SymbolDirectory(tmp_path, fetcher=make_fetcher([]))
    directory.load("KR")

    old = time.time() - 30 * 24 * 3600
    os.utime(directory.path("KR"), (old, old))

    def failing_fetcher(market: str) -> pd.DataFrame:
        raise ConnectionError("offline")

    offline = SymbolDirectory(tmp_path, fetcher=failing_fetcher)
    assert offline.search("KR", "삼성전자") == [("005930", "삼성전자")]


def test_fetch_failure_without_cache_raises(tmp_path):
    def failing_fetcher(market: str) -> pd.DataFrame:
        raise ConnectionError("offline")

    directory = SymbolDirectory(tmp_path, fetcher=failing_fetcher)
    with pytest.raises(ConnectionError):
        directory.load("KR")
