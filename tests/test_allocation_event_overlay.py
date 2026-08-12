from __future__ import annotations

import pytest

from tradingbot.allocation.event_overlay import reduce_for_events

WEIGHTS = {"AAA": 0.4, "BBB": 0.3, "CCC": 0.2}


def reduced(days: dict[str, int | None], window_days: int = 5, scale: float = 0.5):
    return reduce_for_events(
        WEIGHTS, days_to_event=days, window_days=window_days, scale=scale
    )


class TestReduceForEvents:
    def test_a_name_inside_the_window_is_scaled_down(self):
        assert reduced({"AAA": 3})["AAA"] == pytest.approx(0.2)

    def test_a_name_outside_the_window_is_untouched(self):
        assert reduced({"AAA": 30})["AAA"] == pytest.approx(0.4)

    def test_the_boundary_day_is_inside_the_window(self):
        assert reduced({"AAA": 5})["AAA"] == pytest.approx(0.2)

    def test_the_day_after_the_boundary_is_outside(self):
        assert reduced({"AAA": 6})["AAA"] == pytest.approx(0.4)

    def test_the_event_day_itself_is_inside(self):
        assert reduced({"AAA": 0})["AAA"] == pytest.approx(0.2)

    def test_unknown_schedule_leaves_the_weight_alone(self):
        # None means "we don't know", which is not a reason to trade.
        assert reduced({"AAA": None})["AAA"] == pytest.approx(0.4)

    def test_a_missing_symbol_leaves_the_weight_alone(self):
        assert reduced({})["AAA"] == pytest.approx(0.4)

    def test_freed_weight_goes_to_cash_not_to_other_names(self):
        # Redistributing would relocate the exposure this overlay exists to
        # remove — the same rule apply_constraints follows for its cap.
        result = reduced({"AAA": 1})
        assert result["BBB"] == pytest.approx(0.3)
        assert result["CCC"] == pytest.approx(0.2)
        assert sum(result.values()) == pytest.approx(0.7)

    def test_scales_every_affected_name(self):
        result = reduced({"AAA": 1, "BBB": 2})
        assert result["AAA"] == pytest.approx(0.2)
        assert result["BBB"] == pytest.approx(0.15)

    def test_scale_of_one_is_a_no_op(self):
        assert reduce_for_events(
            WEIGHTS, days_to_event={"AAA": 1}, window_days=5, scale=1.0
        ) == pytest.approx(WEIGHTS)

    def test_window_of_zero_still_catches_the_event_day(self):
        result = reduce_for_events(
            WEIGHTS, days_to_event={"AAA": 0}, window_days=0, scale=0.5
        )
        assert result["AAA"] == pytest.approx(0.2)

    def test_negative_window_disables_the_overlay(self):
        result = reduce_for_events(
            WEIGHTS, days_to_event={"AAA": 0}, window_days=-1, scale=0.5
        )
        assert result == pytest.approx(WEIGHTS)

    def test_empty_weights_stay_empty(self):
        assert reduce_for_events({}, days_to_event={"AAA": 1}, window_days=5, scale=0.5) == {}

    def test_does_not_mutate_the_input(self):
        original = dict(WEIGHTS)
        reduced({"AAA": 1})
        assert WEIGHTS == original

    @pytest.mark.parametrize("bad_scale", [-0.1, 1.1])
    def test_scale_outside_zero_to_one_is_rejected(self, bad_scale):
        # A scale above 1 would make this an entry signal, which it is not.
        with pytest.raises(ValueError):
            reduce_for_events(WEIGHTS, days_to_event={}, window_days=5, scale=bad_scale)
