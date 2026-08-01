"""In-sample parameter sensitivity for a strategy.

`evaluation.py` deliberately refuses to search for parameters: "a search loop
bolted onto a measurement tool is automated overfitting." That refusal stands.
This is the other half of the discipline — the question it answers is not
"which value wins?" but **"does this parameter matter, and is the answer
stable?"**

Three things keep it from becoming a winner-picker:

1. **In-sample only, enforced.** A sweep whose window reaches past
   `[periods] in_sample_end` raises. Validation and out-of-sample exist to
   judge a choice that was already made; a parameter tuned on them is a
   parameter with no honest test left.
2. **The whole surface is reported, not the argmax.** Each parameter gets a
   marginal table — the median result across every setting of the *other*
   parameters — because a value that only wins in one corner of the grid is
   noise wearing a result's clothes.
3. **The grid size is printed next to the best cell.** Trying 60 combinations
   and keeping the best of them is 60 chances to be lucky, and the reader is
   told so in those words.

One backtest per grid point, plus one for the benchmark. The cost-doubling
check and walk-forward windows are `research evaluate`'s job: run this to
choose, run that to judge, and never read this module's output as a verdict.
"""

from __future__ import annotations

import copy
import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from itertools import product
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tradingbot.report.metrics import annual_turnover, calculate_metrics
from tradingbot.services import run_backtest
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)


class OutOfSampleError(RuntimeError):
    """Raised when a sweep window would reach past the in-sample period."""


def guard_in_sample(start: str, end: str | None, periods: Mapping[str, Any]) -> None:
    """Refuse to sweep anything the out-of-sample rule protects.

    `end=None` means "to the latest bar", which necessarily includes
    out-of-sample data, so it is rejected as firmly as an explicit date.
    """
    in_sample_end = periods.get("in_sample_end")
    if not in_sample_end:
        raise OutOfSampleError(
            "research config has no [periods] in_sample_end — a sweep cannot "
            "prove it stayed in-sample without one"
        )
    limit = date.fromisoformat(str(in_sample_end))
    if end is None:
        raise OutOfSampleError(
            f"--end is required for a sweep and must not pass {limit.isoformat()} "
            "(in_sample_end). Leaving it open would tune on out-of-sample data."
        )
    if date.fromisoformat(end) > limit:
        raise OutOfSampleError(
            f"sweep window ends {end}, past in_sample_end {limit.isoformat()}. "
            "Choose parameters in-sample, then judge the choice with "
            "`research evaluate` on the later periods."
        )
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise OutOfSampleError(f"start {start} is after end {end}")


def parameter_grid(grid: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    """Every combination of the given parameter values, in declaration order."""
    if not grid:
        raise ValueError("sweep needs at least one parameter")
    empty = sorted(name for name, values in grid.items() if not values)
    if empty:
        raise ValueError(f"parameters with no values to try: {empty}")
    names = list(grid)
    return [dict(zip(names, combo)) for combo in product(*(grid[name] for name in names))]


@dataclass(frozen=True)
class SweepPoint:
    """One grid cell. `error` non-empty means the backtest failed and every
    metric is NaN — recorded, never dropped, so a grid cannot be quietly
    thinned down to the configurations that happened to run."""

    params: dict[str, Any]
    excess_cagr_pct: float
    cagr_pct: float
    sharpe: float
    mdd_pct: float
    annual_turnover: float
    rejected_orders: int
    error: str = ""

    @property
    def measured(self) -> bool:
        return not self.error and not math.isnan(self.excess_cagr_pct)


def _measure(result: Any) -> dict[str, float]:
    metrics, _, _ = calculate_metrics(result)
    return {
        "cagr_pct": float(metrics.cagr_pct),
        "sharpe": float(metrics.sharpe),
        "mdd_pct": abs(float(metrics.max_drawdown_pct)),
        "annual_turnover": float(annual_turnover(result)),
        "rejected_orders": len(result.rejected_orders),
    }


def _with_params(
    config: Mapping[str, Any], strategy_name: str, params: Mapping[str, Any]
) -> dict[str, Any]:
    """A copy of `config` with these strategy parameters overridden.

    Never mutates the input — every grid point must start from the same
    baseline, or point N would inherit point N-1's overrides.
    """
    patched = copy.deepcopy(dict(config))
    strategies = patched.setdefault("strategies", {})
    section = dict(strategies.get(strategy_name, {}))
    section.update(params)
    strategies[strategy_name] = section
    return patched


def run_sweep(
    *,
    config: Mapping[str, Any],
    benchmark_config: Mapping[str, Any],
    market: str,
    symbols: Sequence[str],
    strategy_name: str,
    grid: Mapping[str, Sequence[Any]],
    start: str,
    end: str,
    data_root: str | Path | None = None,
    runner: Callable[..., Any] = run_backtest,
) -> list[SweepPoint]:
    """Backtest every grid combination against one shared benchmark run.

    The benchmark does not depend on the strategy's parameters, so it runs
    once — the alternative would multiply the wall clock by two for an
    identical number every time.
    """
    combinations = parameter_grid(grid)

    def backtest(active: Mapping[str, Any]) -> Any:
        return runner(
            dict(active),
            market=market,
            symbols=list(symbols),
            strategy_name=strategy_name,
            start=start,
            end=end,
            data_root=data_root,
        )

    benchmark = _measure(backtest(benchmark_config))

    points: list[SweepPoint] = []
    for index, params in enumerate(combinations, start=1):
        LOGGER.info("sweep %s/%s: %s", index, len(combinations), params)
        try:
            measured = _measure(backtest(_with_params(config, strategy_name, params)))
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            LOGGER.exception("sweep point %s failed", params)
            nan = float("nan")
            points.append(
                SweepPoint(
                    params=dict(params),
                    excess_cagr_pct=nan,
                    cagr_pct=nan,
                    sharpe=nan,
                    mdd_pct=nan,
                    annual_turnover=nan,
                    rejected_orders=0,
                    error=str(exc),
                )
            )
            continue
        points.append(
            SweepPoint(
                params=dict(params),
                excess_cagr_pct=measured["cagr_pct"] - benchmark["cagr_pct"],
                cagr_pct=measured["cagr_pct"],
                sharpe=measured["sharpe"],
                mdd_pct=measured["mdd_pct"],
                annual_turnover=measured["annual_turnover"],
                rejected_orders=int(measured["rejected_orders"]),
            )
        )
    return points


@dataclass(frozen=True)
class MarginalValue:
    value: Any
    median_excess_pct: float
    worst_excess_pct: float
    best_excess_pct: float
    n: int


@dataclass(frozen=True)
class Marginal:
    """How one parameter behaves across every setting of the others."""

    parameter: str
    values: list[MarginalValue] = field(default_factory=list)

    @property
    def spread_pct(self) -> float:
        """Median-to-median gap between this parameter's best and worst value.

        This is the sensitivity number: near zero means the parameter does not
        matter and any value will do; large means the choice is real — and
        therefore worth defending out-of-sample."""
        medians = [v.median_excess_pct for v in self.values if not math.isnan(v.median_excess_pct)]
        if len(medians) < 2:
            return float("nan")
        return max(medians) - min(medians)


def marginals(points: Sequence[SweepPoint], grid: Mapping[str, Sequence[Any]]) -> list[Marginal]:
    """Per-parameter medians across the rest of the grid.

    The median, not the max: a value that wins only when every other
    parameter is set just so has not been shown to work, it has been shown to
    coincide. Unmeasured points are excluded from the statistics but still
    counted in the report's failure line.
    """
    result: list[Marginal] = []
    for parameter, candidates in grid.items():
        values: list[MarginalValue] = []
        for candidate in candidates:
            excesses = [
                point.excess_cagr_pct
                for point in points
                if point.measured and point.params.get(parameter) == candidate
            ]
            if excesses:
                values.append(
                    MarginalValue(
                        value=candidate,
                        median_excess_pct=float(statistics.median(excesses)),
                        worst_excess_pct=min(excesses),
                        best_excess_pct=max(excesses),
                        n=len(excesses),
                    )
                )
            else:
                nan = float("nan")
                values.append(MarginalValue(candidate, nan, nan, nan, 0))
        result.append(Marginal(parameter=parameter, values=values))
    return result


def sweep_strategy(
    *,
    config: Mapping[str, Any],
    benchmark_config: Mapping[str, Any],
    research: Mapping[str, Any],
    market: str,
    symbols: Sequence[str],
    strategy_name: str,
    grid: Mapping[str, Sequence[Any]],
    start: str,
    end: str,
    data_root: str | Path | None = None,
    config_path: str | None = None,
    benchmark_config_path: str | None = None,
    runner: Callable[..., Any] = run_backtest,
) -> dict[str, Any]:
    """Run the grid in-sample and assemble the sensitivity report."""
    guard_in_sample(start, end, research.get("periods", {}))

    points = run_sweep(
        config=config,
        benchmark_config=benchmark_config,
        market=market,
        symbols=symbols,
        strategy_name=strategy_name,
        grid=grid,
        start=start,
        end=end,
        data_root=data_root,
        runner=runner,
    )
    measured = [point for point in points if point.measured]
    excesses = sorted(point.excess_cagr_pct for point in measured)

    return {
        "strategy_name": strategy_name,
        "market": market.upper(),
        "symbols": list(symbols),
        "period": {"start": start, "end": end},
        "config_path": config_path,
        "benchmark_config_path": benchmark_config_path,
        "data_root": str(data_root) if data_root else None,
        "grid": {name: list(values) for name, values in grid.items()},
        "combinations": len(points),
        "measured": len(measured),
        "failed": len(points) - len(measured),
        "points": [
            {
                "params": point.params,
                "excess_cagr_pct": point.excess_cagr_pct,
                "cagr_pct": point.cagr_pct,
                "sharpe": point.sharpe,
                "mdd_pct": point.mdd_pct,
                "annual_turnover": point.annual_turnover,
                "rejected_orders": point.rejected_orders,
                "error": point.error,
            }
            for point in points
        ],
        "summary": {
            "best_excess_pct": excesses[-1] if excesses else float("nan"),
            "median_excess_pct": float(statistics.median(excesses)) if excesses else float("nan"),
            "worst_excess_pct": excesses[0] if excesses else float("nan"),
        },
        "marginals": [
            {
                "parameter": marginal.parameter,
                "spread_pct": marginal.spread_pct,
                "values": [
                    {
                        "value": value.value,
                        "median_excess_pct": value.median_excess_pct,
                        "worst_excess_pct": value.worst_excess_pct,
                        "best_excess_pct": value.best_excess_pct,
                        "n": value.n,
                    }
                    for value in marginal.values
                ],
            }
            for marginal in marginals(points, grid)
        ],
    }


def _fmt(value: float, spec: str = ".2f") -> str:
    if isinstance(value, float) and math.isnan(value):
        return "측정 불가"
    return format(value, spec)


def render_markdown(report: dict[str, Any]) -> str:
    period = report["period"]
    summary = report["summary"]
    combinations = report["combinations"]
    lines = [
        f"# {report['strategy_name']} 파라미터 민감도 ({report['market']})",
        "",
        "## 이 표를 읽는 법",
        "",
        f"조합 {combinations}개를 In-sample 구간 {period['start']} ~ {period['end']}에서만",
        "돌렸습니다. **가장 좋은 칸을 고르는 표가 아닙니다.** 조합을 "
        f"{combinations}개 시도하고 그중 최고를 고르는 것은 운이 좋을 기회를 "
        f"{combinations}번 갖는 것과 같습니다.",
        "",
        "읽어야 할 것은 아래 **파라미터별 한계표**입니다 — 다른 파라미터를 어떻게",
        "두든 그 값이 꾸준히 좋은지를 봅니다. 한 구석에서만 이기는 값은 결과가",
        "아니라 잡음입니다.",
        "",
        f"- 종목: {', '.join(report['symbols'])}",
        f"- 측정된 조합: {report['measured']} / {combinations}"
        + (f" (실패 {report['failed']})" if report["failed"] else ""),
        f"- 초과 CAGR 최고 {_fmt(summary['best_excess_pct'])}%p / "
        f"중앙값 {_fmt(summary['median_excess_pct'])}%p / "
        f"최저 {_fmt(summary['worst_excess_pct'])}%p",
        "",
    ]

    best = summary["best_excess_pct"]
    median = summary["median_excess_pct"]
    if not math.isnan(best) and not math.isnan(median):
        gap = best - median
        lines += [
            f"최고와 중앙값의 차이는 {_fmt(gap)}%p입니다. 이 차이가 클수록 "
            "'최고 조합'의 상당 부분이",
            "특정 구간에 맞춰진 것일 가능성이 큽니다 — 표면이 평평할수록 선택이 "
            "튼튼합니다.",
            "",
        ]

    lines += ["## 파라미터별 한계표 (다른 파라미터 전체에 대한 중앙값)", ""]
    for marginal in report["marginals"]:
        lines += [
            f"### {marginal['parameter']} — 값 사이 편차 {_fmt(marginal['spread_pct'])}%p",
            "",
            "| 값 | 초과 CAGR 중앙값 | 최저 | 최고 | 조합수 |",
            "|---|---|---|---|---|",
        ]
        for value in marginal["values"]:
            lines.append(
                f"| {value['value']} | {_fmt(value['median_excess_pct'])}%p | "
                f"{_fmt(value['worst_excess_pct'])}%p | "
                f"{_fmt(value['best_excess_pct'])}%p | {value['n']} |"
            )
        lines.append("")

    lines += [
        "## 전체 조합",
        "",
        "| 파라미터 | 초과 CAGR | CAGR | Sharpe | MDD | 연 회전율 | 거부 |",
        "|---|---|---|---|---|---|---|",
    ]
    ordered = sorted(
        report["points"],
        key=lambda point: (
            -point["excess_cagr_pct"]
            if not math.isnan(point["excess_cagr_pct"])
            else float("inf")
        ),
    )
    for point in ordered:
        params = ", ".join(f"{name}={value}" for name, value in point["params"].items())
        if point["error"]:
            lines.append(f"| {params} | 측정 실패 ({point['error']}) | — | — | — | — | — |")
            continue
        lines.append(
            f"| {params} | {_fmt(point['excess_cagr_pct'])}%p | {_fmt(point['cagr_pct'])}% | "
            f"{_fmt(point['sharpe'])} | {_fmt(point['mdd_pct'])}% | "
            f"{_fmt(point['annual_turnover'])} | {point['rejected_orders']} |"
        )

    lines += [
        "",
        "## 다음 단계",
        "",
        "1. 한계표에서 **꾸준히** 좋은 값을 고릅니다 (최고 칸이 아니라).",
        "2. 그 값을 설정에 반영합니다.",
        "3. `research evaluate`를 Validation 구간에서 돌려 선택을 검증합니다.",
        "4. 마지막에 한 번만 Out-of-sample 구간을 엽니다. 여기서 실패하면 "
        "파라미터를 다시 고르는 것이 아니라 가설을 버립니다.",
        "",
    ]
    return "\n".join(lines)
