"""DART (OpenDART) fundamentals ingestion with point-in-time discipline.

The client takes an injected transport so the network is never touched in
tests — all parsing and normalization is exercised against canned responses
(see tests/test_fundamentals_*.py). The API key is read from the environment
and never committed.

Point-in-time rule (framework §8): every record separates report_period (the
accounting period end) from announcement_date (the DART receipt date), and
availability is gated on the announcement, not the period. Using period-dated
data before it was disclosed is the single biggest source of unrealistic
backtest returns.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterable, Sequence

from tradingbot.engine.calendar import get_calendar

DEFAULT_BASE_URL = "https://opendart.fss.or.kr/api"

# DART report codes -> the (month, day) the reporting period ends on.
_REPORT_PERIOD_END: dict[str, tuple[int, int]] = {
    "11013": (3, 31),   # Q1
    "11012": (6, 30),   # half-year
    "11014": (9, 30),   # Q3
    "11011": (12, 31),  # annual
}

# Human-friendly aliases for DART report codes (used by the CLI).
REPORT_CODES: dict[str, str] = {
    "q1": "11013",
    "half": "11012",
    "q3": "11014",
    "annual": "11011",
}

# Transport maps (url, params) -> parsed JSON dict. The real implementation
# uses requests; tests inject a fake that returns a canned dict.
Transport = Callable[[str, dict], dict]


class DartApiError(RuntimeError):
    """A DART response with a non-success status code (other than no-data)."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(f"DART API error {status}: {message}")
        self.status = status
        self.message = message


@dataclass(frozen=True)
class RawAccount:
    """One account line from a DART financial statement."""

    account_name: str
    amount: float | None
    report_period: date
    currency: str
    statement: str          # sj_div: BS / IS / CIS / CF
    account_id: str
    rcept_no: str = ""      # filing that carried this statement


@dataclass(frozen=True)
class Disclosure:
    """One filing from the DART disclosure list."""

    rcept_no: str
    report_name: str
    rcept_dt: date


def report_period_end(year: int, report_code: str) -> date:
    """The accounting period end date for a DART (year, report_code)."""
    try:
        month, day = _REPORT_PERIOD_END[report_code]
    except KeyError as exc:
        raise ValueError(f"unknown DART report code: {report_code}") from exc
    return date(year, month, day)


def _parse_amount(raw: str | None) -> float | None:
    """Parse a DART amount string. '-', '' and None are missing, never 0."""
    if raw is None:
        return None
    text = raw.strip().replace(",", "")
    if text in ("", "-"):
        return None
    return float(text)


class DartClient:
    """Thin OpenDART client over an injected transport."""

    def __init__(self, api_key: str, transport: Transport, base_url: str = DEFAULT_BASE_URL) -> None:
        self.api_key = api_key
        self.transport = transport
        self.base_url = base_url.rstrip("/")

    def _get(self, endpoint: str, params: dict) -> dict:
        payload = {"crtfc_key": self.api_key, **params}
        response = self.transport(f"{self.base_url}/{endpoint}", payload)
        status = response.get("status")
        if status == "013":  # no matching data — a normal empty result
            return {"status": status, "list": []}
        if status != "000":
            raise DartApiError(status or "unknown", response.get("message", ""))
        return response

    def financial_statements(
        self, corp_code: str, year: int, report_code: str, fs_div: str = "CFS"
    ) -> list[RawAccount]:
        """Full financial statement accounts for a company and report period."""
        response = self._get(
            "fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": report_code,
                "fs_div": fs_div,
            },
        )
        period = report_period_end(year, report_code)
        return [
            RawAccount(
                account_name=row.get("account_nm", ""),
                amount=_parse_amount(row.get("thstrm_amount")),
                report_period=period,
                currency=row.get("currency", "KRW"),
                statement=row.get("sj_div", ""),
                account_id=row.get("account_id", ""),
                rcept_no=row.get("rcept_no", ""),
            )
            for row in response.get("list", [])
        ]

    def disclosure_list(self, corp_code: str, start: date, end: date) -> list[Disclosure]:
        """Filings for a company between two dates (inclusive).

        DART's list.json paginates at 100 rows per page. A large-cap easily
        files more than that in a year-long window, so a single unpaginated
        request can silently miss the specific filing a caller is looking
        for. This walks every page the response's total_count implies.
        """
        disclosures: list[Disclosure] = []
        page_no = 1
        while True:
            response = self._get(
                "list.json",
                {
                    "corp_code": corp_code,
                    "bgn_de": start.strftime("%Y%m%d"),
                    "end_de": end.strftime("%Y%m%d"),
                    "page_no": str(page_no),
                    "page_count": "100",
                },
            )
            rows = response.get("list", [])
            disclosures.extend(
                Disclosure(
                    rcept_no=row.get("rcept_no", ""),
                    report_name=row.get("report_nm", ""),
                    rcept_dt=_parse_dart_date(row["rcept_dt"]),
                )
                for row in rows
            )
            total_count = int(response.get("total_count", len(disclosures)))
            if not rows or len(disclosures) >= total_count:
                break
            page_no += 1
        return disclosures


def _parse_dart_date(text: str) -> date:
    """Parse a DART YYYYMMDD date string."""
    cleaned = text.strip()
    return date(int(cleaned[0:4]), int(cleaned[4:6]), int(cleaned[6:8]))


# --- Point-in-time normalization and FCFF-input mapping --------------------

# Interest-bearing debt lines that sum into gross borrowings.
_BORROWING_NAMES = ("단기차입금", "장기차입금", "사채", "유동성장기부채")


@dataclass(frozen=True)
class FundamentalRecord:
    """One point-in-time snapshot of a company's fundamentals.

    report_period is the accounting period end; announcement_date is when the
    filing was received (DART rcept_dt); available_at is the first date the bot
    may use it (next trading day). Component amounts are None when the source
    account is absent — never silently 0, so downstream valuation can tell
    "genuinely zero" from "unknown".
    """

    corp_code: str
    report_period: date
    announcement_date: date
    available_at: date
    currency: str
    revenue: float | None
    operating_income: float | None
    depreciation_amortization: float | None
    capex: float | None
    cash_and_equivalents: float | None
    total_borrowings: float | None
    net_debt: float | None


def available_at(rcept_dt: date, market: str) -> date:
    """First date disclosed data may be used: the next trading day (framework §8)."""
    return get_calendar(market).next_trading_day(rcept_dt)


def _first_amount(accounts: Iterable[RawAccount], name: str) -> float | None:
    for account in accounts:
        if account.account_name == name and account.amount is not None:
            return account.amount
    return None


def _sum_amounts(accounts: Sequence[RawAccount], names: Sequence[str]) -> float | None:
    present = [a.amount for a in accounts if a.account_name in names and a.amount is not None]
    return float(sum(present)) if present else None


def to_fundamental_record(
    corp_code: str,
    accounts: Sequence[RawAccount],
    disclosure: Disclosure,
    market: str,
) -> FundamentalRecord:
    """Map raw DART accounts into a point-in-time fundamentals record.

    Only accounts with clear, stable names are mapped; the rest stay None until
    richer parsing is added. net_debt is derived only when both borrowings and
    cash are present.
    """
    period = accounts[0].report_period if accounts else disclosure.rcept_dt
    currency = accounts[0].currency if accounts else "KRW"

    cash = _first_amount(accounts, "현금및현금성자산")
    borrowings = _sum_amounts(accounts, _BORROWING_NAMES)
    net_debt = borrowings - cash if (borrowings is not None and cash is not None) else None

    return FundamentalRecord(
        corp_code=corp_code,
        report_period=period,
        announcement_date=disclosure.rcept_dt,
        available_at=available_at(disclosure.rcept_dt, market),
        currency=currency,
        revenue=_first_amount(accounts, "매출액"),
        operating_income=_first_amount(accounts, "영업이익"),
        depreciation_amortization=next(
            (a.amount for a in accounts if "감가상각" in a.account_name and a.amount is not None),
            None,
        ),
        capex=_first_amount(accounts, "유형자산의 취득"),
        cash_and_equivalents=cash,
        total_borrowings=borrowings,
        net_debt=net_debt,
    )


def fetch_fundamental_record(
    client: DartClient,
    corp_code: str,
    year: int,
    report_code: str,
    market: str,
    *,
    search_start: date,
    search_end: date,
) -> FundamentalRecord:
    """Fetch statements and tie them to their disclosure into one PIT record.

    The financial-statement rows carry the filing's rcept_no; matching it to the
    disclosure list gives the receipt date that drives available_at. Raises when
    no statements are returned for the period.
    """
    accounts = client.financial_statements(corp_code, year, report_code)
    if not accounts:
        raise ValueError(
            f"no financial statements for corp={corp_code} year={year} report={report_code}"
        )
    rcept_no = accounts[0].rcept_no
    disclosures = client.disclosure_list(corp_code, search_start, search_end)
    disclosure = next((d for d in disclosures if d.rcept_no == rcept_no), None)
    if disclosure is None:
        raise ValueError(
            f"filing {rcept_no} not found in disclosures {search_start}..{search_end}; "
            "widen the search window"
        )
    return to_fundamental_record(corp_code, accounts, disclosure, market)


class FundamentalStore:
    """In-memory point-in-time store of fundamentals records.

    as_of() never returns a record disclosed after the query date — the same
    look-ahead guard the price data store enforces, applied to fundamentals.
    """

    def __init__(self) -> None:
        self._by_corp: dict[str, list[FundamentalRecord]] = {}

    def add(self, record: FundamentalRecord) -> None:
        self._by_corp.setdefault(record.corp_code, []).append(record)

    def records(self, corp_code: str) -> list[FundamentalRecord]:
        return list(self._by_corp.get(corp_code, []))

    def as_of(self, corp_code: str, as_of_date: date) -> FundamentalRecord | None:
        """Latest record available on or before as_of_date, or None."""
        available = [
            record
            for record in self._by_corp.get(corp_code, [])
            if record.available_at <= as_of_date
        ]
        if not available:
            return None
        return max(available, key=lambda r: (r.available_at, r.report_period))


def requests_transport(timeout: float = 10.0) -> Transport:
    """Real network transport. Imported lazily so tests never need requests."""

    def transport(url: str, params: dict) -> dict:
        import requests

        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()

    return transport


def api_key_from_env(env: dict[str, str] | None = None) -> str:
    """Read the DART API key from DART_API_KEY. Never hard-code or commit it."""
    source = env if env is not None else os.environ
    key = source.get("DART_API_KEY")
    if not key:
        raise RuntimeError(
            "DART_API_KEY is not set. Register at https://opendart.fss.or.kr and "
            "export DART_API_KEY=... (do not commit the key)."
        )
    return key
