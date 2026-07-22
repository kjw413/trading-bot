from __future__ import annotations

from datetime import date

import pytest

from tradingbot.data.fundamentals import (
    REPORT_CODES,
    DartClient,
    fetch_fundamental_record,
)

FIN_RESPONSE = {
    "status": "000",
    "message": "정상",
    "list": [
        {
            "rcept_no": "20240315000123", "bsns_year": "2023", "reprt_code": "11011",
            "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "1,000", "currency": "KRW",
        },
        {
            "rcept_no": "20240315000123", "bsns_year": "2023", "reprt_code": "11011",
            "sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": "200", "currency": "KRW",
        },
    ],
}

LIST_RESPONSE = {
    "status": "000",
    "message": "정상",
    "list": [
        {
            "rcept_no": "20231110000001", "report_nm": "분기보고서", "rcept_dt": "20231110",
        },
        {
            "rcept_no": "20240315000123", "report_nm": "사업보고서 (2023.12)", "rcept_dt": "20240315",
        },
    ],
}


def dispatch_transport(url: str, params: dict) -> dict:
    if "fnlttSinglAcntAll" in url:
        return FIN_RESPONSE
    if "list.json" in url:
        return LIST_RESPONSE
    raise AssertionError(f"unexpected url {url}")


class TestFetchFundamentalRecord:
    def test_ties_statements_to_their_disclosure(self):
        client = DartClient(api_key="KEY", transport=dispatch_transport)
        record = fetch_fundamental_record(
            client, "00126380", 2023, "11011", "KR",
            search_start=date(2024, 1, 1), search_end=date(2024, 6, 30),
        )
        assert record.revenue == 1000.0
        assert record.operating_income == 200.0
        # rcept_no on the accounts is matched to the right disclosure (2024-03-15).
        assert record.announcement_date == date(2024, 3, 15)
        assert record.available_at == date(2024, 3, 18)

    def test_missing_statements_raises(self):
        empty = lambda url, params: {"status": "013", "message": "no data"}  # noqa: E731
        client = DartClient(api_key="KEY", transport=empty)
        with pytest.raises(ValueError):
            fetch_fundamental_record(
                client, "c", 2023, "11011", "KR",
                search_start=date(2024, 1, 1), search_end=date(2024, 6, 30),
            )

    def test_report_code_aliases(self):
        assert REPORT_CODES["annual"] == "11011"
        assert REPORT_CODES["q1"] == "11013"
        assert REPORT_CODES["half"] == "11012"
        assert REPORT_CODES["q3"] == "11014"
