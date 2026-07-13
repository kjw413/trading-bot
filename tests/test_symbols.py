from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from tradingbot.symbols import LISTING_CACHE_VERSION, SymbolDirectory, _default_fetcher


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


def test_listing_is_cached_after_first_fetch(tmp_path):
    calls: list[str] = []
    directory = SymbolDirectory(tmp_path, fetcher=make_fetcher(calls))

    directory.search("KR", "삼성")
    directory.search("KR", "하이닉스")

    assert calls == ["KR"]
    assert directory.path("KR").exists()
    assert directory.version_path("KR").read_text(encoding="utf-8") == LISTING_CACHE_VERSION


@pytest.mark.parametrize(
    ("market", "expected_sources", "expected_symbols"),
    [
        ("KR", ["KRX", "ETF/KR"], ["005930", "069500"]),
        ("US", ["S&P500", "ETF/US"], ["AAPL", "SPY"]),
    ],
)
def test_default_fetcher_combines_stocks_and_etfs(monkeypatch, market, expected_sources, expected_symbols):
    calls: list[str] = []
    listings = {
        "KRX": pd.DataFrame({"Code": ["005930"], "Name": ["삼성전자"]}),
        "ETF/KR": pd.DataFrame({"Symbol": ["069500"], "Name": ["KODEX 200"]}),
        "S&P500": pd.DataFrame({"Symbol": ["AAPL"], "Name": ["Apple"]}),
        "ETF/US": pd.DataFrame({"Symbol": ["SPY"], "Name": ["SPDR S&P 500 ETF Trust"]}),
    }

    def stock_listing(source: str) -> pd.DataFrame:
        calls.append(source)
        return listings[source]

    monkeypatch.setitem(sys.modules, "FinanceDataReader", SimpleNamespace(StockListing=stock_listing))

    result = _default_fetcher(market)

    assert calls == expected_sources
    assert sorted(result["symbol"].tolist()) == sorted(expected_symbols)


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
