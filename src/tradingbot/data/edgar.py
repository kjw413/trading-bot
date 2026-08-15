"""Earnings event calendar from SEC EDGAR.

The US counterpart of `data/events.py`, which reads Korean DART filings. Both
write the same panel, so everything downstream — `schedule_dates`, the
overlay, the strategy — is shared and market-agnostic.

The event is the earnings press release, filed as an 8-K carrying Item 2.02
(Results of Operations and Financial Condition). The 10-Q or 10-K repeats the
same numbers days to weeks later. Dating events by the periodic report would
measure pre-event state after the move had already happened and measure the
reaction on a quiet day, which is the mistake the Korean collector was built
to avoid and this one inherits the fix for.

EDGAR is easier to classify than DART: the item number is a structured field
rather than a phrase inside a report title, so there is no string matching to
get wrong. It is harder in one respect — filings carry an acceptance time, and
an 8-K accepted after the closing bell is not priced until the next session.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from tradingbot.data.events import EVENT_COLUMNS, _KIND_PRIORITY
from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.engine.calendar import get_calendar
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

EDGAR_DATA_VERSION = "1"
EDGAR_SOURCE = "edgar"
EDGAR_DEFAULT_START = date(2015, 1, 1)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# 8-K Item 2.02, "Results of Operations and Financial Condition". This is the
# earnings release. Item 7.01 (Reg FD) often rides along and 9.01 carries the
# exhibits; neither is the event on its own.
EARNINGS_ITEM = "2.02"

_PERIODIC_FORMS = ("10-Q", "10-K")
_EARNINGS_FORM = "8-K"
_AMENDMENT_SUFFIX = "/A"

# `acceptanceDateTime` is stamped with a trailing Z but SEC publishes it in
# Eastern Time. Reading it as UTC would move a 16:31 filing to 11:31 ET and
# turn an after-close release into an intraday one — the single most
# consequential detail in this module.
EDGAR_TZ = ZoneInfo("America/New_York")

# A filing accepted at or after the bell cannot be priced until the next
# session. Regular hours end at 16:00 ET; after-hours trading is not the
# reference the strategy fills against.
_MARKET_CLOSE_ET = time(16, 0)

# EDGAR panel rows carry one column the DART ones do not: the first session
# that can price the filing. `date` stays the filing date so the schedule
# estimator measures the reporting cadence, not the reaction.
EDGAR_EVENT_COLUMNS = EVENT_COLUMNS + ["reaction_date"]

Transport = Callable[[str], dict]


@dataclass(frozen=True)
class Filing:
    """One row of an EDGAR submissions index."""

    form: str
    items: str
    filing_date: date
    acceptance: datetime | None
    accession: str


def user_agent_from_env(env: dict[str, str] | None = None) -> str:
    """SEC requires a contact address in the User-Agent and 403s without one."""
    source = env if env is not None else os.environ
    value = source.get("SEC_USER_AGENT")
    if not value:
        raise RuntimeError(
            "SEC_USER_AGENT is not set. SEC rejects unidentified requests with an "
            'opaque 403; set it to a name and address, e.g. "Jane Doe jane@example.com" '
            "(https://www.sec.gov/os/webmaster-faq#developers)."
        )
    return value


def is_amended(form: str) -> bool:
    """Whether this form is a re-filing of one already made."""
    return (form or "").strip().upper().endswith(_AMENDMENT_SUFFIX)


def _base_form(form: str) -> str:
    cleaned = (form or "").strip().upper()
    if cleaned.endswith(_AMENDMENT_SUFFIX):
        cleaned = cleaned[: -len(_AMENDMENT_SUFFIX)]
    return cleaned.strip()


def classify_filing(form: str, items: str) -> str | None:
    """Which kind of earnings event this filing is, or None if it is not one.

    Amendment suffixes are stripped before the decision. An amended earnings
    8-K is still an earnings 8-K; an amended proxy statement is still not an
    earnings event.

    Items are compared element-wise rather than by substring, so "12.02" and
    "2.021" cannot pass as Item 2.02.
    """
    base = _base_form(form)
    if base == _EARNINGS_FORM:
        reported = {part.strip() for part in (items or "").split(",")}
        return "provisional" if EARNINGS_ITEM in reported else None
    if base in _PERIODIC_FORMS:
        return "periodic"
    return None


def reaction_date(filing: Filing, market: str = "US") -> date:
    """First session that can price this filing.

    An 8-K accepted at or after the closing bell is tomorrow's news. Most large
    caps report either before the open or after the close, so getting this
    wrong misdates a large share of events by one session — enough to put the
    reaction window on the wrong day entirely.

    Filings with no acceptance time (older submissions carry none) fall back to
    the filing date unshifted. Assuming "after close" for those would move
    every one of them by a day on no evidence.
    """
    if filing.acceptance is None:
        return filing.filing_date
    local = filing.acceptance.astimezone(EDGAR_TZ)
    if local.time() < _MARKET_CLOSE_ET:
        return local.date()
    return get_calendar(market).next_trading_day(local.date())


def _parse_acceptance(raw: str | None) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    # Drop the misleading Z and read the wall clock as Eastern Time.
    if text.endswith("Z"):
        text = text[:-1]
    try:
        naive = datetime.fromisoformat(text)
    except ValueError:
        LOGGER.warning("Unparseable EDGAR acceptance timestamp %r; treating as absent", raw)
        return None
    return naive.replace(tzinfo=EDGAR_TZ)


def parse_filings(submissions: dict[str, Any]) -> list[Filing]:
    """Read the filing index out of a submissions document.

    EDGAR stores the index column-wise: parallel arrays under
    `filings.recent`, keyed by field name. Only 8-K submissions populate
    `items`, so that column may be missing entirely.
    """
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    if not forms:
        return []

    items = recent.get("items") or [""] * len(forms)
    filing_dates = recent.get("filingDate") or []
    acceptances = recent.get("acceptanceDateTime") or [None] * len(forms)
    accessions = recent.get("accessionNumber") or [""] * len(forms)

    filings: list[Filing] = []
    for index, form in enumerate(forms):
        try:
            filing_date = date.fromisoformat(filing_dates[index])
        except (IndexError, ValueError):
            LOGGER.warning("EDGAR filing %s has no usable filingDate; skipping", index)
            continue
        filings.append(
            Filing(
                form=form,
                items=items[index] if index < len(items) else "",
                filing_date=filing_date,
                acceptance=_parse_acceptance(
                    acceptances[index] if index < len(acceptances) else None
                ),
                accession=accessions[index] if index < len(accessions) else "",
            )
        )
    return filings


def filings_to_events(
    filings: Sequence[Filing], symbol: str, market: str = "US"
) -> pd.DataFrame:
    """Filter filings down to earnings events, one row per (date, symbol).

    `PanelStore` keys on (date, symbol), so filings sharing a date collapse to
    one row. The choice is made here rather than by write order: the earnings
    release outranks the periodic report, and an original outranks its own
    amendment.
    """
    rows = []
    for item in filings:
        kind = classify_filing(item.form, item.items)
        if kind is None:
            continue
        rows.append(
            {
                "date": pd.Timestamp(item.filing_date),
                "symbol": str(symbol).upper(),
                "event_kind": kind,
                "amended": is_amended(item.form),
                # US filings carry no separate/consolidated split. The column
                # exists for the Korean collector, where it separates a
                # quarterly consolidated release from monthly standalone sales;
                # here it is uniformly true so the shared preference step in
                # `schedule_dates` passes straight through.
                "consolidated": True,
                "report_name": item.form,
                "rcept_no": item.accession,
                "reaction_date": pd.Timestamp(reaction_date(item, market)),
            }
        )
    if not rows:
        return pd.DataFrame(columns=EDGAR_EVENT_COLUMNS)

    frame = pd.DataFrame(rows, columns=EDGAR_EVENT_COLUMNS)
    frame["amended"] = frame["amended"].astype(bool)
    frame["consolidated"] = frame["consolidated"].astype(bool)
    frame["_priority"] = frame["event_kind"].map(_KIND_PRIORITY)
    frame["_amended"] = frame["amended"].astype(int)
    return (
        frame.sort_values(["date", "_priority", "_amended"])
        .drop_duplicates(subset=["date", "symbol"], keep="first")
        .drop(columns=["_priority", "_amended"])
        .reset_index(drop=True)
    )


class EdgarClient:
    """Thin EDGAR submissions client over an injected transport.

    The transport maps a URL to parsed JSON so tests never touch the network,
    the same shape `DartClient` uses.
    """

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def submissions(self, cik: int) -> list[Filing]:
        """Every filing on record for this company, newest page first.

        `filings.recent` holds roughly the last thousand filings; anything
        older lives in separate pages listed under `filings.files`. A universe
        with a decade of history needs those pages, and reading only `recent`
        would quietly truncate older companies to their last few years.
        """
        document = self.transport(SUBMISSIONS_URL.format(cik=int(cik)))
        filings = parse_filings(document)
        for page in (document.get("filings") or {}).get("files") or []:
            name = page.get("name")
            if not name:
                continue
            try:
                older = self.transport(SUBMISSIONS_PAGE_URL.format(name=name))
            except Exception:
                LOGGER.exception("EDGAR submissions page %s failed; skipping that page", name)
                continue
            filings.extend(parse_filings({"filings": {"recent": older}}))
        return filings


def requests_transport(user_agent: str | None = None, timeout: float = 15.0) -> Transport:
    """Real network transport. Imported lazily so tests never need requests."""
    agent = user_agent or user_agent_from_env()

    def transport(url: str) -> dict:
        import requests

        response = requests.get(
            url, headers={"User-Agent": agent, "Accept-Encoding": "gzip, deflate"}, timeout=timeout
        )
        response.raise_for_status()
        return response.json()

    return transport


def fetch_edgar_events(cik: int, start: date, end: date, symbol: str) -> pd.DataFrame:
    """Real network fetch: one company's earnings filings in a date range."""
    client = EdgarClient(requests_transport())
    filings = [f for f in client.submissions(cik) if start <= f.filing_date <= end]
    return filings_to_events(filings, symbol)


def update_edgar_events(
    store: PanelStore,
    *,
    symbols: Sequence[str],
    ciks: dict[str, int],
    start: date | None = None,
    end: date | None = None,
    fetcher: Callable[..., pd.DataFrame] = fetch_edgar_events,
) -> int:
    """Incrementally collect earnings events for each symbol.

    A symbol without a CIK, or one whose fetch fails, is logged and skipped so
    a single bad ticker cannot abort the batch.
    """
    written = 0
    fetch_end = end or date.today()
    for symbol in symbols:
        cik = ciks.get(str(symbol).upper()) or ciks.get(str(symbol))
        if not cik:
            LOGGER.warning("No SEC CIK for %s; skipping", symbol)
            continue

        last = store.last_date(symbol)
        fetch_start = last + pd.Timedelta(days=1).to_pytimedelta() if last else (
            start or EDGAR_DEFAULT_START
        )
        if fetch_start > fetch_end:
            continue

        try:
            frame = fetcher(cik, fetch_start, fetch_end, symbol)
        except Exception:
            LOGGER.exception("EDGAR collection failed for %s; skipping this symbol", symbol)
            continue
        if frame.empty:
            continue

        tagged = attach_metadata(
            frame,
            source=EDGAR_SOURCE,
            available_at=next_trading_day_availability(frame["date"], store.market),
            data_version=EDGAR_DATA_VERSION,
        )
        written += store.append(tagged)
    return written
