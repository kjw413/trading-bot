"""A/B study for institutional buying conditional on retail participation.

This module intentionally produces research observations, not trading signals.
It answers the narrower falsifiable question: after persistent institutional
net buying, do low-retail-participation observations outperform otherwise
identical high-participation observations?
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ParticipationStudy:
    """Event-level returns and their grouped A/B summary."""

    observations: pd.DataFrame
    summary: pd.DataFrame


def _require_columns(frame: pd.DataFrame, columns: set[str], name: str) -> None:
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"{name} is missing required column(s): {missing}")


def analyze_participation_hypothesis(
    flows: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    horizons: Sequence[int] = (5, 10, 20),
    institution_window: int = 5,
    baseline_window: int = 252,
    min_baseline: int = 60,
    round_trip_cost_bps: float = 30.0,
    event_spacing: int | None = None,
) -> ParticipationStudy:
    """Compare forward returns for low versus high retail participation.

    A candidate requires positive institutional net buying over the complete
    ``institution_window``. Retail participation is ``(buy + sell) /
    (2 * traded_value)`` and is split around the symbol's *prior* rolling
    median, preventing the current observation from setting its own threshold.
    Entry is the next available session's open and exits use later closes.

    Events for one symbol are spaced by ``event_spacing`` price sessions
    (default: the largest horizon) so a long streak is not counted as many
    independent observations. Returns are net of the supplied round-trip cost.
    """
    _require_columns(
        flows,
        {
            "date",
            "symbol",
            "institution_net",
            "individual_buy",
            "individual_sell",
            "traded_value",
        },
        "flows",
    )
    _require_columns(prices, {"date", "symbol", "open", "close"}, "prices")
    horizons = tuple(sorted(set(int(value) for value in horizons)))
    if not horizons or any(value <= 0 for value in horizons):
        raise ValueError("horizons must contain positive integers")
    if institution_window <= 0 or baseline_window <= 0 or min_baseline <= 0:
        raise ValueError("window lengths must be positive")
    if min_baseline > baseline_window:
        raise ValueError("min_baseline cannot exceed baseline_window")
    spacing = max(horizons) if event_spacing is None else int(event_spacing)
    if spacing < 0:
        raise ValueError("event_spacing cannot be negative")
    if round_trip_cost_bps < 0:
        raise ValueError("round_trip_cost_bps cannot be negative")

    flow_data = flows.copy()
    price_data = prices.copy()
    for frame in (flow_data, price_data):
        frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None).dt.normalize()
        frame["symbol"] = frame["symbol"].astype(str).str.upper()
    flow_data = flow_data.sort_values(["symbol", "date"])
    price_data = price_data.sort_values(["symbol", "date"])

    denominator = 2.0 * pd.to_numeric(flow_data["traded_value"], errors="coerce")
    gross_retail = pd.to_numeric(flow_data["individual_buy"], errors="coerce") + pd.to_numeric(
        flow_data["individual_sell"], errors="coerce"
    )
    flow_data["retail_participation"] = gross_retail.div(denominator.where(denominator > 0))
    grouped = flow_data.groupby("symbol", sort=False)
    flow_data["institution_window_net"] = grouped["institution_net"].transform(
        lambda values: pd.to_numeric(values, errors="coerce").rolling(
            institution_window, min_periods=institution_window
        ).sum()
    )
    flow_data["participation_baseline"] = grouped["retail_participation"].transform(
        lambda values: values.shift(1).rolling(baseline_window, min_periods=min_baseline).median()
    )
    candidates = flow_data[
        (flow_data["institution_window_net"] > 0)
        & flow_data["retail_participation"].notna()
        & flow_data["participation_baseline"].notna()
    ].copy()
    candidates["group"] = np.where(
        candidates["retail_participation"] <= candidates["participation_baseline"], "low", "high"
    )

    rows: list[dict[str, object]] = []
    cost = round_trip_cost_bps / 10_000.0
    for symbol, symbol_candidates in candidates.groupby("symbol", sort=False):
        history = price_data[price_data["symbol"] == symbol].reset_index(drop=True)
        if history.empty:
            continue
        dates = history["date"].to_numpy(dtype="datetime64[ns]")
        last_entry_position = -spacing - 1
        for candidate in symbol_candidates.itertuples(index=False):
            entry_position = int(np.searchsorted(dates, np.datetime64(candidate.date), side="right"))
            if entry_position >= len(history) or entry_position - last_entry_position <= spacing:
                continue
            entry_price = float(history.at[entry_position, "open"])
            if not np.isfinite(entry_price) or entry_price <= 0:
                continue
            available_horizons = [horizon for horizon in horizons if entry_position + horizon < len(history)]
            if not available_horizons:
                continue
            last_entry_position = entry_position
            for horizon in available_horizons:
                exit_position = entry_position + horizon
                exit_price = float(history.at[exit_position, "close"])
                if not np.isfinite(exit_price) or exit_price <= 0:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "signal_date": candidate.date,
                        "entry_date": history.at[entry_position, "date"],
                        "exit_date": history.at[exit_position, "date"],
                        "group": candidate.group,
                        "horizon": horizon,
                        "institution_window_net": candidate.institution_window_net,
                        "retail_participation": candidate.retail_participation,
                        "participation_baseline": candidate.participation_baseline,
                        "net_return": exit_price / entry_price - 1.0 - cost,
                    }
                )

    observation_columns = [
        "symbol", "signal_date", "entry_date", "exit_date", "group", "horizon",
        "institution_window_net", "retail_participation", "participation_baseline", "net_return",
    ]
    observations = pd.DataFrame(rows, columns=observation_columns)
    summary_columns = ["group", "horizon", "count", "mean_return", "median_return", "win_rate"]
    if observations.empty:
        return ParticipationStudy(observations, pd.DataFrame(columns=summary_columns))
    summary = (
        observations.groupby(["group", "horizon"], observed=True)["net_return"]
        .agg(
            count="size",
            mean_return="mean",
            median_return="median",
            win_rate=lambda x: (x > 0).mean(),
        )
        .reset_index()
    )
    return ParticipationStudy(observations, summary[summary_columns])
