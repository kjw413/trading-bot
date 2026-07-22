from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.data.cache import ParquetCache
from tradingbot.data.panel import PanelStore, attach_metadata
from tradingbot.data.store import ParquetDataStore


def panel_frame(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": pd.Timestamp(d), "symbol": s, "value": v} for d, s, v in rows]
    )


@pytest.fixture
def store(tmp_path):
    processed = tmp_path / "processed"
    panel = PanelStore(processed, "flows", "KR")
    panel.append(
        attach_metadata(
            panel_frame(
                [
                    ("2024-01-02", "005930", 1.0),
                    ("2024-01-03", "005930", 2.0),
                    ("2024-01-02", "000660", 3.0),
                ]
            ),
            source="test",
            available_at=pd.Series(
                [
                    pd.Timestamp("2024-01-03"),
                    pd.Timestamp("2024-01-04"),
                    pd.Timestamp("2024-01-03"),
                ]
            ),
            data_version="1",
        )
    )
    return ParquetDataStore(ParquetCache(tmp_path / "cache"), "KR", processed_root=processed)


class TestPanelAccess:
    def test_reads_rows_visible_at_as_of(self, store):
        frame = store.panel("flows", date(2024, 1, 3))
        assert len(frame) == 2  # the 2024-01-03 observation is not yet available

    def test_later_as_of_sees_more(self, store):
        assert len(store.panel("flows", date(2024, 1, 4))) == 3

    def test_as_of_before_anything_is_empty(self, store):
        assert store.panel("flows", date(2024, 1, 1)).empty

    def test_symbol_filter(self, store):
        frame = store.panel("flows", date(2024, 1, 4), symbols=["005930"])
        assert set(frame["symbol"]) == {"005930"}

    def test_unknown_dataset_is_empty_not_error(self, store):
        assert store.panel("nope", date(2024, 1, 4)).empty

    def test_store_without_processed_root_returns_empty(self, tmp_path):
        bare = ParquetDataStore(ParquetCache(tmp_path), "KR")
        assert bare.panel("flows", date(2024, 1, 4)).empty


class TestPanelLatest:
    def test_takes_the_most_recent_observation_per_symbol(self, store):
        latest = store.panel_latest("flows", date(2024, 1, 4), ["005930", "000660"], "value")
        assert latest.loc["005930"] == 2.0  # newer of the two
        assert latest.loc["000660"] == 3.0

    def test_respects_the_as_of_barrier(self, store):
        latest = store.panel_latest("flows", date(2024, 1, 3), ["005930"], "value")
        assert latest.loc["005930"] == 1.0  # the newer row is not yet available

    def test_symbol_without_data_is_nan(self, store):
        latest = store.panel_latest("flows", date(2024, 1, 4), ["999999"], "value")
        assert np.isnan(latest.loc["999999"])

    def test_missing_column_raises(self, store):
        with pytest.raises(KeyError):
            store.panel_latest("flows", date(2024, 1, 4), ["005930"], "nope")

    def test_empty_dataset_yields_all_nan(self, store):
        latest = store.panel_latest("nope", date(2024, 1, 4), ["005930"], "value")
        assert np.isnan(latest.loc["005930"])

    def test_missing_value_on_the_newest_row_is_nan_not_the_older_value(self, store):
        # Sparse panels are normal: KRX publishes no PER for a loss-making
        # quarter. Returning last quarter's number as if it were current would
        # be a silent substitution of stale data.
        panel = PanelStore(store.processed_root, "flows", "KR")
        panel.append(
            attach_metadata(
                panel_frame([("2024-01-05", "005930", float("nan"))]),
                source="test",
                available_at=pd.Timestamp("2024-01-08"),
                data_version="1",
            )
        )
        latest = store.panel_latest("flows", date(2024, 1, 8), ["005930"], "value")
        assert np.isnan(latest.loc["005930"])
