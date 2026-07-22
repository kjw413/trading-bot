"""Collect DART fundamentals into the point-in-time panel store.

This is the research-facing half of the DART integration. It shares the
`DartClient` in `fundamentals.py` — one HTTP path, one period-end mapping,
one announcement-date source — but produces a different shape for a
different consumer: wide `date x symbol` panel rows that the factor layer
reads, rather than the FCFF record the valuation engine reads.

Availability follows the announcement date, never the accounting period end.
A quarter ending 2023-12-31 is not knowable until it is filed the following
March; dating it by the period end would leak a quarter of hindsight into
every backtest.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from tradingbot.data.credentials import MissingCredentialsError, require_env
from tradingbot.data.fundamentals import (
    REPORT_CODES,
    DartClient,
    Disclosure,
    RawAccount,
    api_key_from_env,
    report_period_end,
    requests_transport,
)
from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

FUNDAMENTALS_DATA_VERSION = "2"
FUNDAMENTALS_SOURCE = "dart"

FUNDAMENTAL_COLUMNS = [
    "revenue",
    "operating_income",
    "net_income",
    "total_assets",
    "total_equity",
]

_ACCOUNT_MAP = {
    "매출액": "revenue",
    "영업이익": "operating_income",
    "당기순이익": "net_income",
    "자산총계": "total_assets",
    "자본총계": "total_equity",
}

PANEL_COLUMNS = ["date", "symbol", "announcement_date"] + FUNDAMENTAL_COLUMNS

# How far back to search the disclosure list for the filing that carried a
# statement. A quarter is normally filed within ~3 months of period end;
# a year of slack covers late and amended filings.
_DISCLOSURE_SEARCH_DAYS = 365


class MissingApiKeyError(MissingCredentialsError):
    """Raised when DART_API_KEY is not set.

    Subclasses MissingCredentialsError so the pipeline reports a keyless run
    as `skipped` rather than `failed` — an absent optional key is a
    configuration state, not a collection failure.
    """


def dart_api_key() -> str:
    try:
        return require_env(
            "DART_API_KEY",
            hint="Get a free key at https://opendart.fss.or.kr and set it as an environment "
            "variable; never commit it to the repository.",
        )
    except MissingCredentialsError as exc:
        raise MissingApiKeyError(str(exc)) from exc


def accounts_to_panel_row(
    accounts: Sequence[RawAccount], disclosure: Disclosure, symbol: str
) -> pd.DataFrame:
    """Map DART accounts into one panel row.

    Accounts that are absent stay NaN rather than 0, so a factor can tell
    "not reported" from "reported as zero".
    """
    if not accounts:
        return pd.DataFrame(columns=PANEL_COLUMNS)

    row: dict[str, object] = {
        "date": pd.Timestamp(accounts[0].report_period),
        "symbol": str(symbol).upper(),
        "announcement_date": pd.Timestamp(disclosure.rcept_dt),
    }
    for column in FUNDAMENTAL_COLUMNS:
        row[column] = float("nan")
    for account in accounts:
        column = _ACCOUNT_MAP.get(account.account_name.strip())
        if column and account.amount is not None:
            row[column] = float(account.amount)
    return pd.DataFrame([row], columns=PANEL_COLUMNS)


def fetch_panel_row(
    client: DartClient, corp_code: str, year: int, report_code: str, symbol: str
) -> pd.DataFrame:
    """Fetch one filing and shape it into a panel row.

    Returns an empty frame when DART has no filing for the period, or when the
    filing cannot be tied to a disclosure — without a receipt date there is no
    defensible availability date, and guessing one would be the look-ahead
    this module exists to prevent.
    """
    accounts = client.financial_statements(corp_code, year, report_code)
    if not accounts:
        return pd.DataFrame(columns=PANEL_COLUMNS)

    period_end = report_period_end(year, report_code)
    disclosures = client.disclosure_list(
        corp_code, period_end, period_end + timedelta(days=_DISCLOSURE_SEARCH_DAYS)
    )
    rcept_no = accounts[0].rcept_no
    disclosure = next((d for d in disclosures if d.rcept_no == rcept_no), None)
    if disclosure is None:
        LOGGER.warning(
            "Filing %s for %s %s %s not found in disclosures; skipping (no announcement date)",
            rcept_no,
            symbol,
            year,
            report_code,
        )
        return pd.DataFrame(columns=PANEL_COLUMNS)
    return accounts_to_panel_row(accounts, disclosure, symbol)


def build_client() -> DartClient:
    """Real network client. Raises MissingApiKeyError when the key is absent."""
    dart_api_key()  # surfaces the missing key as a skippable credential error
    return DartClient(api_key_from_env(), requests_transport())


def update_fundamentals(
    store: PanelStore,
    *,
    symbols: Sequence[str],
    corp_codes: dict[str, str],
    years: Sequence[int],
    fetcher: Callable[..., pd.DataFrame] | None = None,
) -> int:
    """Collect quarterly financials for each symbol into the panel store.

    A symbol without a corp_code, or one whose filing cannot be fetched, is
    logged and skipped so one bad company cannot abort the batch. A missing
    API key is a batch-level configuration problem and propagates.
    """
    if fetcher is None:
        client = build_client()

        def fetcher(corp_code: str, year: int, report_code: str, symbol: str) -> pd.DataFrame:
            return fetch_panel_row(client, corp_code, year, report_code, symbol)

    written = 0
    for symbol in symbols:
        corp_code = corp_codes.get(str(symbol).upper()) or corp_codes.get(str(symbol))
        if not corp_code:
            LOGGER.warning("No DART corp_code for %s; skipping", symbol)
            continue
        for year in years:
            for report_code in REPORT_CODES.values():
                try:
                    frame = fetcher(corp_code, year, report_code, symbol)
                except MissingCredentialsError:
                    raise
                except Exception:
                    LOGGER.exception(
                        "Fundamentals fetch failed for %s %s %s; skipping",
                        symbol,
                        year,
                        report_code,
                    )
                    continue
                if frame.empty:
                    continue
                tagged = attach_metadata(
                    frame,
                    source=FUNDAMENTALS_SOURCE,
                    available_at=next_trading_day_availability(
                        frame["announcement_date"], store.market
                    ),
                    data_version=FUNDAMENTALS_DATA_VERSION,
                )
                written += store.append(tagged)
    return written
