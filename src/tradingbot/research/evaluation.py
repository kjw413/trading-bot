"""Measure a strategy against the promotion criteria.

Three of the six criteria — walk-forward win rate, annual turnover, and
cost-doubling sensitivity — had no harness at all, so no strategy could
earn a pass. This module supplies them by composing the existing backtest
engine rather than adding new simulation logic.

Nothing here searches for better parameters. It measures; a human decides
what to change. A search loop bolted onto a measurement tool is automated
overfitting.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Sequence

from tradingbot.report.metrics import annual_turnover, calculate_metrics
from tradingbot.research.walk_forward import WalkForwardWindow, walk_forward_windows
from tradingbot.services import run_backtest
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)


def scale_costs(config: dict[str, Any], market: str, multiplier: float) -> dict[str, Any]:
    """Return a copy of `config` with this market's trading costs multiplied.

    Scales every numeric entry under `[fees.<market>]` plus
    `[execution] slippage_bps`. The FINRA TAF cap scales with its rate on
    purpose: leaving the cap fixed would let it absorb the increase and
    neuter the sensitivity test.

    The input config is never mutated — callers run the same config at 1x
    and 2x and must not have the first run contaminate the second.
    """
    if multiplier < 0:
        raise ValueError("multiplier must be non-negative")

    scaled = copy.deepcopy(config)
    market_fees = scaled.get("fees", {}).get(market.upper())
    if market_fees:
        for name, value in market_fees.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                market_fees[name] = value * multiplier

    execution = scaled.setdefault("execution", {})
    slippage = execution.get("slippage_bps")
    if isinstance(slippage, (int, float)) and not isinstance(slippage, bool):
        execution["slippage_bps"] = slippage * multiplier
    return scaled


@dataclass(frozen=True)
class WindowResult:
    """One rolling out-of-sample window: how the strategy did against the benchmark.

    `won` is None when the window could not be evaluated; `error` says why.
    A failed window is unmeasured, not a loss.
    """

    test_start: date
    test_end: date
    strategy_return_pct: float
    benchmark_return_pct: float
    won: bool | None
    error: str


def run_walk_forward(
    *,
    config: dict[str, Any],
    benchmark_config: dict[str, Any],
    market: str,
    symbols: Sequence[str],
    strategy_name: str,
    windows: Sequence[WalkForwardWindow],
    runner: Callable[..., Any] = run_backtest,
) -> list[WindowResult]:
    """Backtest strategy and benchmark over each window's test segment.

    Only the test segment runs. The train segment is unused because nothing
    in this strategy is fitted to data — what this measures is consistency
    across independent rolling periods, and the report says so plainly.

    A window that fails is recorded with `won=None` rather than dropped, so
    the win rate cannot be quietly inflated by discarding hard periods.
    """
    results: list[WindowResult] = []
    for window in windows:
        start = window.test_start.isoformat()
        end = window.test_end.isoformat()
        try:
            strategy_result = runner(
                config,
                market=market,
                symbols=list(symbols),
                strategy_name=strategy_name,
                start=start,
                end=end,
            )
            benchmark_result = runner(
                benchmark_config,
                market=market,
                symbols=list(symbols),
                strategy_name=strategy_name,
                start=start,
                end=end,
            )
            strategy_return = strategy_result.return_pct
            benchmark_return = benchmark_result.return_pct
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            LOGGER.exception("Walk-forward window %s..%s failed", start, end)
            results.append(
                WindowResult(
                    test_start=window.test_start,
                    test_end=window.test_end,
                    strategy_return_pct=float("nan"),
                    benchmark_return_pct=float("nan"),
                    won=None,
                    error=str(exc),
                )
            )
            continue

        results.append(
            WindowResult(
                test_start=window.test_start,
                test_end=window.test_end,
                strategy_return_pct=strategy_return,
                benchmark_return_pct=benchmark_return,
                won=strategy_return > benchmark_return,
                error="",
            )
        )
    return results


def win_rate(results: Sequence[WindowResult]) -> float:
    """Share of evaluated windows the strategy won. NaN when none were evaluated."""
    evaluated = [result for result in results if result.won is not None]
    if not evaluated:
        return float("nan")
    return sum(1 for result in evaluated if result.won) / len(evaluated)


@dataclass(frozen=True)
class CriterionResult:
    """One promotion criterion. `passed=None` means it could not be measured."""

    name: str
    threshold: str
    measured: float
    passed: bool | None


@dataclass(frozen=True)
class Verdict:
    promoted: bool
    criteria: list[CriterionResult]

    @property
    def unmeasured(self) -> list[str]:
        return [c.name for c in self.criteria if c.passed is None]


def _at_least(measured: float, floor: float) -> bool | None:
    if math.isnan(measured):
        return None
    return measured >= floor


def _at_most(measured: float, ceiling: float) -> bool | None:
    if math.isnan(measured):
        return None
    return measured <= ceiling


def judge(
    *,
    excess_return: float,
    sharpe: float,
    mdd: float,
    turnover: float,
    wf_win_rate: float,
    excess_return_2x: float,
    promotion: dict[str, Any],
) -> Verdict:
    """Compare measurements against the promotion criteria.

    An unmeasured criterion (NaN) is never a pass — it blocks promotion just
    as a failure does. Distinguishing "we checked and it's fine" from "we
    never checked" is the reason this tool exists.
    """
    criteria = [
        CriterionResult(
            "excess_return",
            f">= {promotion['min_excess_return']}",
            excess_return,
            _at_least(excess_return, float(promotion["min_excess_return"])),
        ),
        CriterionResult(
            "sharpe",
            f">= {promotion['min_sharpe']}",
            sharpe,
            _at_least(sharpe, float(promotion["min_sharpe"])),
        ),
        CriterionResult(
            "max_drawdown",
            f"<= {promotion['max_mdd']}",
            mdd,
            _at_most(mdd, float(promotion["max_mdd"])),
        ),
        CriterionResult(
            "annual_turnover",
            f"<= {promotion['max_annual_turnover']}",
            turnover,
            _at_most(turnover, float(promotion["max_annual_turnover"])),
        ),
        CriterionResult(
            "walk_forward_win_rate",
            f">= {promotion['min_walk_forward_win_rate']}",
            wf_win_rate,
            _at_least(wf_win_rate, float(promotion["min_walk_forward_win_rate"])),
        ),
        CriterionResult(
            f"excess_return_at_{promotion['cost_multiplier_check']}x_costs",
            f">= {promotion['min_excess_return']}",
            excess_return_2x,
            _at_least(excess_return_2x, float(promotion["min_excess_return"])),
        ),
    ]
    return Verdict(promoted=all(c.passed for c in criteria), criteria=criteria)


def _measure(result: Any) -> dict[str, float]:
    metrics, _, _ = calculate_metrics(result)
    return {
        "total_return_pct": float(result.return_pct),
        "cagr_pct": float(metrics.cagr_pct),
        "max_drawdown_pct": float(metrics.max_drawdown_pct),
        "sharpe": float(metrics.sharpe),
        "annual_turnover": float(annual_turnover(result)),
        "trades": int(result.trade_count),
        "rejected_orders": len(result.rejected_orders),
    }


def evaluate_strategy(
    *,
    config: dict[str, Any],
    benchmark_config: dict[str, Any],
    research: dict[str, Any],
    market: str,
    symbols: Sequence[str],
    strategy_name: str,
    start: str,
    end: str | None = None,
    runner: Callable[..., Any] = run_backtest,
) -> dict[str, Any]:
    """Measure a strategy against every promotion criterion.

    Runs the full period for strategy and benchmark, repeats it at doubled
    costs, walks rolling out-of-sample windows, and judges the result.
    """
    promotion = research["promotion"]
    multiplier = float(promotion["cost_multiplier_check"])

    def backtest(active_config: dict[str, Any]) -> Any:
        return runner(
            active_config,
            market=market,
            symbols=list(symbols),
            strategy_name=strategy_name,
            start=start,
            end=end,
        )

    strategy_result = backtest(config)
    benchmark_result = backtest(benchmark_config)
    strategy = _measure(strategy_result)
    benchmark = _measure(benchmark_result)

    strategy_2x = _measure(backtest(scale_costs(config, market, multiplier)))
    benchmark_2x = _measure(backtest(scale_costs(benchmark_config, market, multiplier)))

    wf_config = research.get("walk_forward", {})
    windows = walk_forward_windows(
        date.fromisoformat(start),
        date.fromisoformat(end) if end else date.today(),
        train_years=int(wf_config.get("train_years", 3)),
        test_years=int(wf_config.get("test_years", 1)),
        step_years=int(wf_config.get("step_years", 1)),
    )
    window_results = run_walk_forward(
        config=config,
        benchmark_config=benchmark_config,
        market=market,
        symbols=symbols,
        strategy_name=strategy_name,
        windows=windows,
        runner=runner,
    )

    # Both excess figures are annualized (CAGR difference in percentage
    # points). Mixing CAGR here and total return there would make the cost
    # check incomparable to the headline criterion it is supposed to stress.
    excess_return = strategy["cagr_pct"] - benchmark["cagr_pct"]
    excess_return_2x = strategy_2x["cagr_pct"] - benchmark_2x["cagr_pct"]
    verdict = judge(
        excess_return=excess_return,
        sharpe=strategy["sharpe"],
        mdd=abs(strategy["max_drawdown_pct"]) / 100.0,
        turnover=strategy["annual_turnover"],
        wf_win_rate=win_rate(window_results),
        excess_return_2x=excess_return_2x,
        promotion=promotion,
    )

    # A window is either evaluated or failed; a reader must be able to see
    # both counts, not just the win rate that survives them. Otherwise nine
    # failed windows out of ten can leave a single surviving win reading as
    # a clean 1.0 win rate.
    evaluated = sum(1 for result in window_results if result.won is not None)
    failed = sum(1 for result in window_results if result.won is None)

    return {
        "strategy_name": strategy_name,
        "market": market.upper(),
        "symbols": list(symbols),
        "period": {"start": start, "end": end},
        "strategy": strategy,
        "benchmark": benchmark,
        "excess_return_pct": excess_return,
        "cost_2x": {
            "multiplier": multiplier,
            "strategy_total_return_pct": strategy_2x["total_return_pct"],
            "benchmark_total_return_pct": benchmark_2x["total_return_pct"],
            "strategy_cagr_pct": strategy_2x["cagr_pct"],
            "benchmark_cagr_pct": benchmark_2x["cagr_pct"],
            "excess_return_pct": excess_return_2x,
        },
        "walk_forward": {
            "win_rate": win_rate(window_results),
            "train_segments_used": False,
            "evaluated": evaluated,
            "failed": failed,
            "total": len(window_results),
            "windows": [
                {
                    "test_start": result.test_start.isoformat(),
                    "test_end": result.test_end.isoformat(),
                    "strategy_return_pct": result.strategy_return_pct,
                    "benchmark_return_pct": result.benchmark_return_pct,
                    "won": result.won,
                    "error": result.error,
                }
                for result in window_results
            ],
        },
        "verdict": {
            "promoted": verdict.promoted,
            "unmeasured": verdict.unmeasured,
            "criteria": [
                {
                    "name": c.name,
                    "threshold": c.threshold,
                    "measured": c.measured,
                    "passed": c.passed,
                }
                for c in verdict.criteria
            ],
        },
    }


def _verdict_sentence(report: dict[str, Any]) -> str:
    """Plain-language conclusion — no jargon, for the person deciding."""
    verdict = report["verdict"]
    failed = [c["name"] for c in verdict["criteria"] if c["passed"] is False]
    unmeasured = verdict["unmeasured"]

    if verdict["promoted"]:
        return (
            "이 전략은 승격 기준 6개를 모두 충족했습니다. 다음 단계(모의투자)로 "
            "넘길 수 있습니다."
        )
    parts = ["이 전략은 아직 실제 자금을 넣을 단계가 아닙니다."]
    if failed:
        parts.append(f"기준에 미달한 항목: {', '.join(failed)}.")
    if unmeasured:
        parts.append(
            f"측정하지 못한 항목: {', '.join(unmeasured)} — 미달이 아니라 "
            "확인이 안 된 것이며, 확인 전에는 통과로 치지 않습니다."
        )
    return " ".join(parts)


def render_markdown(report: dict[str, Any]) -> str:
    strategy = report["strategy"]
    benchmark = report["benchmark"]
    period = report["period"]
    lines = [
        f"# {report['strategy_name']} 승격 평가 ({report['market']})",
        "",
        "## 결론",
        "",
        _verdict_sentence(report),
        "",
        f"- 기간: {period['start']} ~ {period['end'] or '최신'}",
        f"- 종목: {', '.join(report['symbols'])}",
        "",
        "## 성과",
        "",
        "| 지표 | 전략 | 벤치마크 |",
        "|---|---|---|",
        f"| 총수익률 | {strategy['total_return_pct']:.2f}% | {benchmark['total_return_pct']:.2f}% |",
        f"| CAGR | {strategy['cagr_pct']:.2f}% | {benchmark['cagr_pct']:.2f}% |",
        f"| MDD | {strategy['max_drawdown_pct']:.2f}% | {benchmark['max_drawdown_pct']:.2f}% |",
        f"| Sharpe | {strategy['sharpe']:.2f} | {benchmark['sharpe']:.2f} |",
        f"| 연 회전율 | {strategy['annual_turnover']:.2f} | {benchmark['annual_turnover']:.2f} |",
        f"| 체결수 | {strategy['trades']} | {benchmark['trades']} |",
        f"| 거부 주문 | {strategy['rejected_orders']} | {benchmark['rejected_orders']} |",
        "",
        "## 승격 기준",
        "",
        "| 기준 | 임계값 | 실측 | 판정 |",
        "|---|---|---|---|",
    ]
    for criterion in report["verdict"]["criteria"]:
        if criterion["passed"] is None:
            mark = "**측정 불가**"
        elif criterion["passed"]:
            mark = "통과"
        else:
            mark = "**미달**"
        lines.append(
            f"| {criterion['name']} | {criterion['threshold']} | "
            f"{criterion['measured']:.4f} | {mark} |"
        )

    cost = report["cost_2x"]
    lines += [
        "",
        f"## 비용 {cost['multiplier']:g}배 검정",
        "",
        "전략과 벤치마크 모두 같은 배수의 비용을 뭅니다 — 초과수익은 상대 개념이라",
        "한쪽만 올리면 비교가 성립하지 않습니다.",
        "",
        f"- 전략: 총수익률 {cost['strategy_total_return_pct']:.2f}% / CAGR {cost['strategy_cagr_pct']:.2f}%",
        f"- 벤치마크: 총수익률 {cost['benchmark_total_return_pct']:.2f}% / CAGR {cost['benchmark_cagr_pct']:.2f}%",
        f"- 초과수익(CAGR 기준): {cost['excess_return_pct']:.2f}%p",
        "",
        "## 구간별 결과 (롤링 독립구간)",
        "",
    ]

    wf = report["walk_forward"]
    win_rate_text = "측정 불가" if math.isnan(wf["win_rate"]) else f"{wf['win_rate']:.2f}"
    lines += [
        f"- 승률 {win_rate_text} — 전체 {wf['total']}구간 중 {wf['evaluated']}구간 평가, "
        f"{wf['failed']}구간 측정 실패",
        "",
        "학습 구간은 사용하지 않습니다. 이 전략은 파라미터를 데이터로 맞추지 않는",
        "규칙 기반이라 학습할 것이 없고, 따라서 이 표가 재는 것은 '여러 시기에 걸친",
        "일관성'입니다. 나중에 파라미터를 튜닝하기 시작하면 학습 구간이 실제 의미를",
        "갖게 됩니다.",
        "",
        "| 구간 | 전략 | 벤치마크 | 결과 |",
        "|---|---|---|---|",
    ]
    for window in report["walk_forward"]["windows"]:
        if window["won"] is None:
            outcome = f"측정 실패 ({window['error']})"
            numbers = "— | —"
        else:
            outcome = "승" if window["won"] else "패"
            numbers = (
                f"{window['strategy_return_pct']:.2f}% | "
                f"{window['benchmark_return_pct']:.2f}%"
            )
        lines.append(
            f"| {window['test_start']} ~ {window['test_end']} | {numbers} | {outcome} |"
        )
    lines.append("")
    return "\n".join(lines)
