from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.panel import PanelStore, attach_metadata
from tradingbot.data.store import ParquetDataStore
from tradingbot.research.regime import BEAR, BULL, UNKNOWN, equity_exposure, market_regime

AS_OF = date(2024, 3, 1)


@pytest.fixture
def store(tmp_path):
    return ParquetDataStore(
        ParquetCache(tmp_path / "cache"), "KR", processed_root=tmp_path / "processed"
    )


def write_macro(store, closes: list[float], series: str = "kospi", end: date = AS_OF) -> None:
    index = pd.bdate_range(end=pd.Timestamp(end), periods=len(closes))
    frame = pd.DataFrame({"date": index, "symbol": series, "close": closes})
    PanelStore(store.processed_root, "macro", "KR").append(
        attach_metadata(frame, source="test", available_at=frame["date"], data_version="1")
    )


class TestMarketRegime:
    def test_price_above_moving_average_is_bull(self, store):
        write_macro(store, [100.0] * 200 + [150.0])
        assert market_regime(store, AS_OF, ma_days=200) == BULL

    def test_price_below_moving_average_is_bear(self, store):
        write_macro(store, [100.0] * 200 + [50.0])
        assert market_regime(store, AS_OF, ma_days=200) == BEAR

    def test_insufficient_history_is_unknown(self, store):
        write_macro(store, [100.0] * 10)
        assert market_regime(store, AS_OF, ma_days=200) == UNKNOWN

    def test_no_macro_data_is_unknown(self, store):
        assert market_regime(store, AS_OF) == UNKNOWN

    def test_respects_the_as_of_barrier(self, store):
        write_macro(store, [100.0] * 200 + [150.0])
        # Before any observation exists, the regime is unknowable.
        assert market_regime(store, date(2020, 1, 1), ma_days=200) == UNKNOWN

    def test_unknown_series_is_unknown_not_error(self, store):
        write_macro(store, [100.0] * 201)
        assert market_regime(store, AS_OF, series="nasdaq", ma_days=200) == UNKNOWN


class TestEquityExposure:
    def test_full_exposure_in_a_bull(self):
        assert equity_exposure(BULL) == pytest.approx(1.0)

    def test_reduced_exposure_in_a_bear(self):
        assert equity_exposure(BEAR) == pytest.approx(0.5)

    def test_unknown_does_not_reduce_exposure(self):
        # Treating "no data" as bearish would quietly park the strategy in cash.
        assert equity_exposure(UNKNOWN) == pytest.approx(1.0)

    def test_custom_levels(self):
        assert equity_exposure(BEAR, bear=0.25) == pytest.approx(0.25)
