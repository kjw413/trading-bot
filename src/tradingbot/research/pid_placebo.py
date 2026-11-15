"""Score the PID search against price histories where no rule can have skill.

The search's statistics are biased upward for a rule that sells strength:
timing alpha is measured against the strategy's own average weight over the
window, that average is only known once the window is over, and for a
contrarian rule it is low exactly where the instrument rose. Comparing the
statistics with zero therefore proves nothing.

So they are compared with themselves on placebo histories — real returns with
the one thing a timing rule could use removed — run through the identical
search. A bias that exists without skill exists in the placebo too, and the
gap between real and placebo is what is left for skill to explain.

    sign_flip   every day's move stays on its own date and keeps its size;
                only its direction is randomised. Volatility clustering and
                the crisis episodes stay where they were.
    shuffle     the same days in random order. Removes volatility clustering
                as well.

The placebo is built from the search's own window and nothing after it.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import date
from typing import Any, Sequence

import numpy as np
import pandas as pd

from tradingbot.research.pid_sweep import (
    ExecutionModel,
    SelectionRule,
    build_report,
)
from tradingbot.research.walk_forward import WalkForwardWindow
from tradingbot.strategies.pid_reversion import PidParams

KINDS = ("sign_flip", "shuffle")

# Placebo prices wander far from where they start. A high first close and a
# proportionally large account keep whole-share and one-cent rounding as
# negligible on a placebo path as they are on the real one.
_START_CLOSE = 1000.0
_CASH_SCALE = 10_000.0


def placebo_prices(prices: pd.DataFrame, kind: str, seed: int) -> pd.DataFrame:
    """A history on the same dates with the direction of returns made unpredictable.

    Each day is an (overnight, intraday) pair of log returns and the pair is
    kept together, so the relation between a gap and the session after it is
    not broken apart by the randomisation.
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown placebo kind: {kind}. Available: {', '.join(KINDS)}")
    opens = prices["open"].to_numpy(dtype=float)
    closes = prices["close"].to_numpy(dtype=float)
    overnight = np.log(opens[1:] / closes[:-1])
    intraday = np.log(closes[1:] / opens[1:])

    rng = np.random.default_rng(seed)
    if kind == "sign_flip":
        sign = rng.choice((-1.0, 1.0), size=len(overnight))
        overnight = overnight.mean() + sign * (overnight - overnight.mean())
        intraday = intraday.mean() + sign * (intraday - intraday.mean())
    else:
        order = rng.permutation(len(overnight))
        overnight, intraday = overnight[order], intraday[order]

    new_closes = np.empty(len(closes))
    new_opens = np.empty(len(closes))
    new_closes[0] = new_opens[0] = _START_CLOSE
    steps = np.cumsum(overnight + intraday)
    new_closes[1:] = _START_CLOSE * np.exp(steps)
    new_opens[1:] = new_closes[:-1] * np.exp(overnight)
    return pd.DataFrame(
        {
            "open": new_opens,
            "high": np.maximum(new_opens, new_closes),
            "low": np.minimum(new_opens, new_closes),
            "close": new_closes,
            "volume": prices["volume"].to_numpy(dtype=float),
        },
        index=prices.index,
    )


def search_statistics(report: dict[str, Any]) -> dict[str, Any]:
    """The numbers a real run and a placebo run are compared on."""
    stats: dict[str, Any] = {
        "verdict": report["verdict"],
        "selected": report.get("selected") is not None,
        "wf_mean_alpha": float(report["walk_forward_summary"]["mean_test_alpha"]),
        "wf_positive_share": float(report["walk_forward_summary"]["positive_share"]),
        "grid_median_alpha": float(report["grid_summary"]["median_alpha"]),
        "grid_positive_share": float(report["grid_summary"]["positive_alpha_share"]),
        "validation_alpha": float("nan"),
        "in_sample_alpha": float("nan"),
        "validation_sharpe_gap": float("nan"),
        "in_sample_sharpe_gap": float("nan"),
    }
    if report.get("selected") is not None:
        for name in ("validation", "in_sample"):
            evaluation = report[f"{name}_eval"]
            stats[f"{name}_alpha"] = float(evaluation["strategy"]["alpha"])
            stats[f"{name}_sharpe_gap"] = float(
                evaluation["strategy"]["sharpe"] - evaluation["buy_and_hold"]["sharpe"]
            )
    return stats


def _placebo_run(job: tuple) -> dict[str, Any]:
    prices, kind, seed, settings = job
    report = build_report(placebo_prices(prices, kind, seed), **settings)
    return {"kind": kind, "seed": seed, **search_statistics(report)}


def run_placebo(
    prices: pd.DataFrame,
    *,
    kind: str,
    seeds: Sequence[int],
    in_sample: tuple[date, date],
    validation: tuple[date, date],
    walk_forward_windows_: Sequence[WalkForwardWindow],
    grid: dict[str, Sequence[float]],
    base: PidParams,
    execution: ExecutionModel,
    rule: SelectionRule,
    processes: int = 1,
) -> pd.DataFrame:
    """The full search on each placebo history; one row of statistics per seed."""
    cut = prices.loc[: pd.Timestamp(validation[1])]
    settings = {
        "symbol": f"placebo:{kind}",
        "in_sample": in_sample,
        "validation": validation,
        "walk_forward_windows_": list(walk_forward_windows_),
        "grid": grid,
        "base": base,
        "execution": replace(execution, initial_cash=execution.initial_cash * _CASH_SCALE),
        "rule": rule,
    }
    jobs = [(cut, kind, int(seed), settings) for seed in seeds]
    if processes <= 1:
        rows = [_placebo_run(job) for job in jobs]
    else:
        with ProcessPoolExecutor(max_workers=processes) as pool:
            rows = list(pool.map(_placebo_run, jobs))
    return pd.DataFrame(rows)


def exceedance_p_value(real: float, placebo: Sequence[float]) -> float:
    """One-sided p: how often a skill-free history did at least as well.

    The +1 in both terms counts the real run as one draw from the null, which
    keeps the estimate from ever being exactly zero on a finite sample. A
    placebo run with no measurable statistic counts as not exceeding: it
    produced no evidence of anything.
    """
    values = np.asarray(list(placebo), dtype=float)
    if real != real or len(values) == 0:
        return float("nan")
    return float((1 + np.sum(values >= real)) / (len(values) + 1))


def decide_against_placebo(real: dict[str, Any], placebo: pd.DataFrame, *, level: float = 0.05) -> tuple[str, str, dict[str, float]]:
    """The amended decision rule, on the deciding null's runs."""
    p_wf = exceedance_p_value(real["wf_mean_alpha"], placebo["wf_mean_alpha"])
    p_val = exceedance_p_value(real["validation_alpha"], placebo["validation_alpha"])
    median_val = float(np.nanmedian(placebo["validation_alpha"]))
    numbers = {
        "p_walk_forward": p_wf,
        "p_validation": p_val,
        "placebo_wf_mean": float(np.nanmean(placebo["wf_mean_alpha"])),
        "placebo_wf_p95": float(np.nanpercentile(placebo["wf_mean_alpha"], 95)),
        "placebo_validation_mean": float(np.nanmean(placebo["validation_alpha"])),
        "placebo_validation_median": median_val,
        "placebo_original_rule_pass_share": float((placebo["verdict"] == "consistent").mean()),
    }
    if p_wf != p_wf or real["validation_alpha"] != real["validation_alpha"]:
        return "unmeasured", "A required statistic could not be measured.", numbers
    if p_wf >= level:
        return (
            "rejected",
            f"Walk-forward alpha is not beyond what skill-free histories produce (p = {p_wf:.3f}).",
            numbers,
        )
    if real["validation_alpha"] <= median_val:
        return (
            "rejected",
            "Validation alpha is no better than the typical skill-free history.",
            numbers,
        )
    return "consistent", f"Walk-forward alpha exceeds the placebo (p = {p_wf:.3f}) and validation is above its median.", numbers


def _pct(value: float) -> str:
    return "측정 불가" if value != value else f"{value * 100:+.1f}%"


def render_markdown(real: dict[str, Any], runs: dict[str, pd.DataFrame], deciding: str, verdict: str, reason: str) -> str:
    lines = [
        "# PID 탐색 위약 검정",
        "",
        f"**판정: {'일관됨 (채택 후보)' if verdict == 'consistent' else '기각' if verdict == 'rejected' else '측정 불가'}** — {reason}",
        "",
        "위약 가격은 실제 수익률에서 '방향의 예측 가능성'만 없앤 것이다. 어떤 규칙도 실력으로 벌 수",
        "없는 가격인데도 지표가 양수로 나오면, 그만큼은 지표의 편향이다.",
        "",
        "| 통계량 | 실제 | " + " | ".join(f"{kind} 평균 | {kind} 95% | {kind} p" for kind in runs) + " |",
        "|---|---|" + "---|---|---|" * len(runs),
    ]
    rows = (
        ("워크포워드 다음 해 알파 평균", "wf_mean_alpha"),
        ("검증 구간 알파", "validation_alpha"),
        ("학습 구간 알파 (선택된 구성)", "in_sample_alpha"),
        ("학습 구간 격자 알파 중앙값", "grid_median_alpha"),
        ("검증 구간 샤프 차이 (전략 − 보유)", "validation_sharpe_gap"),
        ("학습 구간 샤프 차이 (전략 − 보유)", "in_sample_sharpe_gap"),
    )
    for label, key in rows:
        cells = []
        for frame in runs.values():
            values = frame[key].to_numpy(dtype=float)
            p_value = exceedance_p_value(real[key], values)
            if key.endswith("sharpe_gap"):
                cells += [f"{np.nanmean(values):+.2f}", f"{np.nanpercentile(values, 95):+.2f}", f"{p_value:.3f}"]
            else:
                cells += [_pct(float(np.nanmean(values))), _pct(float(np.nanpercentile(values, 95))), f"{p_value:.3f}"]
        shown = f"{real[key]:+.2f}" if key.endswith("sharpe_gap") else _pct(real[key])
        lines.append(f"| {label} | {shown} | " + " | ".join(cells) + " |")
    lines += ["", "## 처음 등록한 판정 규칙이 위약에서 통과한 비율", ""]
    for kind, frame in runs.items():
        share = float((frame["verdict"] == "consistent").mean())
        years = float(np.nanmean(frame["wf_positive_share"]))
        lines.append(
            f"- {kind} ({len(frame)}개): '일관됨' {share * 100:.0f}%, "
            f"워크포워드에서 알파가 양수였던 해 평균 {years * 100:.0f}%"
        )
    lines += ["", f"판정에 쓰는 위약: {deciding}", ""]
    return "\n".join(lines)
