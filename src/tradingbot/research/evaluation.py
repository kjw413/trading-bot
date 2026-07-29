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

from tradingbot.research.walk_forward import WalkForwardWindow
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
