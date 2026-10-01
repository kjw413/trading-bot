from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.flows import FLOW_COLUMNS, normalize_flows, update_flows
from tradingbot.data.panel import PanelStore, attach_metadata


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

    def test_maps_gross_individual_trading_and_total_value(self):
        index = pd.DatetimeIndex(["2024-01-02"], name="날짜")
        net = pd.DataFrame(
            {"외국인합계": [100], "기관합계": [200], "개인": [-300]}, index=index
        )
        buys = pd.DataFrame({"개인": [400], "전체": [1000]}, index=index)
        sells = pd.DataFrame({"개인": [700], "전체": [1002]}, index=index)

        result = normalize_flows(net, "005930", buys=buys, sells=sells)

        assert result.loc[0, "individual_buy"] == 400.0
        assert result.loc[0, "individual_sell"] == 700.0
        assert result.loc[0, "traded_value"] == 1001.0

    def test_missing_gross_columns_raise_instead_of_inventing_participation(self):
        index = pd.DatetimeIndex(["2024-01-02"])
        net = pd.DataFrame(
            {"외국인합계": [100], "기관합계": [200], "개인": [-300]}, index=index
        )
        with pytest.raises(ValueError, match="individual_buy"):
            normalize_flows(net, "005930", buys=pd.DataFrame({"개인": [400]}, index=index))

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

    def test_old_net_only_panel_is_backfilled_from_requested_start(self, store):
        old = fake_fetcher("005930", date(2024, 1, 1), date(2024, 1, 3))
        store.append(
            attach_metadata(
                old,
                source="pykrx",
                available_at="2024-01-03",
                data_version="1",
            )
        )
        calls = []

        def recording_fetcher(symbol, start, end):
            calls.append((symbol, start, end))
            return fake_fetcher(symbol, start, end).assign(
                individual_buy=100.0,
                individual_sell=200.0,
                traded_value=500.0,
            )

        update_flows(
            store,
            symbols=["005930"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 3),
            fetcher=recording_fetcher,
        )

        assert calls[0][1] == date(2024, 1, 1)
        assert store.read()["individual_buy"].notna().all()

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

    def test_all_attempted_symbols_failing_raises_with_last_cause(self, store):
        def unavailable(symbol, start, end):
            raise OSError(f"maintenance: {symbol}")

        with pytest.raises(
            RuntimeError, match="2 symbols were attempted and all failed"
        ) as exc_info:
            update_flows(
                store,
                symbols=["005930", "000660"],
                start=date(2024, 1, 1),
                fetcher=unavailable,
            )

        assert isinstance(exc_info.value.__cause__, OSError)
        assert str(exc_info.value.__cause__) == "maintenance: 000660"

    def test_already_current_symbols_are_not_attempted(self, store):
        def complete_fetcher(symbol, start, end):
            return fake_fetcher(symbol, start, end).assign(
                individual_buy=100.0,
                individual_sell=200.0,
                traded_value=500.0,
            )

        update_flows(
            store,
            symbols=["005930"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 3),
            fetcher=complete_fetcher,
        )

        def must_not_fetch(symbol, start, end):
            raise AssertionError("already-current symbol was fetched")

        assert (
            update_flows(
                store,
                symbols=["005930"],
                end=date(2024, 1, 3),
                fetcher=must_not_fetch,
            )
            == 0
        )

    def test_empty_symbol_list_writes_nothing(self, store):
        assert update_flows(store, symbols=[], start=date(2024, 1, 1), fetcher=fake_fetcher) == 0

    def test_missing_credentials_propagates_not_swallowed(self, store, monkeypatch):
        from tradingbot.data.credentials import MissingCredentialsError

        def unauthenticated(symbol, start, end):
            raise MissingCredentialsError("KRX_ID is not set.")

        # A missing credential is a batch-level config problem: it must surface,
        # not be absorbed per-symbol into a silent zero-row result.
        with pytest.raises(MissingCredentialsError):
            update_flows(
                store, symbols=["005930"], start=date(2024, 1, 1), fetcher=unauthenticated
            )


class TestFetchFlowsCredentialGate:
    def test_missing_credentials_raise_before_any_network_call(self, monkeypatch):
        from tradingbot.data.credentials import MissingCredentialsError
        from tradingbot.data.flows import fetch_flows

        monkeypatch.delenv("KRX_ID", raising=False)
        monkeypatch.delenv("KRX_PW", raising=False)

        # The guard must fire in the real fetcher, not just via an injected
        # fake — otherwise moving it below the pykrx call would go unnoticed.
        with pytest.raises(MissingCredentialsError, match="KRX_ID"):
            fetch_flows("005930", date(2024, 1, 1), date(2024, 1, 10))
