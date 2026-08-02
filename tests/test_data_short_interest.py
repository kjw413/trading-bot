"""Short-selling panels.

The test that matters most is the availability one: balance and volume
describe the same days but become public at different times, and getting
that wrong produces a backtest that looks better, not one that fails.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.panel import PanelStore
from tradingbot.data.short_interest import (
    SHORT_BALANCE_LAG_DAYS,
    SHORT_VOLUME_LAG_DAYS,
    normalize_short_balance,
    normalize_short_volume,
    update_short_balance,
    update_short_volume,
)


def balance_raw(days: list[str], qty=5_000_000.0, shares=100_000_000.0) -> pd.DataFrame:
    """Mirrors get_shorting_balance_by_date's documented output."""
    index = pd.DatetimeIndex([pd.Timestamp(day) for day in days], name="날짜")
    return pd.DataFrame(
        {
            "공매도잔고": [qty] * len(days),
            "상장주식수": [shares] * len(days),
            "공매도금액": [qty * 100.0] * len(days),
            "시가총액": [shares * 100.0] * len(days),
            "비중": [round(qty / shares, 4) if shares else 0.0] * len(days),
        },
        index=index,
    )


def volume_raw(days: list[str], short=1_000.0, total=50_000.0) -> pd.DataFrame:
    """Mirrors get_shorting_volume_by_date's documented output."""
    index = pd.DatetimeIndex([pd.Timestamp(day) for day in days], name="날짜")
    return pd.DataFrame(
        {"공매도": [short] * len(days), "매수": [total] * len(days), "비중": [0.02] * len(days)},
        index=index,
    )


def fetcher_for(raw_builder, calls=None):
    """A fake fetcher. Like the real ones, it returns a *normalized* frame —
    `fetch_short_balance` calls its normalizer before handing data back."""
    normalize = (
        normalize_short_balance if raw_builder is balance_raw else normalize_short_volume
    )

    def fetch(symbol: str, start: date, end: date) -> pd.DataFrame:
        if calls is not None:
            calls.append((symbol, start, end))
        days = pd.bdate_range(pd.Timestamp(start), pd.Timestamp(end))
        raw = raw_builder([d.strftime("%Y-%m-%d") for d in days])
        return normalize(raw, symbol)

    return fetch


class TestNormalizeBalance:
    def test_maps_columns_and_recomputes_the_ratio(self):
        frame = normalize_short_balance(
            balance_raw(["2024-01-02"], qty=5_000_000.0, shares=100_000_000.0), "005930"
        )

        assert list(frame.columns)[:2] == ["date", "symbol"]
        assert frame.loc[0, "symbol"] == "005930"
        assert frame.loc[0, "short_balance_qty"] == 5_000_000.0
        assert frame.loc[0, "short_balance_ratio"] == pytest.approx(0.05)

    def test_ratio_is_computed_not_taken_from_the_rounded_krx_column(self):
        """KRX's 비중 is rounded; the factor's whole signal lives in the tail."""
        raw = balance_raw(["2024-01-02"], qty=123_456.0, shares=100_000_000.0)
        raw["비중"] = 0.0012  # rounded

        frame = normalize_short_balance(raw, "005930")

        assert frame.loc[0, "short_balance_ratio"] == pytest.approx(0.00123456)

    def test_zero_shares_outstanding_yields_nan_not_zero(self):
        """A missing denominator must not rank the name as least-shorted."""
        frame = normalize_short_balance(balance_raw(["2024-01-02"], shares=0.0), "005930")

        assert pd.isna(frame.loc[0, "short_balance_ratio"])

    def test_empty_input_returns_the_schema(self):
        frame = normalize_short_balance(pd.DataFrame(), "005930")

        assert frame.empty
        assert "short_balance_ratio" in frame.columns

    def test_missing_column_is_an_error(self):
        raw = balance_raw(["2024-01-02"]).drop(columns=["상장주식수"])

        with pytest.raises(ValueError, match="Short balance response is missing"):
            normalize_short_balance(raw, "005930")


class TestNormalizeVolume:
    def test_maps_columns_and_computes_the_ratio(self):
        frame = normalize_short_volume(
            volume_raw(["2024-01-02"], short=1_000.0, total=50_000.0), "005930"
        )

        assert frame.loc[0, "short_volume"] == 1_000.0
        assert frame.loc[0, "total_volume"] == 50_000.0
        assert frame.loc[0, "short_volume_ratio"] == pytest.approx(0.02)

    def test_zero_volume_yields_nan(self):
        frame = normalize_short_volume(volume_raw(["2024-01-02"], total=0.0), "005930")

        assert pd.isna(frame.loc[0, "short_volume_ratio"])

    def test_missing_column_is_an_error(self):
        raw = volume_raw(["2024-01-02"]).drop(columns=["매수"])

        with pytest.raises(ValueError, match="Short volume response is missing"):
            normalize_short_volume(raw, "005930")


class TestAvailabilityLag:
    """The reason these are two panels and not one."""

    def test_balance_is_not_usable_until_after_its_disclosure_lag(self, tmp_path):
        store = PanelStore(tmp_path, "short_balance", "KR")
        update_short_balance(
            store,
            symbols=["005930"],
            start=date(2024, 1, 2),
            end=date(2024, 1, 2),
            fetcher=fetcher_for(balance_raw),
        )

        row = store.read().iloc[0]
        # Tue 2024-01-02 + 3 trading days -> Fri 2024-01-05
        assert row["date"] == pd.Timestamp("2024-01-02")
        assert row["available_at"] == pd.Timestamp("2024-01-05")

    def test_volume_is_usable_the_next_trading_day(self, tmp_path):
        store = PanelStore(tmp_path, "short_volume", "KR")
        update_short_volume(
            store,
            symbols=["005930"],
            start=date(2024, 1, 2),
            end=date(2024, 1, 2),
            fetcher=fetcher_for(volume_raw),
        )

        row = store.read().iloc[0]
        assert row["available_at"] == pd.Timestamp("2024-01-03")

    def test_balance_lag_is_strictly_longer_than_volume_lag(self):
        """If these ever become equal, the separation has silently collapsed."""
        assert SHORT_BALANCE_LAG_DAYS > SHORT_VOLUME_LAG_DAYS

    def test_the_two_series_never_share_a_panel(self, tmp_path):
        """Same dataset name would let one series inherit the other's lag."""
        balance_store = PanelStore(tmp_path, "short_balance", "KR")
        volume_store = PanelStore(tmp_path, "short_volume", "KR")
        update_short_balance(
            balance_store, symbols=["005930"], start=date(2024, 1, 2),
            end=date(2024, 1, 2), fetcher=fetcher_for(balance_raw),
        )
        update_short_volume(
            volume_store, symbols=["005930"], start=date(2024, 1, 2),
            end=date(2024, 1, 2), fetcher=fetcher_for(volume_raw),
        )

        assert balance_store.read().iloc[0]["available_at"] == pd.Timestamp("2024-01-05")
        assert volume_store.read().iloc[0]["available_at"] == pd.Timestamp("2024-01-03")


class TestCollection:
    def test_incremental_run_resumes_from_the_newest_row(self, tmp_path):
        store = PanelStore(tmp_path, "short_balance", "KR")
        calls: list[tuple[str, date, date]] = []
        fetch = fetcher_for(balance_raw, calls)
        update_short_balance(
            store, symbols=["005930"], start=date(2024, 1, 2), end=date(2024, 1, 31),
            fetcher=fetch,
        )
        calls.clear()

        update_short_balance(
            store, symbols=["005930"], start=date(2024, 1, 2), end=date(2024, 2, 29),
            fetcher=fetch,
        )

        assert calls[0][1] > date(2024, 1, 31)

    def test_backfill_reaches_a_window_before_existing_data(self, tmp_path):
        store = PanelStore(tmp_path, "short_balance", "KR")
        calls: list[tuple[str, date, date]] = []
        fetch = fetcher_for(balance_raw, calls)
        update_short_balance(
            store, symbols=["005930"], start=date(2024, 1, 2), end=date(2024, 1, 31),
            fetcher=fetch,
        )
        calls.clear()

        update_short_balance(
            store, symbols=["005930"], start=date(2021, 1, 4), end=date(2021, 1, 29),
            backfill=True, fetcher=fetch,
        )

        assert calls == [("005930", date(2021, 1, 4), date(2021, 1, 29))]
        years = set(pd.to_datetime(store.read()["date"]).dt.year)
        assert years == {2021, 2024}

    def test_one_failing_symbol_does_not_abort_the_batch(self, tmp_path):
        store = PanelStore(tmp_path, "short_balance", "KR")

        def flaky(symbol: str, start: date, end: date) -> pd.DataFrame:
            if symbol == "BAD":
                raise RuntimeError("KRX timeout")
            return normalize_short_balance(balance_raw(["2024-01-02"]), symbol)

        written = update_short_balance(
            store, symbols=["BAD", "005930"], start=date(2024, 1, 2),
            end=date(2024, 1, 2), fetcher=flaky,
        )

        assert written > 0
        assert set(store.read()["symbol"]) == {"005930"}
