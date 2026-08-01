"""Backfilling a window that sits *before* data already collected.

The daily batch resumes each panel from the day after its newest stored row.
That is what makes it cheap, and it is also why `start` was silently ignored
once anything had been stored — the Korean strategy's walk-forward needs
2021-2022, and the panels already hold 2023 onward, so the ordinary
incremental path could never reach it.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingbot.data.flows import update_flows
from tradingbot.data.panel import PanelStore
from tradingbot.data.pipeline import fundamental_years_for, run_pipeline
from tradingbot.data.valuation import update_valuation


def flow_frame(symbol: str, days: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [pd.Timestamp(day) for day in days],
            "symbol": [symbol] * len(days),
            "foreign_net": [1.0] * len(days),
            "institution_net": [2.0] * len(days),
            "individual_net": [-3.0] * len(days),
        }
    )


def valuation_frame(symbol: str, days: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [pd.Timestamp(day) for day in days],
            "symbol": [symbol] * len(days),
            "per": [10.0] * len(days),
            "pbr": [1.0] * len(days),
            "eps": [100.0] * len(days),
            "bps": [1000.0] * len(days),
            "div_yield": [0.02] * len(days),
        }
    )


def recording_fetcher(frame_for, calls: list[tuple[str, date, date]]):
    def fetch(symbol: str, start: date, end: date) -> pd.DataFrame:
        calls.append((symbol, start, end))
        days = pd.bdate_range(pd.Timestamp(start), pd.Timestamp(end))
        if len(days) == 0:
            return frame_for(symbol, [])
        return frame_for(symbol, [day.date() for day in days])

    return fetch


class TestFlowsBackfill:
    def test_incremental_run_ignores_start_once_data_exists(self, tmp_path):
        """The behaviour that blocked the task — pinned so the fix is visible."""
        store = PanelStore(tmp_path, "flows", "KR")
        calls: list[tuple[str, date, date]] = []
        fetch = recording_fetcher(flow_frame, calls)
        update_flows(
            store, symbols=["005930"], start=date(2023, 1, 2), end=date(2023, 1, 31), fetcher=fetch
        )
        calls.clear()

        update_flows(
            store, symbols=["005930"], start=date(2021, 1, 4), end=date(2021, 1, 29), fetcher=fetch
        )

        # Resumed from 2023-02-01, which is already past the requested end.
        assert calls == []

    def test_backfill_fetches_the_requested_window_verbatim(self, tmp_path):
        store = PanelStore(tmp_path, "flows", "KR")
        calls: list[tuple[str, date, date]] = []
        fetch = recording_fetcher(flow_frame, calls)
        update_flows(
            store, symbols=["005930"], start=date(2023, 1, 2), end=date(2023, 1, 31), fetcher=fetch
        )
        calls.clear()

        written = update_flows(
            store,
            symbols=["005930"],
            start=date(2021, 1, 4),
            end=date(2021, 1, 29),
            backfill=True,
            fetcher=fetch,
        )

        assert calls == [("005930", date(2021, 1, 4), date(2021, 1, 29))]
        assert written > 0

    def test_backfill_leaves_the_existing_window_intact(self, tmp_path):
        store = PanelStore(tmp_path, "flows", "KR")
        fetch = recording_fetcher(flow_frame, [])
        update_flows(
            store, symbols=["005930"], start=date(2023, 1, 2), end=date(2023, 1, 31), fetcher=fetch
        )

        update_flows(
            store,
            symbols=["005930"],
            start=date(2021, 1, 4),
            end=date(2021, 1, 29),
            backfill=True,
            fetcher=fetch,
        )

        panel = store.read()
        years = set(pd.to_datetime(panel["date"]).dt.year)
        assert years == {2021, 2023}

    def test_overlapping_backfill_does_not_duplicate_rows(self, tmp_path):
        """The panel store replaces same-key rows, so re-fetching is safe."""
        store = PanelStore(tmp_path, "flows", "KR")
        fetch = recording_fetcher(flow_frame, [])
        update_flows(
            store, symbols=["005930"], start=date(2023, 1, 2), end=date(2023, 1, 31), fetcher=fetch
        )
        before = len(store.read())

        update_flows(
            store,
            symbols=["005930"],
            start=date(2023, 1, 2),
            end=date(2023, 1, 31),
            backfill=True,
            fetcher=fetch,
        )

        assert len(store.read()) == before


class TestValuationBackfill:
    def test_backfill_fetches_the_requested_window(self, tmp_path):
        store = PanelStore(tmp_path, "valuation", "KR")
        calls: list[tuple[str, date, date]] = []
        fetch = recording_fetcher(valuation_frame, calls)
        update_valuation(
            store, symbols=["005930"], start=date(2023, 1, 2), end=date(2023, 1, 31), fetcher=fetch
        )
        calls.clear()

        update_valuation(
            store,
            symbols=["005930"],
            start=date(2021, 1, 4),
            end=date(2021, 1, 29),
            backfill=True,
            fetcher=fetch,
        )

        assert calls == [("005930", date(2021, 1, 4), date(2021, 1, 29))]


class TestFundamentalYears:
    def test_counts_back_from_today_without_a_range(self):
        years = fundamental_years_for(None, None, 3, today=date(2026, 8, 1))
        assert years == [2024, 2025, 2026]

    def test_a_range_spans_the_year_before_it(self):
        """Scoring January 2021 needs the FY2020 filings that were public then."""
        years = fundamental_years_for(date(2021, 1, 1), date(2022, 12, 31), 3)
        assert years == [2020, 2021, 2022]

    def test_start_alone_is_enough(self):
        assert fundamental_years_for(date(2021, 1, 1), None, 3) == [2020, 2021]


class TestPipelineThreadsTheWindow:
    def test_collectors_receive_the_requested_window(self, tmp_path):
        seen: dict[str, object] = {}

        def collector(**kwargs):
            seen.update(kwargs)
            return 1

        result = run_pipeline(
            {"pipeline": {"processed_dir": str(tmp_path / "p"), "log_dir": str(tmp_path / "l")}},
            market="KR",
            symbols=["005930"],
            start=date(2021, 1, 1),
            end=date(2022, 12, 31),
            backfill=True,
            collectors={"flows": collector},
        )

        assert result.ok
        assert seen["symbols"] == ["005930"]

    def test_backfill_defaults_off(self, tmp_path):
        """The daily batch must keep its cheap incremental behaviour."""
        import inspect

        from tradingbot.data.pipeline import run_pipeline as target

        assert inspect.signature(target).parameters["backfill"].default is False
