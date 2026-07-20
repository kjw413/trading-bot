from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.flows import FLOW_COLUMNS, normalize_flows, update_flows
from tradingbot.data.panel import PanelStore


@pytest.fixture
def store(tmp_path):
    return PanelStore(tmp_path, "flows", "KR")


def fake_fetcher(symbol: str, start: date, end: date) -> pd.DataFrame:
    index = pd.bdate_range(start="2024-01-02", periods=2)
    return pd.DataFrame(
        {
            "date": index,
            "symbol": symbol,
            "foreign_net": [1000.0, -500.0],
            "institution_net": [-200.0, 300.0],
            "individual_net": [-800.0, 200.0],
        }
    )


class TestNormalizeFlows:
    def test_maps_korean_columns_to_english(self):
        raw = pd.DataFrame(
            {"외국인합계": [1000], "기관합계": [-200], "개인": [-800], "전체": [0]},
            index=pd.DatetimeIndex(["2024-01-02"], name="날짜"),
        )
        result = normalize_flows(raw, "005930")
        assert list(result.columns) == ["date", "symbol"] + FLOW_COLUMNS
        assert result.loc[0, "foreign_net"] == 1000.0
        assert result.loc[0, "symbol"] == "005930"

    def test_missing_expected_column_raises(self):
        raw = pd.DataFrame({"외국인합계": [1]}, index=pd.DatetimeIndex(["2024-01-02"]))
        with pytest.raises(ValueError, match="column"):
            normalize_flows(raw, "005930")

    def test_empty_frame_returns_empty_with_schema(self):
        result = normalize_flows(pd.DataFrame(), "005930")
        assert result.empty
        assert list(result.columns) == ["date", "symbol"] + FLOW_COLUMNS


class TestUpdateFlows:
    def test_writes_rows_with_next_day_availability(self, store):
        written = update_flows(store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert written == 2

        panel = store.read()
        first = panel.iloc[0]
        assert first["date"] == pd.Timestamp("2024-01-02")
        assert first["available_at"] == pd.Timestamp("2024-01-03")
        assert first["foreign_net"] == 1000.0
        assert first["source"] == "pykrx"

    def test_as_of_read_hides_future_rows(self, store):
        update_flows(store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(store.read(as_of=date(2024, 1, 3))) == 1

    def test_rerun_is_idempotent(self, store):
        update_flows(store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        update_flows(store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(store.read()) == 2

    def test_one_failing_symbol_does_not_stop_the_rest(self, store):
        def flaky(symbol, start, end):
            if symbol == "BAD":
                raise RuntimeError("boom")
            return fake_fetcher(symbol, start, end)

        written = update_flows(
            store, symbols=["BAD", "005930"], start=date(2024, 1, 1), fetcher=flaky
        )
        assert written == 2
        assert set(store.read()["symbol"]) == {"005930"}

    def test_empty_symbol_list_writes_nothing(self, store):
        assert update_flows(store, symbols=[], start=date(2024, 1, 1), fetcher=fake_fetcher) == 0
