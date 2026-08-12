from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.events import (
    EVENT_COLUMNS,
    classify_report,
    disclosures_to_events,
    update_events,
)
from tradingbot.data.fundamentals import Disclosure
from tradingbot.data.panel import PanelStore


class TestClassifyReport:
    @pytest.mark.parametrize(
        "name",
        [
            "연결재무제표기준영업(잠정)실적(공정공시)",
            "영업(잠정)실적(공정공시)",
            "매출액또는손익구조30%(대규모법인은15%)이상변동",
        ],
    )
    def test_provisional_results_are_the_event(self, name):
        assert classify_report(name) == "provisional"

    @pytest.mark.parametrize(
        "name", ["분기보고서 (2024.03)", "반기보고서 (2024.06)", "사업보고서 (2023.12)"]
    )
    def test_periodic_reports_are_the_fallback(self, name):
        assert classify_report(name) == "periodic"

    @pytest.mark.parametrize(
        "name",
        [
            "주요사항보고서(유상증자결정)",
            "임원ㆍ주요주주특정증권등소유상황보고서",
            "기업설명회(IR)개최(안내공시)",
        ],
    )
    def test_unrelated_filings_are_not_events(self, name):
        assert classify_report(name) is None

    def test_whitespace_and_prefixes_do_not_break_matching(self):
        # DART prefixes filings with tags such as "[기재정정]" (amended).
        assert classify_report("  [기재정정]연결재무제표기준영업(잠정)실적(공정공시) ") == "provisional"

    def test_empty_name_is_not_an_event(self):
        assert classify_report("") is None


def disclosure(name: str, day: str, rcept_no: str = "1") -> Disclosure:
    return Disclosure(rcept_no=rcept_no, report_name=name, rcept_dt=date.fromisoformat(day))


class TestDisclosuresToEvents:
    def test_keeps_only_earnings_filings(self):
        frame = disclosures_to_events(
            [
                disclosure("연결재무제표기준영업(잠정)실적(공정공시)", "2024-01-09", "a"),
                disclosure("주요사항보고서(유상증자결정)", "2024-01-15", "b"),
                disclosure("사업보고서 (2023.12)", "2024-03-11", "c"),
            ],
            "005930",
        )
        assert list(frame["event_kind"]) == ["provisional", "periodic"]
        assert list(frame.columns) == EVENT_COLUMNS

    def test_event_date_is_the_receipt_date(self):
        frame = disclosures_to_events(
            [disclosure("영업(잠정)실적(공정공시)", "2024-01-09")], "005930"
        )
        assert frame["date"].iloc[0] == pd.Timestamp("2024-01-09")

    def test_symbol_is_upper_cased(self):
        frame = disclosures_to_events(
            [disclosure("영업(잠정)실적(공정공시)", "2024-01-09")], "005930"
        )
        assert frame["symbol"].iloc[0] == "005930"

    def test_provisional_wins_when_both_land_on_one_day(self):
        # PanelStore keys on (date, symbol), so two filings on one day collapse
        # to one row. The provisional release is the market event; keeping the
        # periodic report instead would reintroduce the very error this module
        # exists to fix.
        frame = disclosures_to_events(
            [
                disclosure("사업보고서 (2023.12)", "2024-03-11", "c"),
                disclosure("연결재무제표기준영업(잠정)실적(공정공시)", "2024-03-11", "d"),
            ],
            "005930",
        )
        assert len(frame) == 1
        assert frame["event_kind"].iloc[0] == "provisional"

    def test_sorted_by_date(self):
        frame = disclosures_to_events(
            [
                disclosure("영업(잠정)실적(공정공시)", "2024-04-05", "b"),
                disclosure("영업(잠정)실적(공정공시)", "2024-01-09", "a"),
            ],
            "005930",
        )
        assert list(frame["date"]) == [pd.Timestamp("2024-01-09"), pd.Timestamp("2024-04-05")]

    def test_no_earnings_filings_gives_empty_frame_with_schema(self):
        frame = disclosures_to_events(
            [disclosure("주요사항보고서(유상증자결정)", "2024-01-15")], "005930"
        )
        assert frame.empty
        assert list(frame.columns) == EVENT_COLUMNS

    def test_empty_input_gives_empty_frame_with_schema(self):
        frame = disclosures_to_events([], "005930")
        assert frame.empty
        assert list(frame.columns) == EVENT_COLUMNS


class TestUpdateEvents:
    def test_writes_rows_with_availability_on_the_next_trading_day(self, tmp_path):
        store = PanelStore(tmp_path, "events", "KR")

        def fetcher(corp_code, start, end, symbol):
            # 2024-01-06 is a Saturday; the next KRX trading day is Monday.
            return disclosures_to_events(
                [disclosure("영업(잠정)실적(공정공시)", "2024-01-06")], symbol
            )

        written = update_events(
            store,
            symbols=["005930"],
            corp_codes={"005930": "00126380"},
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            fetcher=fetcher,
        )
        assert written == 1
        panel = store.read()
        assert panel["available_at"].iloc[0] == pd.Timestamp("2024-01-08")
        assert panel["source"].iloc[0] == "dart"

    def test_recollecting_unchanged_data_writes_nothing(self, tmp_path):
        store = PanelStore(tmp_path, "events", "KR")

        def fetcher(corp_code, start, end, symbol):
            return disclosures_to_events(
                [disclosure("영업(잠정)실적(공정공시)", "2024-01-09")], symbol
            )

        kwargs = dict(
            symbols=["005930"],
            corp_codes={"005930": "00126380"},
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            fetcher=fetcher,
        )
        assert update_events(store, **kwargs) == 1
        assert update_events(store, **kwargs) == 0

    def test_a_symbol_without_a_corp_code_is_skipped_not_fatal(self, tmp_path):
        store = PanelStore(tmp_path, "events", "KR")

        def fetcher(corp_code, start, end, symbol):
            return disclosures_to_events(
                [disclosure("영업(잠정)실적(공정공시)", "2024-01-09")], symbol
            )

        written = update_events(
            store,
            symbols=["005930", "999999"],
            corp_codes={"005930": "00126380"},
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            fetcher=fetcher,
        )
        assert written == 1

    def test_one_failing_symbol_does_not_abort_the_batch(self, tmp_path):
        store = PanelStore(tmp_path, "events", "KR")

        def fetcher(corp_code, start, end, symbol):
            if symbol == "000660":
                raise RuntimeError("DART timeout")
            return disclosures_to_events(
                [disclosure("영업(잠정)실적(공정공시)", "2024-01-09")], symbol
            )

        written = update_events(
            store,
            symbols=["000660", "005930"],
            corp_codes={"000660": "00164779", "005930": "00126380"},
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            fetcher=fetcher,
        )
        assert written == 1
