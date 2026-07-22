from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.fundamentals import Disclosure, RawAccount
from tradingbot.data.fundamentals_panel import (
    FUNDAMENTAL_COLUMNS,
    PANEL_COLUMNS,
    MissingApiKeyError,
    accounts_to_panel_row,
    dart_api_key,
    fetch_panel_row,
    update_fundamentals,
)
from tradingbot.data.panel import PanelStore

RCEPT_NO = "20240315000123"
# 2024-03-15 is a Friday, so the next trading day is Monday 2024-03-18.
DISCLOSURE = Disclosure(rcept_no=RCEPT_NO, report_name="사업보고서", rcept_dt=date(2024, 3, 15))


@pytest.fixture
def store(tmp_path):
    return PanelStore(tmp_path, "fundamentals", "KR")


def make_accounts(
    period: date = date(2023, 12, 31), rcept_no: str = RCEPT_NO
) -> list[RawAccount]:
    def account(name: str, amount: float | None, statement: str) -> RawAccount:
        return RawAccount(
            account_name=name,
            amount=amount,
            report_period=period,
            currency="KRW",
            statement=statement,
            account_id="",
            rcept_no=rcept_no,
        )

    return [
        account("매출액", 1_000_000.0, "IS"),
        account("영업이익", 200_000.0, "IS"),
        account("당기순이익", 150_000.0, "IS"),
        account("자산총계", 5_000_000.0, "BS"),
        account("자본총계", 3_000_000.0, "BS"),
    ]


class StubClient:
    """DartClient stand-in: canned accounts and disclosures, no network."""

    def __init__(self, accounts=None, disclosures=None):
        self._accounts = make_accounts() if accounts is None else accounts
        self._disclosures = [DISCLOSURE] if disclosures is None else disclosures

    def financial_statements(self, corp_code, year, report_code, fs_div="CFS"):
        return self._accounts

    def disclosure_list(self, corp_code, start, end):
        return self._disclosures


class TestApiKey:
    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("DART_API_KEY", raising=False)
        with pytest.raises(MissingApiKeyError):
            dart_api_key()

    def test_key_is_read_from_environment(self, monkeypatch):
        monkeypatch.setenv("DART_API_KEY", "secret")
        assert dart_api_key() == "secret"


class TestAccountsToPanelRow:
    def test_maps_accounts_and_dates(self):
        frame = accounts_to_panel_row(make_accounts(), DISCLOSURE, "005930")
        assert list(frame.columns) == PANEL_COLUMNS
        row = frame.iloc[0]
        assert row["symbol"] == "005930"
        assert row["revenue"] == 1_000_000
        assert row["operating_income"] == 200_000
        assert row["total_assets"] == 5_000_000
        # The row is dated by the accounting period end...
        assert row["date"] == pd.Timestamp("2023-12-31")
        # ...while the announcement date comes from the filing itself.
        assert row["announcement_date"] == pd.Timestamp("2024-03-15")

    def test_absent_account_is_nan_not_zero(self):
        accounts = [a for a in make_accounts() if a.account_name != "당기순이익"]
        frame = accounts_to_panel_row(accounts, DISCLOSURE, "005930")
        # NaN so a factor can tell "not reported" from "reported as zero".
        assert pd.isna(frame.iloc[0]["net_income"])
        assert frame.iloc[0]["revenue"] == 1_000_000

    def test_none_amount_is_nan(self):
        accounts = [
            a if a.account_name != "매출액" else RawAccount(
                account_name="매출액",
                amount=None,
                report_period=a.report_period,
                currency=a.currency,
                statement=a.statement,
                account_id=a.account_id,
                rcept_no=a.rcept_no,
            )
            for a in make_accounts()
        ]
        frame = accounts_to_panel_row(accounts, DISCLOSURE, "005930")
        assert pd.isna(frame.iloc[0]["revenue"])

    def test_empty_accounts_return_empty_frame_with_schema(self):
        frame = accounts_to_panel_row([], DISCLOSURE, "005930")
        assert frame.empty
        assert list(frame.columns) == PANEL_COLUMNS

    def test_unmapped_accounts_are_ignored(self):
        extra = make_accounts() + [
            RawAccount("이상한계정", 1.0, date(2023, 12, 31), "KRW", "BS", "", RCEPT_NO)
        ]
        frame = accounts_to_panel_row(extra, DISCLOSURE, "005930")
        assert list(frame.columns) == PANEL_COLUMNS
        assert len(frame.columns) == len(PANEL_COLUMNS)


class TestFetchPanelRow:
    def test_uses_the_matching_disclosure(self):
        frame = fetch_panel_row(StubClient(), "00126380", 2023, "11011", "005930")
        assert frame.iloc[0]["announcement_date"] == pd.Timestamp("2024-03-15")

    def test_no_statements_returns_empty(self):
        frame = fetch_panel_row(StubClient(accounts=[]), "00126380", 2023, "11011", "005930")
        assert frame.empty

    def test_unmatched_disclosure_returns_empty_rather_than_guessing(self):
        # Without a receipt date there is no defensible availability date;
        # inventing one would be exactly the look-ahead this module prevents.
        other = Disclosure(rcept_no="99999999999999", report_name="기타", rcept_dt=date(2024, 3, 15))
        frame = fetch_panel_row(StubClient(disclosures=[other]), "00126380", 2023, "11011", "005930")
        assert frame.empty


class TestUpdateFundamentals:
    @staticmethod
    def stub_fetcher(corp_code, year, report_code, symbol):
        if report_code != "11011":
            return pd.DataFrame(columns=PANEL_COLUMNS)
        return accounts_to_panel_row(make_accounts(), DISCLOSURE, symbol)

    def test_availability_follows_announcement_not_period_end(self, store):
        written = update_fundamentals(
            store,
            symbols=["005930"],
            corp_codes={"005930": "00126380"},
            years=[2023],
            fetcher=self.stub_fetcher,
        )
        assert written == 1

        row = store.read().iloc[0]
        assert row["date"] == pd.Timestamp("2023-12-31")
        # Announced Friday 2024-03-15 -> usable from Monday 2024-03-18.
        assert row["available_at"] == pd.Timestamp("2024-03-18")
        assert row["source"] == "dart"

    def test_as_of_before_announcement_hides_the_row(self, store):
        update_fundamentals(
            store,
            symbols=["005930"],
            corp_codes={"005930": "00126380"},
            years=[2023],
            fetcher=self.stub_fetcher,
        )
        # The period ended 2023-12-31, but nobody knew the numbers until March.
        assert store.read(as_of=date(2024, 1, 15)).empty
        assert len(store.read(as_of=date(2024, 3, 18))) == 1

    def test_symbol_without_corp_code_is_skipped(self, store):
        assert (
            update_fundamentals(
                store,
                symbols=["999999"],
                corp_codes={},
                years=[2023],
                fetcher=self.stub_fetcher,
            )
            == 0
        )

    def test_fetch_failure_skips_symbol_without_aborting(self, store):
        def flaky(corp_code, year, report_code, symbol):
            if corp_code == "BAD":
                raise RuntimeError("boom")
            return self.stub_fetcher(corp_code, year, report_code, symbol)

        written = update_fundamentals(
            store,
            symbols=["999999", "005930"],
            corp_codes={"999999": "BAD", "005930": "00126380"},
            years=[2023],
            fetcher=flaky,
        )
        assert written == 1

    def test_missing_api_key_propagates_not_swallowed(self, store):
        def keyless(corp_code, year, report_code, symbol):
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

    def test_rerun_is_idempotent(self, store):
        for _ in range(2):
            update_fundamentals(
                store,
                symbols=["005930"],
                corp_codes={"005930": "00126380"},
                years=[2023],
                fetcher=self.stub_fetcher,
            )
        assert len(store.read()) == 1

    @pytest.mark.parametrize(
        "report_code,expected_period_end",
        [
            ("11013", "2023-03-31"),
            ("11012", "2023-06-30"),
            ("11014", "2023-09-30"),
            ("11011", "2023-12-31"),
        ],
    )
    def test_each_report_code_lands_on_its_period_end(
        self, store, report_code, expected_period_end
    ):
        period = pd.Timestamp(expected_period_end).date()

        def fetcher(corp_code, year, code, symbol):
            if code != report_code:
                return pd.DataFrame(columns=PANEL_COLUMNS)
            return accounts_to_panel_row(make_accounts(period=period), DISCLOSURE, symbol)

        update_fundamentals(
            store,
            symbols=["005930"],
            corp_codes={"005930": "00126380"},
            years=[2023],
            fetcher=fetcher,
        )
        assert store.read().iloc[0]["date"] == pd.Timestamp(expected_period_end)


def test_panel_columns_cover_every_mapped_account():
    for column in FUNDAMENTAL_COLUMNS:
        assert column in PANEL_COLUMNS
