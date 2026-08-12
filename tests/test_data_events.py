from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.events import (
    EVENT_COLUMNS,
    classify_report,
    disclosures_to_events,
    schedule_dates,
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


def events_frame(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime([day for day, _ in rows]),
            "symbol": "005930",
            "event_kind": [kind for _, kind in rows],
        }
    )


# A year of what a large-cap Korean filer actually produces: a provisional
# release, then the periodic report repeating the same quarter weeks later.
INTERLEAVED = events_frame(
    [
        ("2024-01-09", "provisional"),
        ("2024-03-12", "periodic"),
        ("2024-04-05", "provisional"),
        ("2024-05-16", "periodic"),
        ("2024-07-05", "provisional"),
        ("2024-08-14", "periodic"),
        ("2024-10-08", "provisional"),
        ("2024-11-14", "periodic"),
    ]
)


class TestScheduleDates:
    def test_provisional_releases_are_used_alone(self):
        # The bug this exists to prevent: reading both kinds interleaves a
        # provisional release with the periodic report repeating it, halving
        # the median gap so the estimator expects a report every six weeks.
        assert schedule_dates(INTERLEAVED) == [
            date(2024, 1, 9),
            date(2024, 4, 5),
            date(2024, 7, 5),
            date(2024, 10, 8),
        ]

    def test_the_resulting_gaps_look_quarterly(self):
        dates = schedule_dates(INTERLEAVED)
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        assert all(80 <= gap <= 100 for gap in gaps), gaps

    def test_periodic_reports_are_used_when_there_are_no_provisionals(self):
        # Small caps often never publish provisional results. They still have
        # to be scheduled off something.
        periodic_only = events_frame(
            [
                ("2024-03-12", "periodic"),
                ("2024-05-16", "periodic"),
                ("2024-08-14", "periodic"),
                ("2024-11-14", "periodic"),
            ]
        )
        assert len(schedule_dates(periodic_only)) == 4

    def test_too_few_provisionals_falls_back_to_periodic(self):
        # A filer that only recently started publishing provisionals has no
        # usable provisional history yet.
        mostly_periodic = events_frame(
            [
                ("2023-03-10", "periodic"),
                ("2023-05-15", "periodic"),
                ("2023-08-14", "periodic"),
                ("2023-11-14", "periodic"),
                ("2024-01-09", "provisional"),
            ]
        )
        assert schedule_dates(mostly_periodic) == [
            date(2023, 3, 10),
            date(2023, 5, 15),
            date(2023, 8, 14),
            date(2023, 11, 14),
        ]

    def test_the_two_kinds_are_never_returned_together(self):
        combined = set(schedule_dates(INTERLEAVED))
        periodic = {date(2024, 3, 12), date(2024, 5, 16), date(2024, 8, 14), date(2024, 11, 14)}
        assert not combined & periodic

    def test_duplicate_dates_collapse(self):
        # An amended filing re-received on the same day is one announcement.
        # A repeated date would otherwise contribute a zero gap and drag the
        # estimator's median down.
        doubled = events_frame(
            [
                ("2024-01-09", "provisional"),
                ("2024-01-09", "provisional"),
                ("2024-04-05", "provisional"),
                ("2024-07-05", "provisional"),
                ("2024-10-08", "provisional"),
            ]
        )
        assert schedule_dates(doubled) == [
            date(2024, 1, 9),
            date(2024, 4, 5),
            date(2024, 7, 5),
            date(2024, 10, 8),
        ]

    def test_too_few_of_either_kind_gives_nothing_to_estimate_from(self):
        # Not an error: one announcement is no schedule, and the caller reads
        # an empty series as "unknown", which leaves the position alone.
        assert schedule_dates(events_frame([("2024-01-09", "provisional")])) == []

    def test_empty_frame_gives_no_dates(self):
        assert schedule_dates(pd.DataFrame(columns=EVENT_COLUMNS)) == []

    def test_a_panel_without_event_kinds_still_yields_dates(self):
        legacy = pd.DataFrame(
            {"date": pd.to_datetime(["2024-01-09", "2024-04-05"]), "symbol": "005930"}
        )
        assert schedule_dates(legacy) == [date(2024, 1, 9), date(2024, 4, 5)]


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
