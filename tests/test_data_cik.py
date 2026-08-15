from __future__ import annotations

import json

import pytest

from tradingbot.data.cik import CIK_CACHE_FILENAME, CikStore, parse_company_tickers


COMPANY_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "2": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
}


class TestParseCompanyTickers:
    def test_maps_ticker_to_cik(self):
        assert parse_company_tickers(COMPANY_TICKERS) == {
            "AAPL": 320193,
            "MSFT": 789019,
            "GOOGL": 1652044,
        }

    def test_tickers_are_upper_cased(self):
        parsed = parse_company_tickers({"0": {"cik_str": 320193, "ticker": "aapl"}})
        assert parsed == {"AAPL": 320193}

    def test_rows_without_a_ticker_are_dropped(self):
        # Some filers have a CIK but no listed ticker.
        parsed = parse_company_tickers(
            {"0": {"cik_str": 1, "ticker": ""}, "1": {"cik_str": 320193, "ticker": "AAPL"}}
        )
        assert parsed == {"AAPL": 320193}

    def test_cik_is_an_int_not_a_zero_padded_string(self):
        # The submissions URL zero-pads to ten digits itself; carrying the
        # padding here would double it.
        assert parse_company_tickers(COMPANY_TICKERS)["AAPL"] == 320193

    def test_empty_payload_gives_an_empty_mapping(self):
        assert parse_company_tickers({}) == {}


class TestCikStore:
    def downloader(self, calls: list[int]):
        def download() -> dict:
            calls.append(1)
            return COMPANY_TICKERS

        return download

    def test_downloads_and_caches(self, tmp_path):
        calls: list[int] = []
        store = CikStore(tmp_path, downloader=self.downloader(calls))
        assert store.cik("AAPL") == 320193
        assert store.path.exists()
        assert len(calls) == 1

    def test_a_second_store_reads_the_cache_without_downloading(self, tmp_path):
        CikStore(tmp_path, downloader=self.downloader([])).load()
        calls: list[int] = []
        assert CikStore(tmp_path, downloader=self.downloader(calls)).cik("MSFT") == 789019
        assert calls == []

    def test_unknown_ticker_is_none(self, tmp_path):
        store = CikStore(tmp_path, downloader=self.downloader([]))
        assert store.cik("NOTATICKER") is None

    def test_cik_for_omits_unknown_symbols(self, tmp_path):
        store = CikStore(tmp_path, downloader=self.downloader([]))
        assert store.cik_for(["AAPL", "NOTATICKER", "MSFT"]) == {
            "AAPL": 320193,
            "MSFT": 789019,
        }

    def test_lookup_is_case_insensitive(self, tmp_path):
        store = CikStore(tmp_path, downloader=self.downloader([]))
        assert store.cik("aapl") == 320193

    def test_fetch_false_never_downloads(self, tmp_path):
        calls: list[int] = []
        store = CikStore(tmp_path, downloader=self.downloader(calls))
        assert store.load(fetch=False) == {}
        assert calls == []

    def test_a_failed_refresh_keeps_the_stale_cache(self, tmp_path):
        # A month-old mapping is still almost entirely correct. Dropping it
        # over a transient outage would stop collection for no gain.
        CikStore(tmp_path, downloader=self.downloader([])).load()

        def boom() -> dict:
            raise RuntimeError("SEC unreachable")

        stale = CikStore(tmp_path, downloader=boom)
        stale.path.touch()  # keep the file, force the freshness path below
        import os
        import time as _time

        os.utime(stale.path, (0, 0))  # make it stale
        assert stale.cik("AAPL") == 320193

    def test_a_failed_first_download_raises(self, tmp_path):
        def boom() -> dict:
            raise RuntimeError("SEC unreachable")

        with pytest.raises(RuntimeError):
            CikStore(tmp_path, downloader=boom).load()

    def test_cache_is_written_as_readable_csv(self, tmp_path):
        store = CikStore(tmp_path, downloader=self.downloader([]))
        store.load()
        text = store.path.read_text(encoding="utf-8")
        assert text.splitlines()[0] == "ticker,cik"
        assert "AAPL,320193" in text

    def test_cache_lives_beside_the_other_listings(self, tmp_path):
        store = CikStore(tmp_path, downloader=self.downloader([]))
        assert store.path.parent.name == "_listings"
        assert store.path.name == CIK_CACHE_FILENAME


class TestPayloadShape:
    def test_a_real_shaped_payload_round_trips(self):
        # Guards against the SEC changing key names under us: the file is a
        # dict keyed by row index, not a list.
        payload = json.loads(json.dumps(COMPANY_TICKERS))
        assert parse_company_tickers(payload)["GOOGL"] == 1652044
