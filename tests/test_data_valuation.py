from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.panel import PanelStore
from tradingbot.data.valuation import VALUATION_COLUMNS, normalize_valuation, update_valuation


@pytest.fixture
def store(tmp_path):
    return PanelStore(tmp_path, "valuation", "KR")


def fake_fetcher(symbol: str, start: date, end: date) -> pd.DataFrame:
    index = pd.bdate_range(start="2024-01-02", periods=2)
    return pd.DataFrame(
        {
            "date": index,
            "symbol": symbol,
            "per": [10.0, 10.5],
            "pbr": [1.2, 1.3],
            "eps": [5000.0, 5000.0],
            "bps": [40000.0, 40000.0],
            "div_yield": [2.0, 2.0],
        }
    )


class TestNormalizeValuation:
    def test_maps_krx_columns(self):
        raw = pd.DataFrame(
            {"BPS": [40000], "PER": [10.0], "PBR": [1.2], "EPS": [5000], "DIV": [2.0], "DPS": [100]},
            index=pd.DatetimeIndex(["2024-01-02"], name="날짜"),
        )
        result = normalize_valuation(raw, "005930")
        assert list(result.columns) == ["date", "symbol"] + VALUATION_COLUMNS
        assert result.loc[0, "per"] == 10.0
        assert result.loc[0, "div_yield"] == 2.0

    def test_zero_per_becomes_nan(self):
        # KRX reports 0 for loss-making companies; 0 would rank as "cheapest".
        raw = pd.DataFrame(
            {"BPS": [40000], "PER": [0.0], "PBR": [1.2], "EPS": [-100], "DIV": [0.0], "DPS": [0]},
            index=pd.DatetimeIndex(["2024-01-02"]),
        )
        result = normalize_valuation(raw, "005930")
        assert pd.isna(result.loc[0, "per"])

    def test_empty_frame_returns_empty_with_schema(self):
        result = normalize_valuation(pd.DataFrame(), "005930")
        assert result.empty
        assert list(result.columns) == ["date", "symbol"] + VALUATION_COLUMNS


class TestUpdateValuation:
    def test_writes_rows_with_next_day_availability(self, store):
        written = update_valuation(
            store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher
        )
        assert written == 2
        first = store.read().iloc[0]
        assert first["available_at"] == pd.Timestamp("2024-01-03")
        assert first["per"] == 10.0
        assert first["source"] == "pykrx"

    def test_rerun_is_idempotent(self, store):
        update_valuation(store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        update_valuation(store, symbols=["005930"], start=date(2024, 1, 1), fetcher=fake_fetcher)
        assert len(store.read()) == 2

    def test_one_failing_symbol_does_not_stop_the_rest(self, store):
        def flaky(symbol, start, end):
            if symbol == "BAD":
                raise RuntimeError("boom")
            return fake_fetcher(symbol, start, end)

        assert update_valuation(
            store, symbols=["BAD", "005930"], start=date(2024, 1, 1), fetcher=flaky
        ) == 2

    def test_missing_credentials_propagates_not_swallowed(self, store, monkeypatch):
        from tradingbot.data.credentials import MissingCredentialsError

        def unauthenticated(symbol, start, end):
            raise MissingCredentialsError("KRX_ID is not set.")

        # A missing credential is a batch-level config problem: it must surface,
        # not be absorbed per-symbol into a silent zero-row result.
        with pytest.raises(MissingCredentialsError):
            update_valuation(
                store, symbols=["005930"], start=date(2024, 1, 1), fetcher=unauthenticated
            )


class TestFetchValuationCredentialGate:
    def test_missing_credentials_raise_before_any_network_call(self, monkeypatch):
        from tradingbot.data.credentials import MissingCredentialsError
        from tradingbot.data.valuation import fetch_valuation

        monkeypatch.delenv("KRX_ID", raising=False)
        monkeypatch.delenv("KRX_PW", raising=False)

        # The guard must fire in the real fetcher, not just via an injected
        # fake — otherwise moving it below the pykrx call would go unnoticed.
        with pytest.raises(MissingCredentialsError, match="KRX_ID"):
            fetch_valuation("005930", date(2024, 1, 1), date(2024, 1, 10))
