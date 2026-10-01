from __future__ import annotations

import pandas as pd
import pytest

from tradingbot.research.flow_participation import analyze_participation_hypothesis


def sample_data():
    dates = pd.bdate_range("2024-01-02", periods=10)
    participation = [0.10, 0.20, 0.10, 0.30, 0.10, 0.30, 0.10, 0.30, 0.10, 0.30]
    flows = pd.DataFrame(
        {
            "date": dates,
            "symbol": "AAA",
            "institution_net": 100.0,
            "individual_buy": [value * 1_000 for value in participation],
            "individual_sell": [value * 1_000 for value in participation],
            "traded_value": 1_000.0,
        }
    )
    prices = pd.DataFrame(
        {
            "date": dates,
            "symbol": "AAA",
            "open": [100.0] * 10,
            "close": [100.0, 100.0, 100.0, 110.0, 90.0, 120.0, 80.0, 130.0, 70.0, 140.0],
        }
    )
    return flows, prices


def test_study_splits_against_prior_median_and_enters_next_session():
    flows, prices = sample_data()
    result = analyze_participation_hypothesis(
        flows,
        prices,
        horizons=(1,),
        institution_window=2,
        baseline_window=3,
        min_baseline=2,
        round_trip_cost_bps=0,
        event_spacing=0,
    )

    low = result.observations[result.observations["signal_date"] == pd.Timestamp("2024-01-04")]
    assert low.iloc[0]["group"] == "low"
    assert low.iloc[0]["entry_date"] == pd.Timestamp("2024-01-05")
    high = result.observations[result.observations["signal_date"] == pd.Timestamp("2024-01-05")]
    assert high.iloc[0]["group"] == "high"
    assert set(result.summary["group"]) == {"low", "high"}


def test_study_deducts_round_trip_cost_and_spaces_overlapping_events():
    flows, prices = sample_data()
    result = analyze_participation_hypothesis(
        flows,
        prices,
        horizons=(1,),
        institution_window=2,
        baseline_window=3,
        min_baseline=2,
        round_trip_cost_bps=100,
        event_spacing=2,
    )

    first = result.observations.iloc[0]
    assert first["net_return"] == pytest.approx(-0.11)
    entry_positions = [prices.index[prices["date"] == value][0] for value in result.observations["entry_date"]]
    assert all(right - left > 2 for left, right in zip(entry_positions, entry_positions[1:]))


def test_study_rejects_missing_gross_participation_columns():
    flows, prices = sample_data()
    with pytest.raises(ValueError, match="individual_sell"):
        analyze_participation_hypothesis(flows.drop(columns="individual_sell"), prices)
