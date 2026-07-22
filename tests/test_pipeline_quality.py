from __future__ import annotations

import pandas as pd
import pytest

from tradingbot.data.panel import PanelStore, attach_metadata
from tradingbot.data.pipeline import run_pipeline


@pytest.fixture
def config(tmp_path):
    return {
        "pipeline": {
            "processed_dir": str(tmp_path / "processed"),
            "log_dir": str(tmp_path / "log"),
            "symbols": ["005930"],
            "retry_attempts": 1,
        },
        "data": {"cache_dir": str(tmp_path / "cache")},
    }


def write_bad_panel(root, dataset="flows"):
    # PanelStore.append always re-dedupes the combined frame with
    # keep="last" before writing (both within one call and across repeated
    # calls with differing values), so neither reproduces an on-disk
    # duplicate. Writing the parquet partition directly bypasses that
    # safety net to simulate the "corrupt file" scenario append() itself
    # already anticipates (see the comment on PanelStore.append about
    # duplicate keys in a corrupt file).
    frame = pd.DataFrame(
        [
            {"date": pd.Timestamp("2024-01-02"), "symbol": "005930", "value": 1.0},
            {"date": pd.Timestamp("2024-01-02"), "symbol": "005930", "value": 2.0},
        ]
    )
    tagged = attach_metadata(frame, source="t", available_at="2024-01-03", data_version="1")
    store = PanelStore(root, dataset, "KR")
    path = store.path(2024)
    path.parent.mkdir(parents=True, exist_ok=True)
    tagged.to_parquet(path)


def test_clean_panel_reports_no_quality_message(config, tmp_path):
    result = run_pipeline(
        config, market="KR", symbols=["005930"], collectors={"flows": lambda **k: 1}
    )
    assert result.results[0].message == ""


def test_duplicate_keys_surface_in_the_result_message(config, tmp_path):
    def collector(**_):
        write_bad_panel(tmp_path / "processed")
        return 2

    result = run_pipeline(config, market="KR", symbols=["005930"], collectors={"flows": collector})
    source = result.results[0]
    # Collection "succeeded" but the data is unusable — the operator must see it.
    assert source.status == "ok"
    assert "quality=fail" in source.message
    assert "duplicate_key" in source.message


def write_bad_price_cache(cache_root, market="KR", symbol="005930"):
    """Write an OHLCV file with a FAIL-severity bounds violation directly to
    the cache, bypassing any network fetch. This is network-free and mirrors
    the real scenario the reviewer found: a cache file already on disk with
    bad rows, regardless of whether this run's collector re-fetches it."""
    from tradingbot.data.cache import ParquetCache

    index = pd.bdate_range("2024-01-02", periods=2)
    frame = pd.DataFrame(
        {
            "open": [10.0, 10.0],
            "high": [11.0, 9.0],  # second row: high < low -> ohlc_logic FAIL
            "low": [9.0, 11.0],
            "close": [10.0, 10.0],
            "volume": [100.0, 100.0],
        },
        index=index,
    )
    ParquetCache(cache_root).write(market, symbol, frame)


def test_clean_price_cache_reports_no_quality_message(config, tmp_path):
    result = run_pipeline(
        config, market="KR", symbols=["005930"], collectors={"prices": lambda **_: 1}
    )
    assert result.results[0].message == ""


def test_price_ohlc_violation_surfaces_in_the_result_message(config, tmp_path):
    write_bad_price_cache(tmp_path / "cache")

    # A fake collector that never touches the cache: the quality problem must
    # surface even when this run's "collection" did nothing but confirm the
    # cache is present, since a symbol whose fetch is skipped or fails still
    # keeps whatever bad data was already cached.
    result = run_pipeline(
        config, market="KR", symbols=["005930"], collectors={"prices": lambda **_: 0}
    )
    source = result.results[0]
    assert source.status == "ok"
    assert "quality=fail" in source.message
    assert "005930" in source.message
    assert "ohlc_logic" in source.message
