from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.macro import (
    MACRO_SERIES,
    MACRO_SERIES_BY_MARKET,
    fetch_macro_series,
    update_macro,
)
from tradingbot.data.panel import PanelStore


@pytest.fixture
def store(tmp_path):
    return PanelStore(tmp_path, "macro", "KR")


def fake_fetcher(series: str, start: date, end: date | None = None) -> pd.DataFrame:
    """Two business days of synthetic data, independent of the network."""
    index = pd.bdate_range(start="2024-01-02", periods=2)
    return pd.DataFrame({"date": index, "symbol": series, "close": [100.0, 101.0]})


class TestMacroSeries:
    def test_core_series_are_registered(self):
        for expected in ["kospi", "kosdaq", "usdkrw", "vix"]:
            assert expected in MACRO_SERIES

    def test_series_are_scoped_by_market(self):
        assert "kospi" in MACRO_SERIES_BY_MARKET["KR"]
        assert "sp500" in MACRO_SERIES_BY_MARKET["US"]
        # A US panel must never be filled with Korean indices.
        assert "kospi" not in MACRO_SERIES_BY_MARKET["US"]
        assert "sp500" not in MACRO_SERIES_BY_MARKET["KR"]

    def test_flat_lookup_covers_every_market(self):
        for series in MACRO_SERIES_BY_MARKET.values():
            for name, symbol in series.items():
                assert MACRO_SERIES[name] == symbol

    def test_conflicting_symbol_for_a_shared_name_is_rejected(self, monkeypatch):
        from tradingbot.data import macro

        monkeypatch.setattr(
            macro,
            "MACRO_SERIES_BY_MARKET",
            {"KR": {"vix": "VIX"}, "US": {"vix": "SOMETHING_ELSE"}},
        )
        # One name must mean one symbol; a silent overwrite would repoint
        # every market's lookup at whichever market was defined last.
        with pytest.raises(ValueError, match="vix"):
            macro._build_symbol_lookup()


class TestUpdateMacro:
    def test_writes_rows_with_availability_shifted_forward(self, store):
        written = update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert written == 2

        panel = store.read()
        assert set(panel["symbol"]) == {"KOSPI"}
        first = panel.iloc[0]
        assert first["date"] == pd.Timestamp("2024-01-02")
        # Data for Jan 2 is only usable from Jan 3 — no same-day look-ahead.
        assert first["available_at"] == pd.Timestamp("2024-01-03")
        assert first["source"] == "financedatareader"

    def test_as_of_read_hides_future_rows(self, store):
        update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(store.read(as_of=date(2024, 1, 3))) == 1

    def test_rerun_is_idempotent(self, store):
        update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(store.read()) == 2

    def test_defaults_to_the_markets_own_series(self, store):
        update_macro(store, start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(set(store.read()["symbol"])) == len(MACRO_SERIES_BY_MARKET["KR"])

    def test_unknown_series_raises_with_available_names(self, store):
        with pytest.raises(ValueError, match="Available:"):
            update_macro(store, series=["nope"], start=date(2024, 1, 1), fetcher=fake_fetcher)

    def test_empty_response_writes_nothing(self, store):
        def empty_fetcher(series, start, end=None):
            return pd.DataFrame(columns=["date", "symbol", "close"])

        assert update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=empty_fetcher) == 0
        assert store.read().empty

    def test_one_failing_series_does_not_stop_the_rest(self, store):
        def flaky(series, start, end=None):
            if series == "kospi":
                raise RuntimeError("dead ticker")
            return fake_fetcher(series, start, end)

        # A single dead series must not take down the whole macro source —
        # a batch that reports red every day trains the operator to ignore it.
        written = update_macro(
            store, series=["kospi", "kosdaq"], start=date(2024, 1, 1), fetcher=flaky
        )
        assert written == 2
        assert set(store.read()["symbol"]) == {"KOSDAQ"}

    def test_incremental_resumes_after_last_stored_date(self, store):
        captured: list[date] = []

        def recording_fetcher(series, start, end=None):
            captured.append(start)
            return fake_fetcher(series, start, end)

        update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=recording_fetcher)
        update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=recording_fetcher)
        # Second run resumes from the day after the last stored observation.
        assert captured[1] == date(2024, 1, 4)

    def test_a_series_already_current_is_not_fetched_again(self, store):
        # Re-running on the same day puts fetch_start a day past `end`. Yahoo
        # answers an inverted range with a 400, so the run ends in a logged
        # traceback that reads like a real outage. The other collectors skip
        # this case; macro has to as well.
        captured: list[tuple[date, date | None]] = []

        def recording_fetcher(series, start, end=None):
            captured.append((start, end))
            return fake_fetcher(series, start, end)

        update_macro(
            store,
            series=["kospi"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 3),
            fetcher=recording_fetcher,
        )
        update_macro(
            store,
            series=["kospi"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 3),
            fetcher=recording_fetcher,
        )
        assert len(captured) == 1

    def test_an_open_ended_run_still_skips_a_current_series(self, store):
        # `end=None` means "up to now", which is the pipeline's own call.
        # The guard has to resolve it rather than compare against None.
        captured: list[date] = []

        def recording_fetcher(series, start, end=None):
            captured.append(start)
            return pd.DataFrame(
                {
                    "date": [pd.Timestamp(date.today())],
                    "symbol": series,
                    "close": [100.0],
                }
            )

        update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=recording_fetcher)
        update_macro(store, series=["kospi"], start=date(2024, 1, 1), fetcher=recording_fetcher)
        assert len(captured) == 1


class TestFetchMacroSeries:
    def test_normalizes_fdr_frame(self, monkeypatch):
        raw = pd.DataFrame(
            {"Close": [10.0, 11.0]},
            index=pd.DatetimeIndex(["2024-01-02", "2024-01-03"], name="Date"),
        )
        monkeypatch.setattr("FinanceDataReader.DataReader", lambda *a, **k: raw)
        result = fetch_macro_series("kospi", date(2024, 1, 1))
        assert list(result.columns) == ["date", "symbol", "close"]
        assert result.loc[0, "symbol"] == "kospi"
        assert result.loc[0, "close"] == 10.0

    def test_missing_close_column_raises(self, monkeypatch):
        raw = pd.DataFrame({"Open": [1.0]}, index=pd.DatetimeIndex(["2024-01-02"]))
        monkeypatch.setattr("FinanceDataReader.DataReader", lambda *a, **k: raw)
        with pytest.raises(ValueError, match="close"):
            fetch_macro_series("kospi", date(2024, 1, 1))


class TestMarketScopedCollection:
    def test_us_store_collects_only_us_series(self, tmp_path):
        store = PanelStore(tmp_path, "macro", "US")
        update_macro(store, start=date(2024, 1, 1), fetcher=fake_fetcher)
        collected = {symbol.upper() for symbol in store.read()["symbol"]}
        assert collected == {name.upper() for name in MACRO_SERIES_BY_MARKET["US"]}

    def test_kr_store_collects_only_kr_series(self, store):
        update_macro(store, start=date(2024, 1, 1), fetcher=fake_fetcher)
        collected = {symbol.upper() for symbol in store.read()["symbol"]}
        assert collected == {name.upper() for name in MACRO_SERIES_BY_MARKET["KR"]}

    def test_unknown_market_fails_loudly(self, tmp_path):
        store = PanelStore(tmp_path, "macro", "JP")
        # Silently collecting nothing would look like a quiet day forever.
        with pytest.raises(ValueError, match="JP"):
            update_macro(store, start=date(2024, 1, 1), fetcher=fake_fetcher)

    def test_explicit_series_still_honored(self, tmp_path):
        store = PanelStore(tmp_path, "macro", "US")
        update_macro(store, series=["vix"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert set(store.read()["symbol"]) == {"VIX"}
