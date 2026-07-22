from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.panel import PanelStore, attach_metadata
from tradingbot.data.store import ParquetDataStore
from tradingbot.factors.value import BookToMarketFactor, EarningsYieldFactor

AS_OF = date(2024, 3, 1)


@pytest.fixture
def store(tmp_path):
    return ParquetDataStore(
        ParquetCache(tmp_path / "cache"), "KR", processed_root=tmp_path / "processed"
    )


def write_valuation(store, rows: list[tuple[str, str, float, float]], available: str | None = None):
    """rows: (date, symbol, per, pbr)"""
    frame = pd.DataFrame(
        [
            {"date": pd.Timestamp(d), "symbol": s, "per": per, "pbr": pbr}
            for d, s, per, pbr in rows
        ]
    )
    panel = PanelStore(store.processed_root, "valuation", "KR")
    panel.append(
        attach_metadata(
            frame,
            source="test",
            available_at=pd.Timestamp(available) if available else frame["date"],
            data_version="1",
        )
    )


class TestEarningsYieldFactor:
    def test_inverts_per(self, store):
        write_valuation(store, [("2024-02-28", "AAA", 10.0, 1.0)])
        result = EarningsYieldFactor().compute(AS_OF, ["AAA"], store)
        assert result.name == "earnings_yield"
        assert result.loc["AAA"] == pytest.approx(0.1)

    def test_cheaper_stock_scores_higher(self, store):
        write_valuation(store, [("2024-02-28", "CHEAP", 5.0, 1.0), ("2024-02-28", "RICH", 50.0, 1.0)])
        result = EarningsYieldFactor().compute(AS_OF, ["CHEAP", "RICH"], store)
        assert result.loc["CHEAP"] > result.loc["RICH"]

    def test_uses_the_most_recent_observation(self, store):
        write_valuation(store, [("2024-02-01", "AAA", 20.0, 1.0), ("2024-02-28", "AAA", 10.0, 1.0)])
        result = EarningsYieldFactor().compute(AS_OF, ["AAA"], store)
        assert result.loc["AAA"] == pytest.approx(0.1)

    def test_respects_availability(self, store):
        # Observed before AS_OF but only publishable afterwards.
        write_valuation(store, [("2024-02-28", "AAA", 10.0, 1.0)], available="2024-04-01")
        result = EarningsYieldFactor().compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_missing_per_is_nan(self, store):
        write_valuation(store, [("2024-02-28", "AAA", float("nan"), 1.0)])
        result = EarningsYieldFactor().compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_non_positive_per_is_nan(self, store):
        # A loss-making company has no meaningful earnings yield; 1/-5 would
        # rank it between two profitable companies.
        write_valuation(store, [("2024-02-28", "AAA", -5.0, 1.0), ("2024-02-28", "BBB", 0.0, 1.0)])
        result = EarningsYieldFactor().compute(AS_OF, ["AAA", "BBB"], store)
        assert np.isnan(result.loc["AAA"])
        assert np.isnan(result.loc["BBB"])

    def test_symbol_without_data_is_nan(self, store):
        write_valuation(store, [("2024-02-28", "AAA", 10.0, 1.0)])
        result = EarningsYieldFactor().compute(AS_OF, ["AAA", "ZZZ"], store)
        assert np.isnan(result.loc["ZZZ"])

    def test_no_panel_yields_all_nan(self, store):
        result = EarningsYieldFactor().compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_empty_universe(self, store):
        assert EarningsYieldFactor().compute(AS_OF, [], store).empty


class TestBookToMarketFactor:
    def test_inverts_pbr(self, store):
        write_valuation(store, [("2024-02-28", "AAA", 10.0, 2.0)])
        result = BookToMarketFactor().compute(AS_OF, ["AAA"], store)
        assert result.name == "book_to_market"
        assert result.loc["AAA"] == pytest.approx(0.5)

    def test_non_positive_pbr_is_nan(self, store):
        write_valuation(store, [("2024-02-28", "AAA", 10.0, 0.0)])
        result = BookToMarketFactor().compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])
