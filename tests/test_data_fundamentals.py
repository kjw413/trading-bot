from __future__ import annotations

import pandas as pd
import pytest

from tradingbot.data.fundamentals import (
    FUNDAMENTAL_COLUMNS,
    MissingApiKeyError,
    dart_api_key,
    parse_financials,
    update_fundamentals,
)
from tradingbot.data.panel import PanelStore


@pytest.fixture
def store(tmp_path):
    return PanelStore(tmp_path, "fundamentals", "KR")


def payload(rcept_no: str = "20240315000123") -> dict:
    """Shape of a DART fnlttSinglAcnt response (주요계정)."""
    def row(name: str, amount: str, statement: str) -> dict:
        return {
            "rcept_no": rcept_no,
            "bsns_year": "2023",
            "reprt_code": "11011",
            "sj_div": statement,
            "account_nm": name,
            "thstrm_amount": amount,
        }

    return {
        "status": "000",
        "message": "정상",
        "list": [
            row("매출액", "1,000,000", "IS"),
            row("영업이익", "200,000", "IS"),
            row("당기순이익", "150,000", "IS"),
            row("자산총계", "5,000,000", "BS"),
            row("자본총계", "3,000,000", "BS"),
        ],
    }


class TestApiKey:
    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("DART_API_KEY", raising=False)
        with pytest.raises(MissingApiKeyError):
            dart_api_key()

    def test_key_is_read_from_environment(self, monkeypatch):
        monkeypatch.setenv("DART_API_KEY", "secret")
        assert dart_api_key() == "secret"


class TestParseFinancials:
    def test_extracts_accounts_and_dates(self):
        frame = parse_financials(payload(), "005930")
        assert len(frame) == 1
        row = frame.iloc[0]
        assert row["symbol"] == "005930"
        assert row["revenue"] == 1_000_000
        assert row["operating_income"] == 200_000
        assert row["total_assets"] == 5_000_000
        # Annual report for 2023 -> period end is the fiscal year end.
        assert row["date"] == pd.Timestamp("2023-12-31")
        # Announcement date comes from the receipt number's leading date.
        assert row["announcement_date"] == pd.Timestamp("2024-03-15")

    @pytest.mark.parametrize(
        "report_code,expected",
        [
            ("11013", "2023-03-31"),
            ("11012", "2023-06-30"),
            ("11014", "2023-09-30"),
            ("11011", "2023-12-31"),
        ],
    )
    def test_report_code_maps_to_fiscal_period_end(self, report_code, expected):
        data = payload()
        for item in data["list"]:
            item["reprt_code"] = report_code
        frame = parse_financials(data, "005930")
        assert frame.iloc[0]["date"] == pd.Timestamp(expected)

    def test_negative_amount_in_parentheses(self):
        data = payload()
        data["list"][1]["thstrm_amount"] = "(50,000)"
        frame = parse_financials(data, "005930")
        assert frame.iloc[0]["operating_income"] == -50_000

    def test_blank_amount_becomes_nan(self):
        data = payload()
        data["list"][0]["thstrm_amount"] = "-"
        frame = parse_financials(data, "005930")
        assert pd.isna(frame.iloc[0]["revenue"])

    def test_no_data_status_returns_empty(self):
        frame = parse_financials({"status": "013", "message": "조회된 데이터가 없습니다."}, "005930")
        assert frame.empty
        assert list(frame.columns) == ["date", "symbol", "announcement_date"] + FUNDAMENTAL_COLUMNS

    def test_error_status_raises(self):
        with pytest.raises(RuntimeError, match="020"):
            parse_financials({"status": "020", "message": "요청 제한 초과"}, "005930")


class TestUpdateFundamentals:
    def test_availability_follows_announcement_not_period_end(self, store):
        def fetcher(corp_code, year, report_code):
            return payload() if report_code == "11011" else {"status": "013", "message": "none"}

        written = update_fundamentals(
            store,
            symbols=["005930"],
            corp_codes={"005930": "00126380"},
            years=[2023],
            fetcher=fetcher,
        )
        assert written == 1

        row = store.read().iloc[0]
        assert row["date"] == pd.Timestamp("2023-12-31")
        # Announced 2024-03-15 (Fri) -> usable from the next trading day.
        assert row["available_at"] == pd.Timestamp("2024-03-18")
        assert row["source"] == "dart"

    def test_as_of_before_announcement_hides_the_row(self, store):
        def fetcher(corp_code, year, report_code):
            return payload() if report_code == "11011" else {"status": "013", "message": "none"}

        update_fundamentals(
            store,
            symbols=["005930"],
            corp_codes={"005930": "00126380"},
            years=[2023],
            fetcher=fetcher,
        )
        from datetime import date

        # Period ended 2023-12-31 but nobody knew the numbers until March.
        assert store.read(as_of=date(2024, 1, 15)).empty
        assert len(store.read(as_of=date(2024, 3, 18))) == 1

    def test_symbol_without_corp_code_is_skipped(self, store):
        def fetcher(corp_code, year, report_code):
            return payload()

        assert (
            update_fundamentals(
                store, symbols=["999999"], corp_codes={}, years=[2023], fetcher=fetcher
            )
            == 0
        )

    def test_fetch_failure_skips_symbol_without_aborting(self, store):
        def flaky(corp_code, year, report_code):
            if corp_code == "BAD":
                raise RuntimeError("boom")
            return payload() if report_code == "11011" else {"status": "013", "message": "none"}

        written = update_fundamentals(
            store,
            symbols=["999999", "005930"],
            corp_codes={"999999": "BAD", "005930": "00126380"},
            years=[2023],
            fetcher=flaky,
        )
        assert written == 1

    def test_missing_api_key_propagates_not_swallowed(self, store):
        def keyless(corp_code, year, report_code):
            raise MissingApiKeyError("DART_API_KEY is not set.")

        # A missing key is a batch-level config problem: it must surface, not be
        # absorbed once per (symbol, year, report) into a silent zero-row result.
        with pytest.raises(MissingApiKeyError):
            update_fundamentals(
                store,
                symbols=["005930"],
                corp_codes={"005930": "00126380"},
                years=[2023],
                fetcher=keyless,
            )
