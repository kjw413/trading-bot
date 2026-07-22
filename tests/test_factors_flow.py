from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.panel import PanelStore, attach_metadata
from tradingbot.data.store import ParquetDataStore
from tradingbot.factors.flow import NetBuyIntensityFactor

AS_OF = date(2024, 3, 1)


@pytest.fixture
def store(tmp_path):
    return ParquetDataStore(
        ParquetCache(tmp_path / "cache"), "KR", processed_root=tmp_path / "processed"
    )


def write_flows(store, symbol: str, foreign: list[float], end: date = AS_OF) -> None:
    index = pd.bdate_range(end=pd.Timestamp(end), periods=len(foreign))
    frame = pd.DataFrame(
        {
            "date": index,
            "symbol": symbol,
            "foreign_net": foreign,
            "institution_net": [0.0] * len(foreign),
            "individual_net": [0.0] * len(foreign),
        }
    )
    panel = PanelStore(store.processed_root, "flows", "KR")
    panel.append(
        attach_metadata(
            frame,
            source="test",
            # Same-day availability keeps the fixture simple; the PIT barrier
            # itself is covered by the dedicated look-ahead test below.
            available_at=frame["date"],
            data_version="1",
        )
    )


def write_prices(store, symbol: str, closes: list[float], volume: float, end: date = AS_OF) -> None:
    index = pd.bdate_range(end=pd.Timestamp(end), periods=len(closes))
    store.cache.write(
        "KR",
        symbol,
        pd.DataFrame(
            {
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [volume] * len(closes),
            },
            index=index,
        ),
    )


class TestNetBuyIntensityFactor:
    def test_positive_flow_scores_positive(self, store):
        write_flows(store, "AAA", [100.0] * 20)
        write_prices(store, "AAA", [10.0] * 20, volume=100.0)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert result.name == "foreign_net_20d"
        # 20 days x 100 net buy / (20 days x 10 price x 100 volume) = 0.1
        assert result.loc["AAA"] == pytest.approx(0.1)

    def test_selling_scores_negative(self, store):
        write_flows(store, "AAA", [-100.0] * 20)
        write_prices(store, "AAA", [10.0] * 20, volume=100.0)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert result.loc["AAA"] < 0

    def test_scales_out_size(self, store):
        # Same relative flow, ten times the traded value: identical score.
        write_flows(store, "SMALL", [100.0] * 20)
        write_prices(store, "SMALL", [10.0] * 20, volume=100.0)
        write_flows(store, "BIG", [1000.0] * 20)
        write_prices(store, "BIG", [10.0] * 20, volume=1000.0)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["SMALL", "BIG"], store)
        assert result.loc["SMALL"] == pytest.approx(result.loc["BIG"])

    def test_no_lookahead_past_the_as_of_date(self, store):
        # A huge buy recorded after AS_OF must not move the score.
        write_flows(store, "AAA", [100.0] * 20)
        write_prices(store, "AAA", [10.0] * 20, volume=100.0)
        baseline = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)

        future = date(2024, 4, 1)
        write_flows(store, "AAA", [999999.0] * 5, end=future)
        after = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert after.loc["AAA"] == pytest.approx(baseline.loc["AAA"])

    def test_missing_flows_is_nan(self, store):
        write_prices(store, "AAA", [10.0] * 20, volume=100.0)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_missing_prices_is_nan(self, store):
        write_flows(store, "AAA", [100.0] * 20)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_zero_traded_value_is_nan_not_infinite(self, store):
        write_flows(store, "AAA", [100.0] * 20)
        write_prices(store, "AAA", [10.0] * 20, volume=0.0)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_institution_investor_variant(self, store):
        write_flows(store, "AAA", [100.0] * 20)
        write_prices(store, "AAA", [10.0] * 20, volume=100.0)
        result = NetBuyIntensityFactor("institution", 20).compute(AS_OF, ["AAA"], store)
        assert result.name == "institution_net_20d"
        assert result.loc["AAA"] == pytest.approx(0.0)  # fixture writes zeros

    def test_unknown_investor_rejected(self):
        with pytest.raises(ValueError, match="investor"):
            NetBuyIntensityFactor("martian", 20)

    def test_invalid_days_rejected(self):
        with pytest.raises(ValueError):
            NetBuyIntensityFactor("foreign", 0)

    def test_empty_universe(self, store):
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, [], store)
        assert result.empty

    def test_partial_flow_history_is_nan_not_a_shrunken_score(self, store):
        # Only 5 days of flows but 20 days of prices: summing 5 days of buying
        # over 20 days of turnover would look like weak accumulation rather
        # than missing data.
        write_flows(store, "AAA", [100.0] * 5)
        write_prices(store, "AAA", [10.0] * 20, volume=100.0)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_partial_price_history_is_nan(self, store):
        write_flows(store, "AAA", [100.0] * 20)
        write_prices(store, "AAA", [10.0] * 5, volume=100.0)
        result = NetBuyIntensityFactor("foreign", 20).compute(AS_OF, ["AAA"], store)
        assert np.isnan(result.loc["AAA"])

    def test_individual_investor_variant(self, store):
        write_flows(store, "AAA", [100.0] * 20)
        write_prices(store, "AAA", [10.0] * 20, volume=100.0)
        result = NetBuyIntensityFactor("individual", 20).compute(AS_OF, ["AAA"], store)
        assert result.name == "individual_net_20d"
        assert not np.isnan(result.loc["AAA"])
