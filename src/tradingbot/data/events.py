"""Earnings event calendar from DART disclosures.

The event is the day the market learned the numbers, which is *not* the day
the periodic report was filed. Korean issuers publish provisional results
through a fair-disclosure filing weeks before the quarterly or annual report
carries the same figures: a Q4 provisional release lands in early January,
the annual report in March. The price moves in January.

Dating events by the periodic report would measure pre-event state after the
move already happened, and measure the reaction on a quiet day — silently,
producing plausible numbers the whole way. That is why the classification is
a pure function tested against the real DART report names.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from tradingbot.data.credentials import MissingCredentialsError
from tradingbot.data.fundamentals import Disclosure
from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

# 2: amendments and structure-change filings split out of `provisional`.
# Rows written under version 1 carry the old, coarser kinds; delete the
# dataset and re-collect rather than reading the two together.
EVENTS_DATA_VERSION = "2"
EVENTS_SOURCE = "dart"
EVENTS_DEFAULT_START = date(2015, 1, 1)

EVENT_COLUMNS = ["date", "symbol", "event_kind", "report_name", "rcept_no"]

# Substring matching on purpose: DART suffixes filings with periods and filer
# names, so exact equality would match nothing.
#
# `provisional` is the quarterly release the market actually trades on, and it
# is the only kind that forms a regular series. The other three are kept
# because they are real earnings information an event study may want, but none
# of them is a schedule:
#
#   amendment        a correction of a filing already made — the market
#                    reacted to the original, so this is not a new event
#   structure_change 매출액또는손익구조 변경, filed only when revenue or profit
#                    moves past a threshold. Irregular by construction, and
#                    for a large filer it repeats a quarter already announced
#                    (Samsung: 잠정실적 on 2024-01-09, this on 2024-01-31)
#   periodic         the quarterly/annual report, weeks behind the release
_AMENDMENT_MARKER = "기재정정"
_PROVISIONAL_MARKERS = ("영업(잠정)실적",)
_STRUCTURE_CHANGE_MARKERS = ("매출액또는손익구조",)
_PERIODIC_MARKERS = ("분기보고서", "반기보고서", "사업보고서")

# Lower sorts first, so the most event-like row survives deduplication when
# several filings share a receipt date.
_KIND_PRIORITY = {"provisional": 0, "structure_change": 1, "periodic": 2, "amendment": 3}

# Kinds that form a regular series, best first. Anything absent from this
# tuple is stored but never used to estimate a schedule.
_SCHEDULE_KINDS = ("provisional", "periodic")

# Matches `event_calendar.MIN_EVENTS_FOR_ESTIMATE`: below this many releases
# there is nothing to estimate from, so the next kind is tried.
_MIN_DATES_FOR_PREFERRED_KIND = 4


def classify_report(report_name: str) -> str | None:
    """Which kind of earnings event this filing is, or None if it is not one.

    Amendments are checked first: `[기재정정]연결재무제표기준영업(잠정)실적`
    is a correction of a release already made, and calling it a provisional
    release would put a second announcement three weeks after the first,
    which is what the schedule estimator reads as the reporting cadence.
    """
    name = (report_name or "").strip()
    if not name:
        return None
    if _AMENDMENT_MARKER in name:
        return "amendment"
    if any(marker in name for marker in _PROVISIONAL_MARKERS):
        return "provisional"
    if any(marker in name for marker in _STRUCTURE_CHANGE_MARKERS):
        return "structure_change"
    if any(marker in name for marker in _PERIODIC_MARKERS):
        return "periodic"
    return None


def disclosures_to_events(disclosures: Sequence[Disclosure], symbol: str) -> pd.DataFrame:
    """Filter disclosures down to earnings events, one row per (date, symbol).

    `PanelStore` keys on (date, symbol), so two filings received the same day
    would collapse to whichever happened to be written last. That choice is
    made here instead of by accident: the provisional release wins.
    """
    rows = []
    for item in disclosures:
        kind = classify_report(item.report_name)
        if kind is None:
            continue
        rows.append(
            {
                "date": pd.Timestamp(item.rcept_dt),
                "symbol": str(symbol).upper(),
                "event_kind": kind,
                "report_name": item.report_name,
                "rcept_no": item.rcept_no,
            }
        )
    if not rows:
        return pd.DataFrame(columns=EVENT_COLUMNS)

    frame = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    frame["_priority"] = frame["event_kind"].map(_KIND_PRIORITY)
    return (
        frame.sort_values(["date", "_priority"])
        .drop_duplicates(subset=["date", "symbol"], keep="first")
        .drop(columns="_priority")
        .reset_index(drop=True)
    )


def schedule_dates(events: pd.DataFrame) -> list[date]:
    """One symbol's announcement dates, of a single kind, for the estimator.

    Both kinds are stored, because an issuer that never publishes provisional
    results still has to be scheduled off something. But they must never be
    read together: a company filing both puts a provisional release and a
    periodic report a couple of months apart in the same series, and the
    interleaved gaps halve the median. The estimator would then expect a
    report every six weeks and flag the name almost continuously.

    Provisional releases win whenever there are enough of them to estimate
    from; otherwise the periodic reports are used alone. Amendments and
    structure-change filings are never a schedule — see `_KIND_PRIORITY`.

    Returns nothing when no single kind has enough history. A short series is
    not a schedule, and handing one back would only invite a caller to treat
    two filings as a reporting cadence.
    """
    if events.empty:
        return []
    if "event_kind" not in events.columns:
        # A panel written before event kinds existed. Reading every row is
        # wrong in the same way described above, but it is the only option,
        # and it is better than silently returning nothing.
        return sorted({value.date() for value in pd.to_datetime(events["date"])})

    def dates_of(kind: str) -> list[date]:
        rows = events[events["event_kind"] == kind]
        return sorted({value.date() for value in pd.to_datetime(rows["date"])})

    for kind in _SCHEDULE_KINDS:
        dates = dates_of(kind)
        if len(dates) >= _MIN_DATES_FOR_PREFERRED_KIND:
            return dates
    return []


def fetch_events(corp_code: str, start: date, end: date, symbol: str) -> pd.DataFrame:
    """Real network fetch: one company's earnings filings in a date range."""
    from tradingbot.data.fundamentals_panel import build_client

    client = build_client()
    return disclosures_to_events(client.disclosure_list(corp_code, start, end), symbol)


def update_events(
    store: PanelStore,
    *,
    symbols: Sequence[str],
    corp_codes: dict[str, str],
    start: date | None = None,
    end: date | None = None,
    fetcher: Callable[..., pd.DataFrame] = fetch_events,
) -> int:
    """Incrementally collect earnings events for each symbol.

    A symbol without a corp_code, or one whose fetch fails, is logged and
    skipped so a single bad company cannot abort the batch. A missing API key
    is a batch-level configuration problem and propagates.
    """
    written = 0
    fetch_end = end or date.today()
    for symbol in symbols:
        corp_code = corp_codes.get(str(symbol).upper()) or corp_codes.get(str(symbol))
        if not corp_code:
            LOGGER.warning("No DART corp_code for %s; skipping", symbol)
            continue

        last = store.last_date(symbol)
        fetch_start = last + timedelta(days=1) if last else (start or EVENTS_DEFAULT_START)
        if fetch_start > fetch_end:
            continue

        try:
            frame = fetcher(corp_code, fetch_start, fetch_end, symbol)
        except MissingCredentialsError:
            raise
        except Exception:
            LOGGER.exception("Event collection failed for %s; skipping this symbol", symbol)
            continue
        if frame.empty:
            continue

        tagged = attach_metadata(
            frame,
            source=EVENTS_SOURCE,
            available_at=next_trading_day_availability(frame["date"], store.market),
            data_version=EVENTS_DATA_VERSION,
        )
        written += store.append(tagged)
    return written
