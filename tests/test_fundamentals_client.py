from __future__ import annotations

from datetime import date

import pytest

from tradingbot.data.fundamentals import (
    DartApiError,
    DartClient,
    Disclosure,
    RawAccount,
)


def fake_transport(response: dict):
    """Return a transport that records the params it was called with."""
    calls: list[tuple[str, dict]] = []

    def transport(url: str, params: dict) -> dict:
        calls.append((url, dict(params)))
        return response

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


FIN_RESPONSE = {
    "status": "000",
    "message": "정상",
    "list": [
        {
            "rcept_no": "20240315000123",
            "bsns_year": "2023",
            "reprt_code": "11011",
            "sj_div": "IS",
            "sj_nm": "손익계산서",
            "account_id": "ifrs-full_Revenue",
            "account_nm": "매출액",
            "thstrm_amount": "1,234,567",
            "currency": "KRW",
        },
        {
            "rcept_no": "20240315000123",
            "bsns_year": "2023",
            "reprt_code": "11011",
            "sj_div": "IS",
            "sj_nm": "손익계산서",
            "account_id": "dart_OperatingIncomeLoss",
            "account_nm": "영업이익",
            "thstrm_amount": "-",  # missing -> None, never coerced to 0
            "currency": "KRW",
        },
    ],
}

LIST_RESPONSE = {
    "status": "000",
    "message": "정상",
    "list": [
        {
            "corp_code": "00126380",
            "corp_name": "삼성전자",
            "report_nm": "사업보고서 (2023.12)",
            "rcept_no": "20240315000123",
            "rcept_dt": "20240315",
        }
    ],
}


class TestFinancialStatements:
    def test_parses_accounts(self):
        transport = fake_transport(FIN_RESPONSE)
        client = DartClient(api_key="KEY", transport=transport)
        accounts = client.financial_statements("00126380", 2023, "11011")

        assert all(isinstance(a, RawAccount) for a in accounts)
        revenue = next(a for a in accounts if a.account_name == "매출액")
        assert revenue.amount == pytest.approx(1234567.0)
        assert revenue.currency == "KRW"
        assert revenue.statement == "IS"
        # Annual report (11011) -> reporting period ends 2023-12-31.
        assert revenue.report_period == date(2023, 12, 31)

    def test_dash_amount_is_none_not_zero(self):
        client = DartClient(api_key="KEY", transport=fake_transport(FIN_RESPONSE))
        op = next(a for a in client.financial_statements("00126380", 2023, "11011")
                  if a.account_name == "영업이익")
        assert op.amount is None

    def test_api_key_injected(self):
        transport = fake_transport(FIN_RESPONSE)
        DartClient(api_key="SECRET", transport=transport).financial_statements("x", 2023, "11011")
        _, params = transport.calls[0]
        assert params["crtfc_key"] == "SECRET"
        assert params["corp_code"] == "x"
        assert params["bsns_year"] == "2023"

    def test_quarterly_report_period(self):
        response = {
            "status": "000",
            "message": "정상",
            "list": [
                {
                    "rcept_no": "r", "bsns_year": "2023", "reprt_code": "11013",
                    "sj_div": "IS", "account_id": "x", "account_nm": "매출액",
                    "thstrm_amount": "100", "currency": "KRW",
                }
            ],
        }
        client = DartClient(api_key="KEY", transport=fake_transport(response))
        # Q1 report (11013) -> 2023-03-31.
        assert client.financial_statements("c", 2023, "11013")[0].report_period == date(2023, 3, 31)


class TestDisclosureList:
    def test_parses_disclosures(self):
        client = DartClient(api_key="KEY", transport=fake_transport(LIST_RESPONSE))
        disclosures = client.disclosure_list("00126380", date(2024, 1, 1), date(2024, 3, 31))
        assert len(disclosures) == 1
        disclosure = disclosures[0]
        assert isinstance(disclosure, Disclosure)
        assert disclosure.rcept_no == "20240315000123"
        assert disclosure.rcept_dt == date(2024, 3, 15)


class TestErrors:
    def test_non_success_status_raises(self):
        bad = {"status": "020", "message": "사용한도를 초과하였습니다.", "list": []}
        client = DartClient(api_key="KEY", transport=fake_transport(bad))
        with pytest.raises(DartApiError) as exc:
            client.financial_statements("c", 2023, "11011")
        assert "020" in str(exc.value)

    def test_no_data_status_returns_empty(self):
        # 013 = no matching data; a normal empty result, not an error.
        empty = {"status": "013", "message": "조회된 데이터가 없습니다."}
        client = DartClient(api_key="KEY", transport=fake_transport(empty))
        assert client.financial_statements("c", 2023, "11011") == []
