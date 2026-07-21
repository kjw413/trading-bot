from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from tradingbot.engine.calendar import get_calendar

PASS = "pass"
WARN = "warn"
FAIL = "fail"

_SEVERITY_ORDER = {PASS: 0, WARN: 1, FAIL: 2}

PANEL_REQUIRED_COLUMNS = ["date", "symbol", "available_at"]


@dataclass(frozen=True)
class QualityIssue:
    check: str
    severity: str
    message: str
    count: int = 0


@dataclass(frozen=True)
class QualityReport:
    dataset: str
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def severity(self) -> str:
        if not self.issues:
            return PASS
        return max((issue.severity for issue in self.issues), key=lambda s: _SEVERITY_ORDER[s])

    @property
    def ok(self) -> bool:
        return self.severity != FAIL

    def summary(self) -> str:
        if not self.issues:
            return f"{self.dataset}: pass"
        details = "; ".join(f"{i.check}({i.count})" for i in self.issues)
        return f"{self.dataset}: {self.severity} — {details}"


def check_ohlcv(
    frame: pd.DataFrame, *, dataset: str, market: str, max_jump: float = 0.5
) -> QualityReport:
    """Validate a daily OHLCV frame indexed by date."""
    issues: list[QualityIssue] = []
    if frame.empty:
        return QualityReport(dataset, [QualityIssue("empty", FAIL, "No rows", 0)])

    duplicates = int(frame.index.duplicated().sum())
    if duplicates:
        issues.append(QualityIssue("duplicate_date", FAIL, "Duplicate dates", duplicates))

    illogical = (
        (frame["high"] < frame["low"])
        | (frame["close"] > frame["high"])
        | (frame["close"] < frame["low"])
        | (frame["open"] > frame["high"])
        | (frame["open"] < frame["low"])
    )
    count = int(illogical.sum())
    if count:
        issues.append(QualityIssue("ohlc_logic", FAIL, "OHLC bounds violated", count))

    negative = int((frame["volume"] < 0).sum())
    if negative:
        issues.append(QualityIssue("negative_volume", FAIL, "Negative volume", negative))

    non_positive = int((frame["close"] <= 0).sum())
    if non_positive:
        issues.append(QualityIssue("non_positive_price", FAIL, "Non-positive close", non_positive))

    returns = frame["close"].pct_change().abs()
    jumps = int((returns > max_jump).sum())
    if jumps:
        issues.append(
            QualityIssue("price_jump", WARN, f"Daily move over {max_jump:.0%}", jumps)
        )

    expected = get_calendar(market).trading_days(
        frame.index.min().date(), frame.index.max().date()
    )
    present = {timestamp.date() for timestamp in frame.index}
    missing = [day for day in expected if day not in present]
    if missing:
        issues.append(
            QualityIssue("missing_trading_day", WARN, "Trading days absent", len(missing))
        )

    return QualityReport(dataset, issues)


def check_panel(frame: pd.DataFrame, *, dataset: str) -> QualityReport:
    """Validate a point-in-time panel frame."""
    if frame.empty:
        return QualityReport(dataset, [QualityIssue("empty", WARN, "No rows", 0)])

    missing_columns = [c for c in PANEL_REQUIRED_COLUMNS if c not in frame.columns]
    if missing_columns:
        return QualityReport(
            dataset,
            [
                QualityIssue(
                    "missing_column", FAIL, f"Missing columns: {missing_columns}", len(missing_columns)
                )
            ],
        )

    issues: list[QualityIssue] = []
    duplicates = int(frame.duplicated(subset=["date", "symbol"]).sum())
    if duplicates:
        issues.append(QualityIssue("duplicate_key", FAIL, "Duplicate (date, symbol)", duplicates))

    early = int((frame["available_at"] < frame["date"]).sum())
    if early:
        issues.append(
            QualityIssue(
                "availability_precedes_date",
                FAIL,
                "available_at earlier than the observation date",
                early,
            )
        )

    return QualityReport(dataset, issues)
