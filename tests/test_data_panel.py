from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from tradingbot.data.panel import (
    PANEL_META_COLUMNS,
    PanelStore,
    attach_metadata,
    next_trading_day_availability,
)


def make_frame(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": pd.Timestamp(d), "symbol": s, "value": v} for d, s, v in rows]
    )


@pytest.fixture
def store(tmp_path):
    return PanelStore(tmp_path, "flows", "KR")


def tagged(frame: pd.DataFrame, available_at: pd.Series | str) -> pd.DataFrame:
    return attach_metadata(frame, source="test", available_at=available_at, data_version="1")


class TestAttachMetadata:
    def test_adds_all_meta_columns(self):
        frame = tagged(make_frame([("2024-01-02", "005930", 1.0)]), "2024-01-03")
        for column in PANEL_META_COLUMNS:
            assert column in frame.columns
        assert frame.loc[0, "source"] == "test"
        assert frame.loc[0, "available_at"] == pd.Timestamp("2024-01-03")
        assert frame.loc[0, "data_version"] == "1"

    def test_ingested_at_is_timezone_aware_utc(self):
        frame = tagged(make_frame([("2024-01-02", "005930", 1.0)]), "2024-01-03")
        ingested = frame.loc[0, "ingested_at"]
        assert ingested.tzinfo is not None

    def test_explicit_ingested_at_is_preserved(self):
        moment = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)
        frame = attach_metadata(
            make_frame([("2024-01-02", "005930", 1.0)]),
            source="test",
            available_at="2024-01-03",
            data_version="1",
            ingested_at=moment,
        )
        assert frame.loc[0, "ingested_at"] == pd.Timestamp(moment)

    def test_missing_key_column_raises(self):
        with pytest.raises(ValueError, match="date"):
            attach_metadata(
                pd.DataFrame({"symbol": ["005930"], "value": [1.0]}),
                source="test",
                available_at="2024-01-03",
                data_version="1",
            )


class TestNextTradingDayAvailability:
    def test_weekday_maps_to_next_weekday(self):
        dates = pd.Series([pd.Timestamp("2024-01-02")])  # Tue
        assert next_trading_day_availability(dates, "KR").iloc[0] == pd.Timestamp("2024-01-03")

    def test_friday_maps_past_the_weekend(self):
        dates = pd.Series([pd.Timestamp("2024-01-05")])  # Fri
        assert next_trading_day_availability(dates, "KR").iloc[0] == pd.Timestamp("2024-01-08")

    def test_empty_series_returns_empty(self):
        result = next_trading_day_availability(pd.Series([], dtype="datetime64[ns]"), "KR")
        assert result.empty


class TestPanelStoreRoundTrip:
    def test_append_then_read(self, store):
        store.append(tagged(make_frame([("2024-01-02", "005930", 1.0)]), "2024-01-03"))
        result = store.read()
        assert len(result) == 1
        assert result.loc[0, "symbol"] == "005930"
        assert result.loc[0, "value"] == 1.0

    def test_partitions_by_year(self, store, tmp_path):
        store.append(
            tagged(
                make_frame([("2023-12-28", "005930", 1.0), ("2024-01-02", "005930", 2.0)]),
                pd.Series([pd.Timestamp("2023-12-29"), pd.Timestamp("2024-01-03")]),
            )
        )
        assert (tmp_path / "flows" / "KR" / "2023.parquet").exists()
        assert (tmp_path / "flows" / "KR" / "2024.parquet").exists()
        assert store.years() == [2023, 2024]

    def test_append_replaces_same_key(self, store):
        store.append(tagged(make_frame([("2024-01-02", "005930", 1.0)]), "2024-01-03"))
        store.append(tagged(make_frame([("2024-01-02", "005930", 9.0)]), "2024-01-03"))
        result = store.read()
        assert len(result) == 1
        assert result.loc[0, "value"] == 9.0

    def test_append_returns_rows_actually_added_not_incoming_count(self, store):
        frame = tagged(make_frame([("2024-01-02", "005930", 1.0)]), "2024-01-03")
        first = store.append(frame)
        second = store.append(frame)
        assert first == 1
        assert second == 0

    def test_append_counts_changed_rows_not_just_new_ones(self, store):
        first = tagged(make_frame([("2024-01-02", "005930", 1.0)]), "2024-01-03")
        assert store.append(first) == 1

        # Same key, corrected value — a restatement. The operator must see that
        # something actually changed, not a silent zero.
        revised = tagged(make_frame([("2024-01-02", "005930", 9.0)]), "2024-01-03")
        assert store.append(revised) == 1
        assert store.read().loc[0, "value"] == 9.0

    def test_append_ignores_ingested_at_when_counting_changes(self, store):
        frame = tagged(make_frame([("2024-01-02", "005930", 1.0)]), "2024-01-03")
        assert store.append(frame) == 1

        # Re-collecting identical data stamps a new ingested_at; that alone is
        # not a change and must not be reported as one.
        resent = tagged(make_frame([("2024-01-02", "005930", 1.0)]), "2024-01-03")
        assert store.append(resent) == 0

    def test_append_counts_intra_chunk_duplicate_key_once(self, store):
        # Two rows for the same key in one call collapse to one stored row,
        # so the operator-facing count must be 1, not 2.
        frame = tagged(
            make_frame([("2024-01-02", "005930", 1.0), ("2024-01-02", "005930", 9.0)]),
            "2024-01-03",
        )
        assert store.append(frame) == 1
        assert len(store.read()) == 1
        assert store.read().loc[0, "value"] == 9.0

    def test_append_treats_missing_values_as_equal(self, store):
        rows = make_frame([("2024-01-02", "005930", float("nan"))])
        assert store.append(tagged(rows, "2024-01-03")) == 1
        # NaN == NaN must count as unchanged, or a field that is legitimately
        # empty would report as changed on every single run.
        assert store.append(tagged(make_frame([("2024-01-02", "005930", float("nan"))]), "2024-01-03")) == 0

    def test_read_missing_dataset_is_empty_not_error(self, tmp_path):
        empty_store = PanelStore(tmp_path, "nothing", "KR")
        assert empty_store.read().empty
        assert empty_store.years() == []
        assert empty_store.last_date() is None


class TestPanelStorePointInTime:
    @pytest.fixture
    def filled(self, store):
        store.append(
            tagged(
                make_frame([("2024-01-02", "005930", 1.0), ("2024-01-03", "005930", 2.0)]),
                pd.Series([pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-04")]),
            )
        )
        return store

    def test_as_of_hides_not_yet_available_rows(self, filled):
        result = filled.read(as_of=date(2024, 1, 3))
        assert len(result) == 1
        assert result.loc[0, "value"] == 1.0

    def test_as_of_on_availability_date_includes_row(self, filled):
        assert len(filled.read(as_of=date(2024, 1, 4))) == 2

    def test_as_of_before_everything_is_empty(self, filled):
        assert filled.read(as_of=date(2024, 1, 1)).empty

    def test_read_without_as_of_returns_everything(self, filled):
        assert len(filled.read()) == 2


class TestPanelStoreFilters:
    @pytest.fixture
    def filled(self, store):
        store.append(
            tagged(
                make_frame(
                    [
                        ("2024-01-02", "005930", 1.0),
                        ("2024-01-02", "000660", 2.0),
                        ("2024-02-01", "005930", 3.0),
                    ]
                ),
                "2024-03-01",
            )
        )
        return store

    def test_symbol_filter_is_case_insensitive(self, filled):
        assert len(filled.read(symbols=["005930"])) == 2

    def test_date_range_filter(self, filled):
        result = filled.read(start=date(2024, 1, 1), end=date(2024, 1, 31))
        assert len(result) == 2

    def test_last_date_overall_and_per_symbol(self, filled):
        assert filled.last_date() == date(2024, 2, 1)
        assert filled.last_date("000660") == date(2024, 1, 2)
        assert filled.last_date("999999") is None

    def test_read_is_sorted_by_date_then_symbol(self, filled):
        result = filled.read()
        assert list(result["date"]) == sorted(result["date"])
