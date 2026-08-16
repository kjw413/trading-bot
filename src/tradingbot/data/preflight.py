"""Check that a host can actually reach what collection needs.

Written for the move to a server, where the failures are different from the
ones a laptop sees. Yahoo answers datacentre addresses with 403 far more
readily than residential ones, SEC rejects a request with no contact in the
User-Agent, and a container without tzdata computes the wrong session dates
while looking perfectly healthy.

Every check is cheap and reports on its own. The point is to find out in
thirty seconds, before a multi-hour backfill, whether this host can do the
job — and if not, which part is missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

OK = "ok"
FAILED = "failed"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str

    @property
    def passed(self) -> bool:
        return self.status == OK


def _run(name: str, fn: Callable[[], str]) -> CheckResult:
    try:
        return CheckResult(name, OK, fn())
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        LOGGER.debug("Preflight check %s failed", name, exc_info=True)
        return CheckResult(name, FAILED, f"{type(exc).__name__}: {exc}")


def check_timezones() -> str:
    """Exchange calendars need tzdata, which slim base images often omit."""
    from zoneinfo import ZoneInfo

    from tradingbot.engine.calendar import get_calendar

    ZoneInfo("America/New_York")
    calendar = get_calendar("US")
    # A known holiday: if this comes back as a trading day the calendar is
    # falling back to a plain weekday rule and every session date is suspect.
    if calendar.is_trading_day(date(2024, 7, 4)):
        raise RuntimeError("XNYS calendar says 2024-07-04 is a trading day; tzdata/exchange data missing")
    return "XNYS calendar loaded, holidays honoured"


def check_sec_contact() -> str:
    """SEC rejects unidentified requests; better to find out here."""
    from tradingbot.data.edgar import user_agent_from_env

    agent = user_agent_from_env()
    return f"SEC_USER_AGENT set ({len(agent)} chars)"


def check_edgar() -> str:
    """One submissions document — the events collector's only endpoint."""
    from tradingbot.data.edgar import EdgarClient, requests_transport

    filings = EdgarClient(requests_transport()).submissions(320193)  # AAPL
    if not filings:
        raise RuntimeError("EDGAR returned no filings for CIK 320193")
    return f"EDGAR reachable, {len(filings)} filings for AAPL"


def check_sec_tickers() -> str:
    from tradingbot.data.cik import download_company_tickers, parse_company_tickers

    mapping = parse_company_tickers(download_company_tickers())
    if "AAPL" not in mapping:
        raise RuntimeError("company_tickers.json parsed but has no AAPL")
    return f"{len(mapping)} ticker-to-CIK entries"


def check_listings() -> str:
    from tradingbot.data.listings import fetch_us_common_stocks

    symbols = fetch_us_common_stocks()
    if len(symbols) < 1000:
        raise RuntimeError(f"only {len(symbols)} common stocks; the directory looks truncated")
    return f"{len(symbols)} US common stocks"


def check_prices() -> str:
    """A real price fetch. This is the check most likely to fail on a server."""
    import FinanceDataReader as fdr

    end = date.today()
    frame = fdr.DataReader("AAPL", end - timedelta(days=30), end)
    if frame is None or len(frame) == 0:
        raise RuntimeError("price source returned no rows for AAPL")
    return f"{len(frame)} daily bars for AAPL"


def check_writable(root) -> Callable[[], str]:
    def check() -> str:
        from pathlib import Path

        path = Path(root) / ".preflight"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")
        path.unlink()
        return f"{path.parent} is writable"

    return check


CHECKS: dict[str, Callable[[], str]] = {
    "timezones": check_timezones,
    "sec_contact": check_sec_contact,
    "edgar": check_edgar,
    "sec_tickers": check_sec_tickers,
    "listings": check_listings,
    "prices": check_prices,
}


def run_preflight(data_root: str = "data", checks: dict[str, Callable[[], str]] | None = None) -> list[CheckResult]:
    """Run every check, and never stop at the first failure.

    One broken source should not hide the state of the others — the whole
    reason to run this is to learn everything that needs fixing in one pass.
    """
    active = dict(checks or CHECKS)
    active.setdefault("writable", check_writable(data_root))
    return [_run(name, fn) for name, fn in active.items()]


def render(results: list[CheckResult]) -> str:
    lines = ["사전 점검 결과", ""]
    for result in results:
        mark = "성공" if result.passed else "실패"
        lines.append(f"  - {result.name}: {mark} — {result.detail}")
    failed = [r.name for r in results if not r.passed]
    lines.append("")
    if failed:
        lines.append(f"전체 결과: 실패 — {', '.join(failed)}")
        lines.append("이 호스트로는 수집을 시작하지 마세요. 위 항목을 먼저 해결해야 합니다.")
    else:
        lines.append("전체 결과: 정상 — 이 호스트에서 수집을 진행할 수 있습니다.")
    return "\n".join(lines)
