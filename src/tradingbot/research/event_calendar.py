"""Estimate when a company will next report, using only what was knowable.

The next announcement date is not something the bot may look up: filling it
from present-day knowledge is exactly the look-ahead the point-in-time layer
exists to prevent. So it is extrapolated from the spacing of past events —
the median gap between consecutive announcements, about a quarter for most
filers and about half a year for semiannual ones.

Returning None means "unknown", never "no event". Callers must not substitute
a number for it; an unknown schedule is a reason to leave a position alone.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Sequence

# Three gaps is the fewest that gives a median with any resistance to one
# irregular filing. Below this the estimate is noise wearing a number.
MIN_EVENTS_FOR_ESTIMATE = 4

# How long an announcement may be overdue before the estimate is abandoned.
# Companies do report a little later than their own past cadence, so some
# grace is right; a quarter of it is not. Allowing the estimate to stay
# "due today" for a full median gap leaves a symbol flagged for months when
# the announcement simply was not collected, and every rebalance in that
# stretch would halve its target.
MAX_OVERDUE_DAYS = 14


def days_to_next_event(event_dates: Sequence[date], as_of: date) -> int | None:
    """Calendar days until this company is next expected to report.

    Returns 0 when the estimate has already passed but not by much — an
    overdue quarterly report means the release is due, not absent.

    Returns None when the schedule cannot be estimated: too few past events,
    or an estimate more than one median gap in the past. That second case is
    a stalled collection job rather than an imminent announcement, and
    treating it as imminent would hold every position reduced indefinitely.
    """
    # Deduplicated: a repeated date would contribute a zero gap, dragging the
    # median down until every symbol looked permanently about to report.
    past = sorted({day for day in event_dates if day <= as_of})
    if len(past) < MIN_EVENTS_FOR_ESTIMATE:
        return None

    gaps = [(later - earlier).days for earlier, later in zip(past, past[1:])]
    median_gap = statistics.median(gaps)
    if median_gap <= 0:
        return None

    estimate = past[-1] + timedelta(days=round(median_gap))
    if estimate >= as_of:
        return (estimate - as_of).days
    if (as_of - estimate).days <= MAX_OVERDUE_DAYS:
        return 0
    return None
