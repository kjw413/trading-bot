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
from typing import Callable

DEFAULT_BASE_URL = "https://opendart.fss.or.kr/api"

# DART report codes -> the (month, day) the reporting period ends on.
_REPORT_PERIOD_END: dict[str, tuple[int, int]] = {
    "11013": (3, 31),   # Q1
    "11012": (6, 30),   # half-year
    "11014": (9, 30),   # Q3
    "11011": (12, 31),  # annual
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
            )
            for row in response.get("list", [])
        ]

    def disclosure_list(self, corp_code: str, start: date, end: date) -> list[Disclosure]:
        """Filings for a company between two dates (inclusive)."""
        response = self._get(
            "list.json",
            {
                "corp_code": corp_code,
                "bgn_de": start.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
            },
        )
        return [
            Disclosure(
                rcept_no=row.get("rcept_no", ""),
                report_name=row.get("report_nm", ""),
                rcept_dt=_parse_dart_date(row["rcept_dt"]),
            )
            for row in response.get("list", [])
        ]


def _parse_dart_date(text: str) -> date:
    """Parse a DART YYYYMMDD date string."""
    cleaned = text.strip()
    return date(int(cleaned[0:4]), int(cleaned[4:6]), int(cleaned[6:8]))


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
