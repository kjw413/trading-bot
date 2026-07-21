from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.macro import MACRO_SERIES, fetch_macro_series, update_macro
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

    def test_defaults_to_all_registered_series(self, store):
        update_macro(store, start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(set(store.read()["symbol"])) == len(MACRO_SERIES)

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
