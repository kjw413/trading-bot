"""Tests for the event study harness.

The hard part is not arithmetic, it is trusting the answer. So the main test
plants a known interaction in synthetic prices and checks the table recovers
it, and a companion test plants nothing and checks the table stays flat. A
harness that reports an effect either way is worse than no harness.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.research.event_study import (
    EventWindow,
    abnormal_returns,
    assign_quantiles,
    build_event_panel,
    event_row,
    quantile_table,
    render_markdown,
)

INDEX = pd.bdate_range("2020-01-01", periods=600)


def flat(value: float = 100.0) -> pd.Series:
    return pd.Series(np.full(len(INDEX), value), index=INDEX)


class TestAbnormalReturns:
    def test_a_stock_matching_the_market_has_no_abnormal_return(self):
        prices = pd.Series(np.linspace(100, 200, len(INDEX)), index=INDEX)
        ar = abnormal_returns(prices, prices)
        assert ar.abs().max() < 1e-12

    def test_outperformance_shows_up_as_positive(self):
        stock = pd.Series(np.linspace(100, 200, len(INDEX)), index=INDEX)
        assert abnormal_returns(stock, flat()).sum() > 0

    def test_only_shared_dates_are_used(self):
        # A benchmark missing days must not silently shift the alignment.
        stock = pd.Series(np.linspace(100, 200, len(INDEX)), index=INDEX)
        partial = flat().iloc[::2]
        assert len(abnormal_returns(stock, partial)) == len(partial) - 1

    def test_too_little_overlap_gives_nothing(self):
        assert abnormal_returns(flat().head(1), flat().head(1)).empty


class TestWindows:
    def build_ar(self) -> pd.Series:
        return pd.Series(np.full(len(INDEX), 0.001), index=INDEX)

    def test_windows_do_not_overlap_the_reaction_day(self):
        ar = self.build_ar()
        day_zero = INDEX[300].date()
        row = event_row("X", day_zero, ar, EventWindow(-10, -1, 1, 10))
        # 10 days before, 1 day on, 10 days after, all at 0.001.
        assert row["pre_car"] == pytest.approx(0.010)
        assert row["reaction_ar"] == pytest.approx(0.001)
        assert row["post_car"] == pytest.approx(0.010)

    def test_a_truncated_window_is_nan_not_a_partial_sum(self):
        # An event five days from the start has no sixty-day run-up. Summing
        # what exists would make it look like a calm name.
        ar = self.build_ar()
        row = event_row("X", INDEX[5].date(), ar, EventWindow())
        assert np.isnan(row["pre_car"])

    def test_an_event_past_the_end_is_nan(self):
        ar = self.build_ar()
        row = event_row("X", INDEX[-2].date(), ar, EventWindow())
        assert np.isnan(row["post_car"])

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"pre_end": 0},        # would double-count the reaction day
            {"post_start": 0},     # same
            {"pre_start": 1},      # inverted
        ],
    )
    def test_overlapping_or_inverted_windows_are_rejected(self, kwargs):
        with pytest.raises(ValueError):
            EventWindow(**kwargs)


class TestQuantiles:
    def test_lowest_values_land_in_bucket_one(self):
        values = pd.Series(range(100), dtype=float)
        buckets = assign_quantiles(values, 5)
        assert buckets.iloc[0] == 1
        assert buckets.iloc[-1] == 5

    def test_a_group_too_small_to_split_is_nan(self):
        # Three events cut into quintiles says nothing; forcing them into
        # buckets would manufacture a spread out of noise.
        values = pd.Series([1.0, 2.0, 3.0])
        assert assign_quantiles(values, 5).isna().all()

    def test_grouping_ranks_within_the_group(self):
        # Every 2021 value is larger than every 2020 value. Ungrouped, 2021
        # fills the top buckets; grouped, each year spans all five.
        values = pd.Series(list(range(50)) + list(range(1000, 1050)), dtype=float)
        years = pd.Series([2020] * 50 + [2021] * 50)
        grouped = assign_quantiles(values, 5, years)
        assert set(grouped[years == 2020].dropna().unique()) == {1, 2, 3, 4, 5}
        assert set(grouped[years == 2021].dropna().unique()) == {1, 2, 3, 4, 5}


def synthetic_events(n_per_bucket: int, effect: float, seed: int = 0) -> pd.DataFrame:
    """A panel with a planted interaction.

    High run-up plus a weak reaction is given `effect` of extra drift; every
    other combination gets none. With effect=0 the panel is pure noise.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for pre_high in (False, True):
        for weak_reaction in (False, True):
            for _ in range(n_per_bucket):
                pre = rng.normal(0.30 if pre_high else -0.30, 0.05)
                reaction = rng.normal(-0.04 if weak_reaction else 0.04, 0.01)
                drift = rng.normal(0.0, 0.01)
                if pre_high and weak_reaction:
                    drift += effect
                rows.append(
                    {
                        "symbol": "X",
                        "reaction_date": pd.Timestamp("2022-01-03"),
                        "pre_car": pre,
                        "reaction_ar": reaction,
                        "post_car": drift,
                    }
                )
    return pd.DataFrame(rows)


class TestTheHarnessFindsAPlantedEffect:
    def test_a_real_interaction_shows_up_in_the_corner(self):
        panel = synthetic_events(200, effect=-0.05)
        table = quantile_table(panel, n_quantiles=2, group_by_year=False)
        cell = table.set_index(["pre_car", "reaction_ar"])["mean"]
        # High run-up (bucket 2), weak reaction (bucket 1) is the planted cell.
        assert cell[(2.0, 1.0)] < cell[(2.0, 2.0)] - 0.03
        assert cell[(2.0, 1.0)] < cell[(1.0, 1.0)] - 0.03

    def test_no_effect_leaves_the_table_flat(self):
        # The other half of trusting the answer. A harness that finds a corner
        # in pure noise would have "confirmed" every hypothesis put to it.
        panel = synthetic_events(200, effect=0.0, seed=7)
        table = quantile_table(panel, n_quantiles=2, group_by_year=False)
        spread = table["mean"].max() - table["mean"].min()
        assert spread < 0.01

    def test_every_cell_reports_its_sample_size(self):
        table = quantile_table(synthetic_events(50, effect=-0.05), n_quantiles=2, group_by_year=False)
        assert (table["count"] > 0).all()
        assert table["count"].sum() == 200


class TestBuildEventPanel:
    def test_measures_one_row_per_event(self):
        prices = {"AAA": pd.Series(np.linspace(100, 200, len(INDEX)), index=INDEX)}
        events = pd.DataFrame(
            {"symbol": ["AAA", "AAA"], "reaction_date": [INDEX[200], INDEX[300]]}
        )
        panel = build_event_panel(events, prices, flat())
        assert len(panel) == 2
        assert panel["pre_car"].notna().all()

    def test_a_symbol_without_prices_is_dropped(self):
        events = pd.DataFrame({"symbol": ["GHOST"], "reaction_date": [INDEX[300]]})
        assert build_event_panel(events, {}, flat()).empty

    def test_an_empty_event_set_gives_an_empty_panel_with_columns(self):
        panel = build_event_panel(
            pd.DataFrame(columns=["symbol", "reaction_date"]), {}, flat()
        )
        assert panel.empty
        assert "post_car" in panel.columns


class TestRendering:
    def test_the_table_carries_its_own_caveats(self):
        panel = synthetic_events(200, effect=-0.05)
        text = render_markdown(quantile_table(panel, n_quantiles=2, group_by_year=False), panel)
        assert "결론이 될 수 없다" in text
        assert "기술통계" in text
        assert "n=" in text

    def test_an_unmeasurable_panel_says_so_rather_than_printing_nothing(self):
        empty = pd.DataFrame(columns=["symbol", "reaction_date", "pre_car", "reaction_ar", "post_car"])
        text = render_markdown(pd.DataFrame(), empty)
        assert "측정할 수 있는 이벤트가 없습니다" in text
