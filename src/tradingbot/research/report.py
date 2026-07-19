from __future__ import annotations

from datetime import date
from typing import Any, Sequence

from tradingbot.data.store import ResearchDataStore
from tradingbot.factors.base import Factor
from tradingbot.research.gate import GateThresholds, evaluate_gate
from tradingbot.research.ic import ic_series, summarize_ic
from tradingbot.research.quantiles import monotonicity, quantile_returns, top_quantile_turnover
from tradingbot.research.walk_forward import WalkForwardWindow, walk_forward_ic, window_win_rate


def build_factor_report(
    *,
    store: ResearchDataStore,
    market: str,
    universe: Sequence[str],
    factors: Sequence[Factor],
    dates: Sequence[date],
    windows: Sequence[WalkForwardWindow],
    thresholds: GateThresholds,
) -> dict[str, Any]:
    """IC / quantile / walk-forward / gate summary for each factor."""
    report: dict[str, Any] = {
        "market": market,
        "universe": list(universe),
        "n_dates": len(dates),
        "horizon_days": thresholds.horizon_days,
        "n_quantiles": thresholds.n_quantiles,
        "factors": {},
    }
    for factor in factors:
        ic_summary = summarize_ic(
            ic_series(factor, store, universe, dates, thresholds.horizon_days)
        )
        quantile_frame = quantile_returns(
            factor, store, universe, dates, thresholds.horizon_days, thresholds.n_quantiles
        )
        if quantile_frame.empty:
            quantile_means: list[float] = []
            spread_mean = float("nan")
        else:
            quantile_means = [
                float(quantile_frame[f"q{q}"].mean())
                for q in range(1, thresholds.n_quantiles + 1)
            ]
            spread_mean = float(quantile_frame["spread"].mean())
        mono = monotonicity(quantile_means)
        turnover = top_quantile_turnover(factor, store, universe, dates, thresholds.n_quantiles)
        wf = walk_forward_ic(
            factor,
            store,
            universe,
            market=market,
            horizon_days=thresholds.horizon_days,
            windows=windows,
        )
        gate = evaluate_gate(ic_summary, mono, thresholds)
        report["factors"][factor.name] = {
            "ic": {
                "mean": ic_summary.mean,
                "std": ic_summary.std,
                "ir": ic_summary.ir,
                "positive_share": ic_summary.positive_share,
                "n_periods": ic_summary.n_periods,
            },
            "quantile_means": quantile_means,
            "spread_mean": spread_mean,
            "monotonicity": mono,
            "turnover_mean": float(turnover.mean()) if len(turnover) else float("nan"),
            "walk_forward": {
                "windows": wf.to_dict(orient="records"),
                "win_rate": window_win_rate(wf),
            },
            "gate": {"passed": gate.passed, "reasons": gate.reasons},
        }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Factor Research Report",
        "",
        f"- Market: {report['market']}",
        f"- Universe: {', '.join(report['universe'])}",
        f"- Evaluation dates: {report['n_dates']} (month-end)",
        f"- Horizon: {report['horizon_days']} trading days, quantiles: {report['n_quantiles']}",
        "",
        "| factor | IC mean | IC IR | IC>0 | mono | spread | turnover | WF win | gate |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, data in report["factors"].items():
        ic = data["ic"]
        gate = "PASS" if data["gate"]["passed"] else "FAIL: " + "; ".join(data["gate"]["reasons"])
        lines.append(
            f"| {name} | {ic['mean']:.4f} | {ic['ir']:.2f} | {ic['positive_share']:.0%} "
            f"| {data['monotonicity']:.2f} | {data['spread_mean']:.4f} "
            f"| {data['turnover_mean']:.2f} | {data['walk_forward']['win_rate']:.0%} | {gate} |"
        )
    lines.append("")
    return "\n".join(lines)
