from __future__ import annotations

import pandas as pd
import pytest

from tradingbot.data.quality import FAIL, PASS, WARN, check_ohlcv, check_panel


def ohlcv(rows: list[tuple[str, float, float, float, float, float]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        rows, columns=["date", "open", "high", "low", "close", "volume"]
    ).set_index("date")
    frame.index = pd.to_datetime(frame.index)
    return frame


CLEAN = ohlcv(
    [
        ("2024-01-02", 100.0, 105.0, 99.0, 104.0, 1000.0),
        ("2024-01-03", 104.0, 106.0, 103.0, 105.0, 1100.0),
        ("2024-01-04", 105.0, 108.0, 104.0, 107.0, 1200.0),
    ]
)


class TestCheckOhlcv:
    def test_clean_data_passes(self):
        report = check_ohlcv(CLEAN, dataset="prices", market="KR")
        assert report.ok
        assert report.severity == PASS
        assert report.issues == []

    def test_high_below_low_fails(self):
        broken = CLEAN.copy()
        broken.loc[pd.Timestamp("2024-01-03"), "high"] = 1.0
        report = check_ohlcv(broken, dataset="prices", market="KR")
        assert report.severity == FAIL
        assert any(issue.check == "ohlc_logic" for issue in report.issues)

    def test_close_outside_high_low_fails(self):
        broken = CLEAN.copy()
        broken.loc[pd.Timestamp("2024-01-03"), "close"] = 999.0
        report = check_ohlcv(broken, dataset="prices", market="KR")
        assert report.severity == FAIL

    def test_negative_volume_fails(self):
        broken = CLEAN.copy()
        broken.loc[pd.Timestamp("2024-01-03"), "volume"] = -1.0
        report = check_ohlcv(broken, dataset="prices", market="KR")
        assert any(issue.check == "negative_volume" for issue in report.issues)
        assert report.severity == FAIL

    def test_duplicate_dates_fail(self):
        broken = pd.concat([CLEAN, CLEAN.iloc[[0]]])
        report = check_ohlcv(broken, dataset="prices", market="KR")
        assert any(issue.check == "duplicate_date" for issue in report.issues)

    def test_large_price_jump_warns(self):
        jumpy = CLEAN.copy()
        jumpy.loc[pd.Timestamp("2024-01-04"), ["open", "high", "low", "close"]] = [
            500.0,
            510.0,
            499.0,
            505.0,
        ]
        report = check_ohlcv(jumpy, dataset="prices", market="KR")
        assert any(issue.check == "price_jump" for issue in report.issues)
        assert report.severity == WARN

    def test_missing_trading_day_warns(self):
        # 2024-01-03 removed from an otherwise contiguous span.
        gapped = CLEAN.drop(index=pd.Timestamp("2024-01-03"))
        report = check_ohlcv(gapped, dataset="prices", market="KR")
        assert any(issue.check == "missing_trading_day" for issue in report.issues)

    def test_empty_frame_fails_loudly(self):
        report = check_ohlcv(ohlcv([]), dataset="prices", market="KR")
        assert report.severity == FAIL
        assert any(issue.check == "empty" for issue in report.issues)


def panel(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(d),
                "symbol": s,
                "available_at": pd.Timestamp(a),
                "value": 1.0,
            }
            for d, s, a in rows
        ]
    )


class TestCheckPanel:
    def test_clean_panel_passes(self):
        report = check_panel(
            panel([("2024-01-02", "005930", "2024-01-03")]), dataset="flows"
        )
        assert report.ok

    def test_duplicate_key_fails(self):
        report = check_panel(
            panel(
                [("2024-01-02", "005930", "2024-01-03"), ("2024-01-02", "005930", "2024-01-03")]
            ),
            dataset="flows",
        )
        assert any(issue.check == "duplicate_key" for issue in report.issues)
        assert report.severity == FAIL

    def test_availability_before_observation_fails(self):
        # available_at earlier than date means the data claims to be knowable
        # before it existed — a look-ahead leak.
        report = check_panel(
            panel([("2024-01-02", "005930", "2024-01-01")]), dataset="flows"
        )
        assert any(issue.check == "availability_precedes_date" for issue in report.issues)
        assert report.severity == FAIL

    def test_missing_meta_column_fails(self):
        frame = panel([("2024-01-02", "005930", "2024-01-03")]).drop(columns=["available_at"])
        report = check_panel(frame, dataset="flows")
        assert any(issue.check == "missing_column" for issue in report.issues)

    def test_empty_panel_warns_but_does_not_fail(self):
        report = check_panel(pd.DataFrame(), dataset="flows")
        assert report.severity == WARN
