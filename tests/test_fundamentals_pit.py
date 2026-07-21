from __future__ import annotations

from datetime import date

from tradingbot.data.fundamentals import (
    Disclosure,
    FundamentalRecord,
    FundamentalStore,
    RawAccount,
    available_at,
    to_fundamental_record,
)


def account(name: str, amount: float | None, statement: str = "IS") -> RawAccount:
    return RawAccount(
        account_name=name,
        amount=amount,
        report_period=date(2023, 12, 31),
        currency="KRW",
        statement=statement,
        account_id=name,
    )


DISCLOSURE = Disclosure(
    rcept_no="20240315000123",
    report_name="사업보고서 (2023.12)",
    rcept_dt=date(2024, 3, 15),  # a Friday
)

ACCOUNTS = [
    account("매출액", 1000.0, "IS"),
    account("영업이익", 200.0, "IS"),
    account("감가상각비", 50.0, "CF"),
    account("유형자산의 취득", 80.0, "CF"),
    account("현금및현금성자산", 300.0, "BS"),
    account("단기차입금", 120.0, "BS"),
    account("장기차입금", 180.0, "BS"),
]


class TestAvailableAt:
    def test_next_trading_day_after_disclosure(self):
        # Disclosed Fri 2024-03-15 -> first usable Mon 2024-03-18.
        assert available_at(date(2024, 3, 15), "KR") == date(2024, 3, 18)

    def test_strictly_after_even_on_a_trading_day(self):
        # Availability is never the disclosure day itself.
        result = available_at(date(2024, 3, 14), "KR")  # Thursday
        assert result > date(2024, 3, 14)


class TestToFundamentalRecord:
    def test_maps_core_accounts(self):
        record = to_fundamental_record("00126380", ACCOUNTS, DISCLOSURE, "KR")
        assert isinstance(record, FundamentalRecord)
        assert record.corp_code == "00126380"
        assert record.revenue == 1000.0
        assert record.operating_income == 200.0
        assert record.depreciation_amortization == 50.0
        assert record.capex == 80.0

    def test_separates_period_announcement_and_availability(self):
        record = to_fundamental_record("c", ACCOUNTS, DISCLOSURE, "KR")
        assert record.report_period == date(2023, 12, 31)
        assert record.announcement_date == date(2024, 3, 15)
        assert record.available_at == date(2024, 3, 18)

    def test_net_debt_is_borrowings_minus_cash(self):
        # (120 + 180) - 300 = 0
        record = to_fundamental_record("c", ACCOUNTS, DISCLOSURE, "KR")
        assert record.net_debt == 0.0

    def test_missing_account_is_none_not_zero(self):
        accounts = [account("매출액", 1000.0)]  # no capex, no cash, etc.
        record = to_fundamental_record("c", accounts, DISCLOSURE, "KR")
        assert record.capex is None
        assert record.net_debt is None  # cannot compute without both sides


class TestFundamentalStorePit:
    def _store(self) -> FundamentalStore:
        store = FundamentalStore()
        store.add(to_fundamental_record("c", ACCOUNTS, DISCLOSURE, "KR"))
        older = Disclosure("2023", "사업보고서 (2022.12)", date(2023, 3, 15))
        store.add(to_fundamental_record("c", [account("매출액", 900.0)], older, "KR"))
        return store

    def test_as_of_returns_none_before_availability(self):
        store = self._store()
        # 2022 report available 2023-03-16; nothing is available on 2023-01-01.
        assert store.as_of("c", date(2023, 1, 1)) is None

    def test_as_of_hides_future_disclosure(self):
        store = self._store()
        # On 2024-03-17 the 2023 report (available 2024-03-18) is not yet visible.
        record = store.as_of("c", date(2024, 3, 17))
        assert record is not None
        assert record.revenue == 900.0  # still the older report

    def test_as_of_returns_latest_available(self):
        store = self._store()
        record = store.as_of("c", date(2024, 6, 1))
        assert record is not None
        assert record.revenue == 1000.0

    def test_as_of_unknown_corp_is_none(self):
        assert self._store().as_of("nope", date(2024, 6, 1)) is None
