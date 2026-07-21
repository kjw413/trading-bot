from __future__ import annotations

from typing import Any, Callable, Sequence

import pandas as pd

from tradingbot.data.credentials import MissingCredentialsError, require_env
from tradingbot.data.panel import PanelStore, attach_metadata, next_trading_day_availability
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

FUNDAMENTALS_DATA_VERSION = "1"
FUNDAMENTALS_SOURCE = "dart"
DART_ENDPOINT = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"

FUNDAMENTAL_COLUMNS = [
    "revenue",
    "operating_income",
    "net_income",
    "total_assets",
    "total_equity",
]

# DART report code -> (fiscal quarter end month, day)
REPORT_CODES: dict[str, tuple[int, int]] = {
    "11013": (3, 31),   # 1분기보고서
    "11012": (6, 30),   # 반기보고서
    "11014": (9, 30),   # 3분기보고서
    "11011": (12, 31),  # 사업보고서
}

_ACCOUNT_MAP = {
    "매출액": "revenue",
    "영업이익": "operating_income",
    "당기순이익": "net_income",
    "자산총계": "total_assets",
    "자본총계": "total_equity",
}

_EMPTY_COLUMNS = ["date", "symbol", "announcement_date"] + FUNDAMENTAL_COLUMNS

# DART returns 013 when a filing simply does not exist — a normal outcome,
# not an error.
_NO_DATA_STATUS = "013"


class MissingApiKeyError(MissingCredentialsError):
    """Raised when DART_API_KEY is not set."""


def dart_api_key() -> str:
    try:
        return require_env(
            "DART_API_KEY",
            hint="Get a free key at https://opendart.fss.or.kr and set it as an environment "
            "variable; never commit it to the repository.",
        )
    except MissingCredentialsError as exc:
        raise MissingApiKeyError(str(exc)) from exc


def _parse_amount(raw: Any) -> float:
    """DART amounts are comma-grouped strings; negatives use parentheses."""
    text = str(raw).strip()
    if not text or text in {"-", "--"}:
        return float("nan")
    negative = text.startswith("(") and text.endswith(")")
    digits = text.strip("()").replace(",", "").replace(" ", "")
    if digits.startswith("-"):
        negative = True
        digits = digits[1:]
    if not digits.replace(".", "", 1).isdigit():
        return float("nan")
    value = float(digits)
    return -value if negative else value


def parse_financials(payload: dict[str, Any], symbol: str) -> pd.DataFrame:
    """Turn one DART fnlttSinglAcnt response into a single panel row."""
    status = str(payload.get("status", ""))
    if status == _NO_DATA_STATUS:
        return pd.DataFrame(columns=_EMPTY_COLUMNS)
    if status != "000":
        raise RuntimeError(f"DART request failed: status={status} message={payload.get('message')}")

    items = payload.get("list") or []
    if not items:
        return pd.DataFrame(columns=_EMPTY_COLUMNS)

    first = items[0]
    year = int(first["bsns_year"])
    report_code = str(first["reprt_code"])
    if report_code not in REPORT_CODES:
        raise RuntimeError(f"Unknown DART report code: {report_code}")
    month, day = REPORT_CODES[report_code]

    receipt = str(first["rcept_no"])
    announcement = pd.Timestamp(f"{receipt[:4]}-{receipt[4:6]}-{receipt[6:8]}")

    row: dict[str, Any] = {
        "date": pd.Timestamp(year=year, month=month, day=day),
        "symbol": str(symbol).upper(),
        "announcement_date": announcement,
    }
    for column in FUNDAMENTAL_COLUMNS:
        row[column] = float("nan")
    for item in items:
        column = _ACCOUNT_MAP.get(str(item.get("account_nm", "")).strip())
        if column:
            row[column] = _parse_amount(item.get("thstrm_amount"))
    return pd.DataFrame([row], columns=_EMPTY_COLUMNS)


def fetch_financials(corp_code: str, year: int, report_code: str) -> dict[str, Any]:
    """One DART 주요계정 request. Network call; mocked in tests."""
    import requests

    response = requests.get(
        DART_ENDPOINT,
        params={
            "crtfc_key": dart_api_key(),
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": report_code,
            "fs_div": "CFS",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def update_fundamentals(
    store: PanelStore,
    *,
    symbols: Sequence[str],
    corp_codes: dict[str, str],
    years: Sequence[int],
    fetcher: Callable[..., dict[str, Any]] = fetch_financials,
) -> int:
    """Collect quarterly financials.

    `available_at` follows the announcement date, never the period end — the
    numbers for a quarter are not knowable until they are filed."""
    written = 0
    for symbol in symbols:
        corp_code = corp_codes.get(str(symbol).upper()) or corp_codes.get(str(symbol))
        if not corp_code:
            LOGGER.warning("No DART corp_code for %s; skipping", symbol)
            continue
        for year in years:
            for report_code in REPORT_CODES:
                try:
                    payload = fetcher(corp_code, year, report_code)
                    frame = parse_financials(payload, symbol)
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
