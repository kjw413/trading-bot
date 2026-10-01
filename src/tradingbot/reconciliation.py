"""Realised holding returns against qualified leveraged proxy expectations.

All prices and proxy measurements are supplied by the caller.  The only I/O
here is the versioned cumulative-gap history under the caller's state root.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from pathlib import Path

import numpy as np
import pandas as pd

from tradingbot.proxy import ProxyMeasurement, ProxyStatus

RECONCILIATION_SCHEMA_VERSION = 1
RECONCILIATION_DIRNAME = "reconciliation"
RECONCILIATION_FILENAME = "history.json"

DateLike = str | date | datetime | pd.Timestamp


@dataclass(frozen=True)
class ReconciliationEntry:
    symbol: str
    proxy_symbol: str
    leverage: float
    period_start: date
    period_end: date
    status: ProxyStatus
    realised_return: float
    proxy_return: float | None
    expected_return: float | None
    gap_percentage_points: float | None
    cumulative_gap_percentage_points: float | None


@dataclass(frozen=True)
class ReconciliationResult:
    entries: tuple[ReconciliationEntry, ...]

    def for_symbol(self, symbol: str) -> tuple[ReconciliationEntry, ...]:
        wanted = _normalised_symbol(symbol)
        return tuple(entry for entry in self.entries if entry.symbol == wanted)


def _reconciliation_path(root: str | Path) -> Path:
    return Path(root) / RECONCILIATION_DIRNAME / RECONCILIATION_FILENAME


def _normalised_symbol(symbol: str) -> str:
    normalised = str(symbol).strip().upper()
    if not normalised:
        raise ValueError("symbol must not be empty")
    return normalised


def _normalised_date(value: DateLike, *, name: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid date") from exc
    if pd.isna(timestamp):
        raise ValueError(f"{name} must be a valid date")
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def _finite_number(value: float, *, name: str) -> float:
    measured = float(value)
    if not isfinite(measured):
        raise ValueError(f"{name} must be finite")
    return measured


def _optional_number(value: object, *, name: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, name=name)


def _clean_prices(prices: pd.Series, *, name: str) -> pd.Series:
    if not isinstance(prices, pd.Series):
        raise TypeError(f"{name} must be a pandas Series")
    if not isinstance(prices.index, pd.DatetimeIndex):
        raise TypeError(f"{name} must have a DatetimeIndex")
    if prices.index.has_duplicates:
        raise ValueError(f"{name} index must not contain duplicates")
    try:
        numeric = pd.to_numeric(prices, errors="raise").astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain numeric prices") from exc
    numeric = numeric.replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    index = numeric.index
    if index.tz is not None:
        index = index.tz_localize(None)
    numeric.index = index.normalize()
    if numeric.index.has_duplicates:
        raise ValueError(f"{name} must not contain multiple prices for one date")
    if len(numeric) < 2:
        raise ValueError(f"{name} needs at least two finite prices")
    if bool((numeric <= 0.0).any()):
        raise ValueError(f"{name} must contain only positive prices")
    return numeric


def _period_prices(
    prices: pd.Series,
    *,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
    name: str,
) -> pd.Series:
    cleaned = _clean_prices(prices, name=name)
    selected = cleaned.loc[
        (cleaned.index >= period_start) & (cleaned.index <= period_end)
    ]
    if len(selected) < 2:
        raise ValueError(
            f"{name} needs at least two prices between "
            f"{period_start.date()} and {period_end.date()}"
        )
    return selected


def _return_from_prices(prices: pd.Series) -> float:
    return float(prices.iloc[-1] / prices.iloc[0] - 1.0)


def _entry_payload(entry: ReconciliationEntry) -> dict[str, object]:
    return {
        "symbol": entry.symbol,
        "proxy_symbol": entry.proxy_symbol,
        "leverage": entry.leverage,
        "period_start": entry.period_start.isoformat(),
        "period_end": entry.period_end.isoformat(),
        "status": entry.status.value,
        "realised_return": entry.realised_return,
        "proxy_return": entry.proxy_return,
        "expected_return": entry.expected_return,
        "gap_percentage_points": entry.gap_percentage_points,
        "cumulative_gap_percentage_points": entry.cumulative_gap_percentage_points,
    }


def _validate_entry(entry: ReconciliationEntry) -> ReconciliationEntry:
    if entry.period_end < entry.period_start:
        raise ValueError("period_end must not precede period_start")
    if entry.leverage == 0.0:
        raise ValueError("leverage must not be zero")

    comparison_values = (
        entry.proxy_return,
        entry.expected_return,
        entry.gap_percentage_points,
        entry.cumulative_gap_percentage_points,
    )
    if entry.status is ProxyStatus.QUALIFIED:
        if any(value is None for value in comparison_values):
            raise ValueError("qualified reconciliation is missing comparison values")
    elif any(value is not None for value in comparison_values):
        raise ValueError("unqualified reconciliation must not contain a gap")
    return entry


def _entry_from_payload(row: object) -> ReconciliationEntry:
    if not isinstance(row, dict):
        raise TypeError("reconciliation record must be an object")
    entry = ReconciliationEntry(
        symbol=_normalised_symbol(row["symbol"]),
        proxy_symbol=_normalised_symbol(row["proxy_symbol"]),
        leverage=_finite_number(row["leverage"], name="leverage"),
        period_start=date.fromisoformat(row["period_start"]),
        period_end=date.fromisoformat(row["period_end"]),
        status=ProxyStatus(row["status"]),
        realised_return=_finite_number(
            row["realised_return"], name="realised_return"
        ),
        proxy_return=_optional_number(row["proxy_return"], name="proxy_return"),
        expected_return=_optional_number(
            row["expected_return"], name="expected_return"
        ),
        gap_percentage_points=_optional_number(
            row["gap_percentage_points"], name="gap_percentage_points"
        ),
        cumulative_gap_percentage_points=_optional_number(
            row["cumulative_gap_percentage_points"],
            name="cumulative_gap_percentage_points",
        ),
    )
    return _validate_entry(entry)


def load_reconciliation(root: str | Path) -> ReconciliationResult | None:
    """Load every recorded interval, or ``None`` when tracking has not begun."""
    path = _reconciliation_path(root)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["schema_version"] != RECONCILIATION_SCHEMA_VERSION:
            raise ValueError("unknown reconciliation schema")
        records = payload["records"]
        if not isinstance(records, list):
            raise TypeError("reconciliation records must be a list")
        return ReconciliationResult(
            entries=tuple(_entry_from_payload(row) for row in records)
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Corrupt reconciliation history: {path}") from exc


def _save_reconciliation(
    result: ReconciliationResult, root: str | Path
) -> Path:
    path = _reconciliation_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "records": [_entry_payload(entry) for entry in result.entries],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return path


def _normalised_mapping(values: Mapping[str, object], *, name: str) -> dict[str, object]:
    normalised: dict[str, object] = {}
    for supplied_symbol, value in values.items():
        symbol = _normalised_symbol(supplied_symbol)
        if symbol in normalised:
            raise ValueError(f"{name} contains duplicate symbol {symbol}")
        normalised[symbol] = value
    return normalised


def reconcile_holdings(
    *,
    period_start: DateLike,
    period_end: DateLike,
    holding_prices: Mapping[str, pd.Series],
    proxy_prices: Mapping[str, pd.Series],
    proxy_measurements: Mapping[str, ProxyMeasurement],
    state_root: str | Path,
) -> ReconciliationResult:
    """Measure one interval and append it to cumulative reconciliation history.

    A proxy return is read only for a qualified measurement.  Rejected and
    unmeasurable pairs retain their distinct statuses and all expectation/gap
    fields remain ``None``.
    """
    start = _normalised_date(period_start, name="period_start")
    end = _normalised_date(period_end, name="period_end")
    if end <= start:
        raise ValueError("period_end must be later than period_start")

    prices_by_holding = _normalised_mapping(holding_prices, name="holding_prices")
    prices_by_proxy = _normalised_mapping(proxy_prices, name="proxy_prices")
    measurements = _normalised_mapping(
        proxy_measurements, name="proxy_measurements"
    )

    history = load_reconciliation(state_root)
    stored_entries = () if history is None else history.entries
    cumulative_by_symbol: dict[str, float] = {}
    for stored in stored_entries:
        if stored.cumulative_gap_percentage_points is not None:
            cumulative_by_symbol[stored.symbol] = (
                stored.cumulative_gap_percentage_points
            )

    current_entries: list[ReconciliationEntry] = []
    for symbol, supplied_prices in prices_by_holding.items():
        measurement = measurements.get(symbol)
        if not isinstance(measurement, ProxyMeasurement):
            raise ValueError(f"proxy measurement required for {symbol}")
        if _normalised_symbol(measurement.traded_symbol) != symbol:
            raise ValueError(f"proxy measurement symbol does not match {symbol}")

        multiple = _finite_number(measurement.leverage, name="leverage")
        if multiple == 0.0:
            raise ValueError("leverage must not be zero")
        proxy_symbol = _normalised_symbol(measurement.proxy_symbol)
        status = measurement.status

        if status is ProxyStatus.QUALIFIED:
            supplied_proxy_prices = prices_by_proxy.get(proxy_symbol)
            if not isinstance(supplied_proxy_prices, pd.Series):
                raise ValueError(
                    f"proxy prices required for qualified pair {symbol}/{proxy_symbol}"
                )
            holding_period = _period_prices(
                supplied_prices,
                period_start=start,
                period_end=end,
                name=f"holding_prices[{symbol}]",
            )
            proxy_period = _period_prices(
                supplied_proxy_prices,
                period_start=start,
                period_end=end,
                name=f"proxy_prices[{proxy_symbol}]",
            )
            aligned = pd.concat(
                (holding_period.rename("holding"), proxy_period.rename("proxy")),
                axis=1,
                join="inner",
            ).dropna()
            if len(aligned) < 2:
                raise ValueError(
                    f"qualified pair {symbol}/{proxy_symbol} needs two aligned prices"
                )
            realised_return = _return_from_prices(aligned["holding"])
            proxy_return = _return_from_prices(aligned["proxy"])
            expected_return = multiple * proxy_return
            gap = (realised_return - expected_return) * 100.0
            cumulative_gap = cumulative_by_symbol.get(symbol, 0.0) + gap
            observed_start = aligned.index[0].date()
            observed_end = aligned.index[-1].date()
        else:
            holding_period = _period_prices(
                supplied_prices,
                period_start=start,
                period_end=end,
                name=f"holding_prices[{symbol}]",
            )
            realised_return = _return_from_prices(holding_period)
            proxy_return = None
            expected_return = None
            gap = None
            cumulative_gap = None
            observed_start = holding_period.index[0].date()
            observed_end = holding_period.index[-1].date()

        current_entries.append(
            _validate_entry(
                ReconciliationEntry(
                    symbol=symbol,
                    proxy_symbol=proxy_symbol,
                    leverage=multiple,
                    period_start=observed_start,
                    period_end=observed_end,
                    status=status,
                    realised_return=_finite_number(
                        realised_return, name="realised_return"
                    ),
                    proxy_return=proxy_return,
                    expected_return=expected_return,
                    gap_percentage_points=gap,
                    cumulative_gap_percentage_points=cumulative_gap,
                )
            )
        )

    current = ReconciliationResult(entries=tuple(current_entries))
    _save_reconciliation(
        ReconciliationResult(entries=(*stored_entries, *current.entries)),
        state_root,
    )
    return current
