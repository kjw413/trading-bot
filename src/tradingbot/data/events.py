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

import re
from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from tradingbot.data.credentials import MissingCredentialsError
from tradingbot.data.fundamentals import Disclosure
from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

# 2: amendments and structure-change filings split out of `provisional`.
# 3: `amended` became a column instead of a kind.
# 4: `consolidated` added, to tell a quarterly consolidated release apart from
#    a monthly standalone one that shares the 영업(잠정)실적 title.
# Earlier rows carry the older schema; delete the dataset and re-collect
# rather than reading them together.
EVENTS_DATA_VERSION = "4"
EVENTS_SOURCE = "dart"
EVENTS_DEFAULT_START = date(2015, 1, 1)

EVENT_COLUMNS = [
    "date",
    "symbol",
    "event_kind",
    "amended",
    "consolidated",
    "report_name",
    "rcept_no",
]

# Substring matching on purpose: DART suffixes filings with periods and filer
# names, so exact equality would match nothing.
#
# `provisional` is the quarterly release the market actually trades on, and it
# is the only kind that forms a regular series. The other two are kept because
# they are real earnings information an event study may want, but neither is a
# schedule:
#
#   structure_change 매출액또는손익구조 변경, filed only when revenue or profit
#                    moves past a threshold. Irregular by construction, and
#                    for a large filer it repeats a quarter already announced
#                    (Samsung: 잠정실적 on 2024-01-09, this on 2024-01-31)
#   periodic         the quarterly/annual report, weeks behind the release
_PROVISIONAL_MARKERS = ("영업(잠정)실적",)
_STRUCTURE_CHANGE_MARKERS = ("매출액또는손익구조",)
_PERIODIC_MARKERS = ("분기보고서", "반기보고서", "사업보고서")

# `영업(잠정)실적` alone is not always a quarterly release. Hyundai files it on
# the first business day of every month — those are monthly sales figures —
# while its actual quarterly earnings come out as
# `연결재무제표기준영업(잠정)실적` on the 25th or 26th of the following month.
# The consolidated prefix is what separates the two, so it is recorded rather
# than folded into the kind.
_CONSOLIDATED_MARKER = "연결재무제표기준"

# DART prefixes a re-filed report with bracketed tags: [기재정정] for a
# corrected body, [첨부정정] for corrected attachments, [첨부추가], and
# combinations of them. Being amended is a property of a filing, not a kind of
# filing, so the tags are stripped before classification and recorded
# separately. Treating "amendment" as its own kind — the first attempt —
# swallowed every corrected filing in the repository, including ones with no
# connection to earnings at all: NAVER came back with 92 of them.
_TAG_PATTERN = re.compile(r"^\s*(?:\[[^\]]*\]\s*)+")
_CORRECTION_TAG_MARKER = "정정"

# Lower sorts first, so the most event-like row survives deduplication when
# several filings share a receipt date. An original outranks its own
# amendment: the market reacted to the original.
_KIND_PRIORITY = {"provisional": 0, "structure_change": 1, "periodic": 2}

# Matches `event_calendar.MIN_EVENTS_FOR_ESTIMATE`: below this many releases
# there is nothing to estimate from, so the next series is tried.
_MIN_DATES_FOR_PREFERRED_KIND = 4

# Two announcements closer together than this belong to one reporting period.
# Samsung files the same report title twice a quarter — the preliminary
# release in the first week (2023-01-06, 04-07, 07-07, 10-11) and the detailed
# confirmation about three weeks later (01-31, 04-27, 07-27, 10-31) — and no
# title tells them apart. 45 days sits well clear of both: far above the 20-25
# day repeat, far below the 91-day gap between real quarters.
_MIN_EVENT_GAP_DAYS = 45


def split_amendment_tags(report_name: str) -> tuple[bool, str]:
    """Separate DART's leading `[...]` tags from the report name itself.

    Returns whether any tag marks this as a correction, and the untagged name.
    Every leading tag is stripped, not just `[기재정정]`: a filing tagged
    `[첨부정정]` or `[기재정정][첨부추가]` is the same report re-filed, and
    matching only one spelling lets the others through as fresh events.
    """
    name = (report_name or "").strip()
    match = _TAG_PATTERN.match(name)
    if not match:
        return False, name
    tags = match.group(0)
    return _CORRECTION_TAG_MARKER in tags, name[match.end() :].strip()


def classify_report(report_name: str) -> str | None:
    """Which kind of earnings event this filing is, or None if it is not one.

    Amendment tags are stripped first and do not decide the answer. A
    corrected quarterly report is still a quarterly report; a corrected
    capital-raising disclosure is still not an earnings event, and reading
    the tag as the kind made every corrected filing in the repository look
    like one.
    """
    _, name = split_amendment_tags(report_name)
    if not name:
        return None
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
        amended, untagged = split_amendment_tags(item.report_name)
        kind = classify_report(item.report_name)
        if kind is None:
            continue
        rows.append(
            {
                "date": pd.Timestamp(item.rcept_dt),
                "symbol": str(symbol).upper(),
                "event_kind": kind,
                "amended": amended,
                "consolidated": _CONSOLIDATED_MARKER in untagged,
                "report_name": item.report_name,
                "rcept_no": item.rcept_no,
            }
        )
    if not rows:
        return pd.DataFrame(columns=EVENT_COLUMNS)

    frame = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    frame["amended"] = frame["amended"].astype(bool)
    frame["consolidated"] = frame["consolidated"].astype(bool)
    frame["_priority"] = frame["event_kind"].map(_KIND_PRIORITY)
    # On a shared date: the most event-like kind wins, then an original over
    # its own amendment, then the consolidated basis over the standalone one.
    frame["_amended"] = frame["amended"].astype(int)
    frame["_standalone"] = (~frame["consolidated"]).astype(int)
    return (
        frame.sort_values(["date", "_priority", "_amended", "_standalone"])
        .drop_duplicates(subset=["date", "symbol"], keep="first")
        .drop(columns=["_priority", "_amended", "_standalone"])
        .reset_index(drop=True)
    )


def collapse_near_duplicates(dates: Sequence[date], min_gap_days: int) -> list[date]:
    """Keep one date per reporting period, the earliest of each cluster.

    The earliest is the market event. Samsung's preliminary release moves the
    price; the detailed confirmation three weeks later restates a quarter
    already traded on.
    """
    kept: list[date] = []
    for day in sorted(dates):
        if not kept or (day - kept[-1]).days >= min_gap_days:
            kept.append(day)
    return kept


def schedule_dates(events: pd.DataFrame) -> list[date]:
    """One symbol's announcement dates, one per reporting period.

    Three things have to be filtered out before the gaps between these dates
    mean anything, and each was found by looking at a live collection rather
    than by reasoning about the schema:

    - **Other kinds.** A filer publishing both a provisional release and the
      periodic report that repeats it two months later produces interleaved
      gaps that halve the median. Kinds are tried in order, never mixed.
    - **Standalone releases, when consolidated ones exist.** Hyundai files
      `영업(잠정)실적` on the first business day of every month — monthly
      sales — and `연결재무제표기준영업(잠정)실적` quarterly. Same kind, same
      marker, twelve extra dates a year.
    - **Repeats within a period.** Samsung files the identical title twice a
      quarter, roughly three weeks apart, and no field distinguishes them.

    Returns nothing when no series has enough history. A short series is not a
    schedule, and handing one back invites a caller to read two filings as a
    reporting cadence.
    """
    if events.empty:
        return []
    if "event_kind" not in events.columns:
        # A panel written before event kinds existed. Reading every row is
        # wrong in the ways described above, but it is the only option, and it
        # beats silently returning nothing.
        return sorted({value.date() for value in pd.to_datetime(events["date"])})

    originals = events
    if "amended" in events.columns:
        # A correction re-files a report already made. Counting it puts a
        # second date weeks after the first and the estimator reads that as
        # the cadence.
        originals = events[~events["amended"].astype(bool)]

    def dates_of(rows: pd.DataFrame) -> list[date]:
        return sorted({value.date() for value in pd.to_datetime(rows["date"])})

    provisional = originals[originals["event_kind"] == "provisional"]
    candidates = []
    if "consolidated" in provisional.columns:
        candidates.append(provisional[provisional["consolidated"].astype(bool)])
    candidates.append(provisional)
    candidates.append(originals[originals["event_kind"] == "periodic"])

    for rows in candidates:
        dates = dates_of(rows)
        if len(dates) >= _MIN_DATES_FOR_PREFERRED_KIND:
            return collapse_near_duplicates(dates, _MIN_EVENT_GAP_DAYS)
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
