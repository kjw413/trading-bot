"""Reproducible information-ratio ceiling reports for factor IC series."""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import date
from typing import Any, Callable, Sequence

import pandas as pd

from tradingbot.data.store import ResearchDataStore
from tradingbot.factors.base import Factor
from tradingbot.research.horizon import HorizonDiagnostics, diagnose
from tradingbot.research.ic import spearman_ic
from tradingbot.research.labels import forward_returns
from tradingbot.research.report import MembershipAwareFactor


def _factor_observations(
    factor: Factor,
    store: ResearchDataStore,
    universe: Sequence[str],
    dates: Sequence[date],
    horizon_days: int,
) -> tuple[list[float], list[int]]:
    """Return each date's IC and the paired cross-section used to compute it."""
    ics: list[float] = []
    cross_section_sizes: list[int] = []
    for dt in dates:
        factor_values = factor.compute(dt, universe, store)
        forward = forward_returns(store, universe, dt, horizon_days)
        paired = pd.concat([factor_values, forward], axis=1, join="inner").dropna()
        ics.append(spearman_ic(factor_values, forward))
        cross_section_sizes.append(len(paired))
    return ics, cross_section_sizes


def _diagnostics_dict(diagnostics: HorizonDiagnostics) -> dict[str, Any]:
    return asdict(diagnostics)


def build_ceiling_report(
    *,
    store: ResearchDataStore,
    market: str,
    universe: Sequence[str],
    factors: Sequence[Factor],
    dates: Sequence[date],
    horizon_days: int,
    members_on: Callable[[date], Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Information-ratio ceiling diagnostics for each requested factor."""
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")

    ordered_dates = sorted(dates)
    midpoint = (len(ordered_dates) + 1) // 2
    first_half_dates = ordered_dates[:midpoint]
    second_half_dates = ordered_dates[midpoint:]
    first_half_size = len(first_half_dates)
    second_half_size = len(second_half_dates)
    nw_lag = max(0, math.ceil(horizon_days / 21) - 1)

    report: dict[str, Any] = {
        "market": market,
        "universe": list(universe),
        "n_dates": len(ordered_dates),
        "horizon_days": horizon_days,
        "nw_lag": nw_lag,
        "factors": {},
    }
    measured_factors = (
        [MembershipAwareFactor(factor, members_on) for factor in factors]
        if members_on is not None
        else factors
    )
    for factor in measured_factors:
        ics, cross_section_sizes = _factor_observations(
            factor, store, universe, ordered_dates, horizon_days
        )
        report["factors"][factor.name] = {
            "overall": _diagnostics_dict(diagnose(ics, cross_section_sizes, nw_lag)),
            "first_half": _diagnostics_dict(
                diagnose(
                    ics[:first_half_size],
                    cross_section_sizes[:first_half_size],
                    nw_lag,
                )
            ),
            "second_half": _diagnostics_dict(
                diagnose(
                    ics[first_half_size : first_half_size + second_half_size],
                    cross_section_sizes[
                        first_half_size : first_half_size + second_half_size
                    ],
                    nw_lag,
                )
            ),
        }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Factor Ceiling Report",
        "",
        f"- Market: {report['market']}",
    ]
    if report.get("universe_layer"):
        lines.append(f"- Universe layer: {report['universe_layer']}")
    lines += [
        f"- Universe: {', '.join(report['universe'])}",
        f"- Evaluation dates: {report['n_dates']} (month-end)",
        f"- Horizon: {report['horizon_days']} trading days",
        f"- Newey-West lag: {report['nw_lag']}",
        "",
        "| factor | periods | IC mean | IC IR | ceiling | IR 95% CI | first-half ceiling | second-half ceiling |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, data in report["factors"].items():
        overall = data["overall"]
        first_half = data["first_half"]
        second_half = data["second_half"]
        ci_low, ci_high = overall["ci95"]
        lines.append(
            f"| {name} | {overall['n_periods']} | {overall['mean_ic']:.4f} "
            f"| {overall['ir']:.2f} | {overall['ceiling']:.3f} "
            f"| [{ci_low:.2f}, {ci_high:.2f}] | {first_half['ceiling']:.3f} "
            f"| {second_half['ceiling']:.3f} |"
        )
    lines.append("")
    return "\n".join(lines)
