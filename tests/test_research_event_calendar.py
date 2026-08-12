from __future__ import annotations

from datetime import date, timedelta

from tradingbot.research.event_calendar import (
    MAX_OVERDUE_DAYS,
    MIN_EVENTS_FOR_ESTIMATE,
    days_to_next_event,
)

# Four quarterly events, roughly 91 days apart.
QUARTERLY = [date(2023, 1, 9), date(2023, 4, 10), date(2023, 7, 10), date(2023, 10, 10)]


class TestDaysToNextEvent:
    def test_estimates_from_the_median_gap(self):
        # Last event 2023-10-10, median gap ~91 days -> around 2024-01-09.
        # Asking on 2023-12-10 leaves roughly a month.
        days = days_to_next_event(QUARTERLY, date(2023, 12, 10))
        assert days is not None
        assert 25 <= days <= 35

    def test_zero_when_the_estimate_has_just_passed(self):
        # An overdue quarterly report means the release is due now, not that
        # there is no event.
        assert days_to_next_event(QUARTERLY, date(2024, 1, 15)) == 0

    def test_none_when_the_estimate_is_long_overdue(self):
        # Past the grace period the estimate is abandoned: collection stopped,
        # rather than an announcement being imminent. Without this guard a
        # dead pipeline would hold every position permanently reduced.
        assert days_to_next_event(QUARTERLY, date(2024, 6, 1)) is None

    def test_the_overdue_grace_period_is_short(self):
        # Estimated 2024-01-09. Two weeks of lateness is plausible; a quarter
        # of it is a stalled feed, and treating that as "due today" would
        # halve the name's target at every rebalance for months.
        assert days_to_next_event(QUARTERLY, date(2024, 1, 9 + MAX_OVERDUE_DAYS)) == 0
        assert days_to_next_event(QUARTERLY, date(2024, 1, 9) + timedelta(days=MAX_OVERDUE_DAYS + 1)) is None

    def test_the_grace_period_does_not_scale_with_the_gap(self):
        # A semiannual reporter must not get six months of "due today" just
        # because its gaps are wide.
        semiannual = [date(2022, 3, 1), date(2022, 9, 1), date(2023, 3, 1), date(2023, 9, 1)]
        # Estimated around 2024-03-02; well past the grace period by June.
        assert days_to_next_event(semiannual, date(2024, 6, 1)) is None

    def test_none_when_there_are_too_few_events(self):
        assert (
            days_to_next_event(QUARTERLY[: MIN_EVENTS_FOR_ESTIMATE - 1], date(2023, 12, 10))
            is None
        )

    def test_none_for_no_events(self):
        assert days_to_next_event([], date(2023, 12, 10)) is None

    def test_ignores_events_after_as_of(self):
        # A future-dated row must never influence the estimate — that is the
        # look-ahead this function exists to prevent.
        with_future = QUARTERLY + [date(2024, 1, 9)]
        assert days_to_next_event(with_future, date(2023, 12, 10)) == days_to_next_event(
            QUARTERLY, date(2023, 12, 10)
        )

    def test_unsorted_input_gives_the_same_answer(self):
        shuffled = [QUARTERLY[2], QUARTERLY[0], QUARTERLY[3], QUARTERLY[1]]
        assert days_to_next_event(shuffled, date(2023, 12, 10)) == days_to_next_event(
            QUARTERLY, date(2023, 12, 10)
        )

    def test_duplicate_dates_do_not_create_a_zero_gap(self):
        # A zero gap would drag the median down and make every symbol look
        # permanently about to report.
        doubled = QUARTERLY + [QUARTERLY[1]]
        assert days_to_next_event(doubled, date(2023, 12, 10)) == days_to_next_event(
            QUARTERLY, date(2023, 12, 10)
        )

    def test_semiannual_reporter_gets_a_wider_estimate(self):
        semiannual = [date(2022, 3, 1), date(2022, 9, 1), date(2023, 3, 1), date(2023, 9, 1)]
        days = days_to_next_event(semiannual, date(2023, 12, 1))
        assert days is not None
        assert 80 <= days <= 100

    def test_as_of_on_the_estimated_day_is_zero(self):
        # 2023-10-10 + 91 days = 2024-01-09.
        assert days_to_next_event(QUARTERLY, date(2024, 1, 9)) == 0
