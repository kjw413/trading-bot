"""Measure a single-instrument sizing rule on design data, then test it once.

Two questions are kept apart because they have different answers and the
second is easy to mistake for the first.

**Did the rule meet the owner's bar on unseen data?** One number, fixed in
advance, read once: total return over the test window.

**Did the sizing do anything?** A long-only rule on a rising instrument meets
an absolute-return bar by being long. So beside the verdict sit the two
things anyone could have done without a rule — hold the instrument, and hold
a fixed fraction of it — and whether each of them met the same bar.

The fixed fraction used on the test window is the rule's average weight over
the *design* window. It is known before the test opens, which is the point:
a comparison weight taken from the window being scored is biased toward any
rule whose size responds to price (see `research/pid_sweep.py`, P10).

On design data the rule's timing alpha is compared with the same rule on
placebo histories, as in P10, rather than with zero.

Design functions refuse a price frame that runs past the design cutoff.
`final_test` is the only way into the test window.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from tradingbot.research.pid_placebo import KINDS, exceedance_p_value, placebo_prices
from tradingbot.research.pid_sweep import (
    TRADING_DAYS,
    ExecutionModel,
    buy_and_hold_metrics,
    path_metrics,
    simulate,
    static_mix_metrics,
)

TargetFn = Callable[[pd.Series], pd.Series]

# As in the PID placebo: keep rounding negligible on paths that wander.
_CASH_SCALE = 10_000.0


@dataclass(frozen=True)
class RulePath:
    """One rule simulated through the engine's order path."""

    dates: pd.DatetimeIndex
    asset_return: np.ndarray
    equity: np.ndarray  # 1 x day, so the grid metrics apply unchanged
    weight: np.ndarray
    bought: np.ndarray
    fills: np.ndarray
    initial_cash: float


def simulate_rule(
    prices: pd.DataFrame,
    make_targets: TargetFn,
    *,
    start: date,
    band: float,
    execution: ExecutionModel,
) -> RulePath:
    """Targets from the full history (so the rule is warm), trades from `start`."""
    window = prices.loc[pd.Timestamp(start) :]
    if window.empty:
        raise ValueError(f"No prices on or after {start}")
    offset = len(prices) - len(window)
    targets = make_targets(prices["close"]).to_numpy(dtype=float)[offset:]
    result = simulate(
        window["open"].to_numpy(dtype=float).tolist(),
        window["close"].to_numpy(dtype=float).tolist(),
        targets.tolist(),
        band=band,
        execution=execution,
    )
    return RulePath(
        dates=window.index,
        asset_return=window["close"].pct_change().fillna(0.0).to_numpy(),
        equity=result.equity[None, :],
        weight=result.weight[None, :],
        bought=result.bought[None, :],
        fills=result.fills[None, :],
        initial_cash=execution.initial_cash,
    )


def rule_metrics(path: RulePath, start: date, end: date) -> dict[str, float]:
    frame = path_metrics(
        path.dates, path.asset_return, path.equity, path.weight, path.bought, path.fills,
        path.initial_cash, start, end,
    )
    stats = {key: float(value) for key, value in frame.iloc[0].items()}
    stats["days"] = int(frame.attrs["days"])
    return stats


def comparison(path: RulePath, start: date, end: date, fixed_weight: float | None = None) -> dict[str, Any]:
    """The rule, the instrument held outright, and a fixed fraction of it.

    `fixed_weight` defaults to the rule's own average over this window, which
    is only acceptable on design data. On a test window pass the weight
    measured beforehand.
    """
    strategy = rule_metrics(path, start, end)
    weight = strategy["avg_weight"] if fixed_weight is None else fixed_weight
    return {
        "start": start,
        "end": end,
        "strategy": strategy,
        "buy_and_hold": buy_and_hold_metrics(path, start, end),
        "fixed": {"weight": weight, **static_mix_metrics(path, start, end, weight)},
    }


def yearly_table(path: RulePath, start: date, end: date) -> pd.DataFrame:
    """Per calendar year: the rule's return, the instrument's, and the weight held."""
    mask = (path.dates >= pd.Timestamp(start)) & (path.dates <= pd.Timestamp(end))
    rows = []
    for year in sorted(set(path.dates[mask].year)):
        first = max(pd.Timestamp(start), pd.Timestamp(year=year, month=1, day=1)).date()
        last = min(pd.Timestamp(end), pd.Timestamp(year=year, month=12, day=31)).date()
        in_year = (path.dates >= pd.Timestamp(first)) & (path.dates <= pd.Timestamp(last))
        if in_year.sum() < 2:
            continue
        stats = rule_metrics(path, first, last)
        hold = buy_and_hold_metrics(path, first, last)
        rows.append(
            {
                "year": year,
                "strategy_return": stats["total_return"],
                "hold_return": hold["total_return"],
                "avg_weight": stats["avg_weight"],
                "strategy_drawdown": stats["max_drawdown"],
                "hold_drawdown": hold["max_drawdown"],
            }
        )
    return pd.DataFrame(rows)


def placebo_runs(
    prices: pd.DataFrame,
    make_targets: TargetFn,
    *,
    start: date,
    end: date,
    band: float,
    execution: ExecutionModel,
    n: int,
    seed_base: dict[str, int] | None = None,
) -> pd.DataFrame:
    """The rule on skill-free histories: timing alpha and Sharpe gap per seed."""
    bases = seed_base or {"sign_flip": 0, "shuffle": 1000}
    scaled = replace(execution, initial_cash=execution.initial_cash * _CASH_SCALE)
    rows = []
    for kind in KINDS:
        for seed in range(bases[kind], bases[kind] + n):
            fake = placebo_prices(prices, kind, seed)
            path = simulate_rule(fake, make_targets, start=start, band=band, execution=scaled)
            stats = rule_metrics(path, start, end)
            hold = buy_and_hold_metrics(path, start, end)
            rows.append(
                {
                    "kind": kind,
                    "seed": seed,
                    "alpha": stats["alpha"],
                    "sharpe_gap": stats["sharpe"] - hold["sharpe"],
                }
            )
    return pd.DataFrame(rows)


def design_report(
    prices: pd.DataFrame,
    *,
    symbol: str,
    label: str,
    make_targets: TargetFn,
    variants: dict[str, TargetFn],
    band: float,
    sim_start: date,
    windows: dict[str, tuple[date, date]],
    execution: ExecutionModel,
    placebo_n: int,
) -> dict[str, Any]:
    """Everything that may be known before the test window opens.

    `windows` maps a label to a (start, end) pair; the last one is taken as
    the full design window. Refuses a frame that extends past it.
    """
    design_start, design_end = list(windows.values())[-1]
    if prices.index.max() > pd.Timestamp(design_end):
        raise ValueError(
            f"Price frame runs to {prices.index.max().date()}, past the design cutoff {design_end}; "
            "cut it before measuring."
        )
    path = simulate_rule(prices, make_targets, start=sim_start, band=band, execution=execution)
    report: dict[str, Any] = {
        "symbol": symbol,
        "label": label,
        "data": {"first": prices.index.min().date(), "cutoff": design_end, "rows": int(len(prices))},
        "windows": {name: comparison(path, start, end) for name, (start, end) in windows.items()},
        "yearly": yearly_table(path, design_start, design_end),
        "design_avg_weight": rule_metrics(path, design_start, design_end)["avg_weight"],
    }
    rows = []
    for name, targets in variants.items():
        variant = simulate_rule(prices, targets, start=sim_start, band=band, execution=execution)
        rows.append({"variant": name, **rule_metrics(variant, design_start, design_end)})
    report["variants"] = pd.DataFrame(rows)

    real = report["windows"][list(windows)[-1]]
    real_alpha = real["strategy"]["alpha"]
    real_gap = real["strategy"]["sharpe"] - real["buy_and_hold"]["sharpe"]
    if placebo_n > 0:
        runs = placebo_runs(
            prices, make_targets, start=design_start, end=design_end, band=band,
            execution=execution, n=placebo_n,
        )
        report["placebo"] = {
            kind: {
                "n": int(len(frame)),
                "alpha_mean": float(frame["alpha"].mean()),
                "alpha_p95": float(frame["alpha"].quantile(0.95)),
                "alpha_p": exceedance_p_value(real_alpha, frame["alpha"]),
                "sharpe_gap_mean": float(frame["sharpe_gap"].mean()),
                "sharpe_gap_p95": float(frame["sharpe_gap"].quantile(0.95)),
                "sharpe_gap_p": exceedance_p_value(real_gap, frame["sharpe_gap"]),
            }
            for kind, frame in runs.groupby("kind")
        }
    report["real"] = {"alpha": real_alpha, "sharpe_gap": real_gap}
    return report


def final_test(
    prices: pd.DataFrame,
    *,
    make_targets: TargetFn,
    variants: dict[str, TargetFn],
    band: float,
    sim_start: date,
    test_start: date,
    execution: ExecutionModel,
    min_total_return: float,
    fixed_weight: float,
) -> dict[str, Any]:
    """Open the test window once and read the registered number.

    The rule is simulated continuously from `sim_start`, so it enters the
    test window holding what it had built by then; its return is measured
    from the last close before the window. `fixed_weight` must come from
    design data.
    """
    end = prices.index.max().date()
    path = simulate_rule(prices, make_targets, start=sim_start, band=band, execution=execution)
    result = comparison(path, test_start, end, fixed_weight=fixed_weight)
    total = result["strategy"]["total_return"]
    result["min_total_return"] = min_total_return
    result["verdict"] = "pass" if total >= min_total_return else "fail"
    result["buy_and_hold_passes"] = bool(result["buy_and_hold"]["total_return"] >= min_total_return)
    result["fixed_passes"] = bool(result["fixed"]["total_return"] >= min_total_return)
    result["yearly"] = yearly_table(path, test_start, end)
    rows = []
    for name, targets in variants.items():
        variant = simulate_rule(prices, targets, start=sim_start, band=band, execution=execution)
        stats = rule_metrics(variant, test_start, end)
        rows.append({"variant": name, **stats, "passes": bool(stats["total_return"] >= min_total_return)})
    result["variants"] = pd.DataFrame(rows)
    return result


def highest_level_within(table: pd.DataFrame, max_drawdown: float) -> float | None:
    """The highest level such that it and every lower level stayed inside the limit.

    Not simply the highest level that passes. The trading band makes drawdown
    slightly uneven in the level, and a level that slips under the limit above
    one that did not is passing by an accident of when it happened to trade.
    """
    limit = -abs(max_drawdown)
    selected: float | None = None
    for _, row in table.sort_values("level").iterrows():
        if row["max_drawdown"] < limit:
            break
        selected = float(row["level"])
    return selected


def calibrate_level(
    prices: pd.DataFrame,
    *,
    targets_for: Callable[[float], TargetFn],
    levels: Sequence[float],
    max_drawdown: float,
    band: float,
    start: date,
    end: date,
    execution: ExecutionModel,
) -> tuple[float | None, pd.DataFrame]:
    """Find the risk level a drawdown limit allows, on design data only.

    This chooses how much risk to carry, not how to earn more: one parameter,
    one stated limit, and nothing about return enters the choice. Refuses a
    frame that runs past `end`.
    """
    if prices.index.max() > pd.Timestamp(end):
        raise ValueError(
            f"Price frame runs to {prices.index.max().date()}, past the design cutoff {end}; "
            "cut it before calibrating."
        )
    rows = []
    for level in levels:
        path = simulate_rule(prices, targets_for(level), start=start, band=band, execution=execution)
        stats = rule_metrics(path, start, end)
        rows.append(
            {
                "level": float(level),
                "max_drawdown": stats["max_drawdown"],
                "total_return": stats["total_return"],
                "cagr": stats["cagr"],
                "sharpe": stats["sharpe"],
                "avg_weight": stats["avg_weight"],
            }
        )
    table = pd.DataFrame(rows)
    table["within_limit"] = table["max_drawdown"] >= -abs(max_drawdown)
    return highest_level_within(table, max_drawdown), table


def render_calibration_markdown(
    selected: float | None, table: pd.DataFrame, *, symbol: str, max_drawdown: float, start: date, end: date
) -> str:
    lines = [
        f"# 낙폭 한도로 위험 수준 정하기 — {symbol}",
        "",
        f"한도: 최대 낙폭 {-abs(max_drawdown) * 100:.0f}%. 측정 구간 {start} ~ {end} (설계 구간만).",
        "",
        "선택 규칙: 자신과 그보다 낮은 모든 수준이 한도 안에 들어온 가장 높은 수준.",
        "",
        "| 목표 변동성 | 최대 낙폭 | 한도 이내 | 총수익 | 연환산 | 샤프 | 평균 비중 |",
        "|---|---|---|---|---|---|---|",
    ]
    for _, row in table.sort_values("level").iterrows():
        mark = " **← 선택**" if selected is not None and abs(row["level"] - selected) < 1e-9 else ""
        lines.append(
            f"| {row['level'] * 100:.0f}%{mark} | {_pct(row['max_drawdown'])} | {'예' if row['within_limit'] else '아니오'} "
            f"| {_pct(row['total_return'])} | {_pct(row['cagr'])} | {_num(row['sharpe'])} | {row['avg_weight'] * 100:.0f}% |"
        )
    lines.append("")
    if selected is None:
        lines.append("한도 안에 들어온 수준이 없다.")
    else:
        lines.append(f"선택된 목표 변동성: {selected * 100:.0f}%")
    lines += [
        "",
        "과거에 한도를 지켰다는 것이 앞으로도 지킨다는 보장은 아니다. 이 규칙은 변동성이 오른 *뒤에*",
        "비중을 줄이므로, 변동성이 오르기 전에 한 번에 오는 급락은 그때 들고 있던 비중으로 맞는다.",
        "",
    ]
    return "\n".join(lines)


def _pct(value: float, digits: int = 1) -> str:
    return "측정 불가" if value != value else f"{value * 100:+.{digits}f}%"


def _num(value: float) -> str:
    return "측정 불가" if value != value else f"{value:.2f}"


def _comparison_table(block: dict[str, Any]) -> list[str]:
    strategy, hold, fixed = block["strategy"], block["buy_and_hold"], block["fixed"]
    return [
        "| 구분 | 총수익 | 연환산 | 최대 낙폭 | 샤프 | 평균 비중 |",
        "|---|---|---|---|---|---|",
        f"| 전략 | {_pct(strategy['total_return'])} | {_pct(strategy['cagr'])} | {_pct(strategy['max_drawdown'])} "
        f"| {_num(strategy['sharpe'])} | {strategy['avg_weight'] * 100:.0f}% |",
        f"| {fixed['weight'] * 100:.0f}% 고정 보유 | {_pct(fixed['total_return'])} | {_pct(fixed['cagr'])} "
        f"| {_pct(fixed['max_drawdown'])} | {_num(fixed['sharpe'])} | {fixed['weight'] * 100:.0f}% |",
        f"| 전량 매수 후 보유 | {_pct(hold['total_return'])} | {_pct(hold['cagr'])} | {_pct(hold['max_drawdown'])} "
        f"| {_num(hold['sharpe'])} | 100% |",
    ]


def _yearly_lines(table: pd.DataFrame) -> list[str]:
    lines = [
        "| 연도 | 전략 수익 | 보유 수익 | 평균 비중 | 전략 낙폭 | 보유 낙폭 |",
        "|---|---|---|---|---|---|",
    ]
    for _, row in table.iterrows():
        lines.append(
            f"| {int(row['year'])} | {_pct(row['strategy_return'])} | {_pct(row['hold_return'])} "
            f"| {row['avg_weight'] * 100:.0f}% | {_pct(row['strategy_drawdown'])} | {_pct(row['hold_drawdown'])} |"
        )
    return lines


def _variant_lines(table: pd.DataFrame, *, with_verdict: bool) -> list[str]:
    head = "| 설정 | 총수익 | 최대 낙폭 | 샤프 | 평균 비중 | 연 회전율 |"
    rule = "|---|---|---|---|---|---|"
    if with_verdict:
        head, rule = head + " 기준 충족 |", rule + "---|"
    lines = [head, rule]
    for _, row in table.iterrows():
        line = (
            f"| {row['variant']} | {_pct(row['total_return'])} | {_pct(row['max_drawdown'])} "
            f"| {_num(row['sharpe'])} | {row['avg_weight'] * 100:.0f}% | {_num(row['turnover'])} |"
        )
        if with_verdict:
            line += f" {'예' if row['passes'] else '아니오'} |"
        lines.append(line)
    return lines


def render_design_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['label']} — {report['symbol']} 설계 구간",
        "",
        f"가격 이력 {report['data']['first']}부터, 읽은 마지막 날 {report['data']['cutoff']}. "
        "시험 구간은 읽지 않았다.",
        "",
    ]
    for name, block in report["windows"].items():
        lines += [f"## {name} ({block['start']} ~ {block['end']})", "", *_comparison_table(block), ""]
    lines += ["## 연도별", "", *_yearly_lines(report["yearly"]), ""]
    lines += ["## 설정을 바꿨을 때 (설계 구간 전체)", "", *_variant_lines(report["variants"], with_verdict=False), ""]
    if "placebo" in report:
        real = report["real"]
        lines += [
            "## 비중 조절이 실제로 한 일이 있는가 — 위약 비교",
            "",
            "실제 수익률에서 방향만 무작위로 바꾼 가짜 가격에 같은 규칙을 돌린 것과 비교한다.",
            "",
            "| 통계량 | 실제 | " + " | ".join(f"{k} 평균 | {k} 95% | {k} p" for k in report["placebo"]) + " |",
            "|---|---|" + "---|---|---|" * len(report["placebo"]),
            f"| 타이밍 알파 | {_pct(real['alpha'])} | "
            + " | ".join(
                f"{_pct(v['alpha_mean'])} | {_pct(v['alpha_p95'])} | {v['alpha_p']:.3f}"
                for v in report["placebo"].values()
            )
            + " |",
            f"| 샤프 차이 (전략 − 보유) | {real['sharpe_gap']:+.2f} | "
            + " | ".join(
                f"{v['sharpe_gap_mean']:+.2f} | {v['sharpe_gap_p95']:+.2f} | {v['sharpe_gap_p']:.3f}"
                for v in report["placebo"].values()
            )
            + " |",
            "",
        ]
    lines += [f"시험 구간 비교에 쓸 고정 비중(설계 구간 평균): {report['design_avg_weight'] * 100:.0f}%", ""]
    return "\n".join(lines)


def render_final_markdown(result: dict[str, Any], *, symbol: str, label: str) -> str:
    total = result["strategy"]["total_return"]
    passed = result["verdict"] == "pass"
    lines = [
        f"# {label} — {symbol} 최종 시험 ({result['start']} ~ {result['end']})",
        "",
        f"**판정: {'합격' if passed else '불합격'}** — 누적 수익 {_pct(total)}, 기준 {_pct(result['min_total_return'])} 이상.",
        "",
        *_comparison_table(result),
        "",
        f"- 같은 기준을 전량 보유는 {'충족' if result['buy_and_hold_passes'] else '미달'}, "
        f"{result['fixed']['weight'] * 100:.0f}% 고정 보유는 {'충족' if result['fixed_passes'] else '미달'}.",
        "",
        "## 연도별",
        "",
        *_yearly_lines(result["yearly"]),
        "",
        "## 설정을 바꿨을 때 (판정에는 쓰지 않는다)",
        "",
        *_variant_lines(result["variants"], with_verdict=True),
        "",
    ]
    return "\n".join(lines)
