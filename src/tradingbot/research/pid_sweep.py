"""Search the PID controller's parameters without letting the search flatter it.

A parameter search always finds something. Run a few hundred controllers over
one price history and the best of them looks skilful whether or not any of
them is, so the search here is built around three things that make its answer
checkable rather than merely good.

**What counts as value.** Timing alpha: the strategy's daily return minus what
a fixed position of the same average size earned on the same days. A rule that
simply holds less of a volatile instrument has a smaller drawdown than
buy-and-hold without having timed anything; comparing against its own average
exposure removes that and leaves only what the *changes* in size contributed.

**How one is picked.** By the worse of a configuration's own alpha and the
median over its grid neighbours, not by its own alone. A real effect survives
a small change in a gain; a lucky draw is a spike surrounded by nothing.

**Where it is judged.** On dates the pick never saw: the validation window,
and a walk-forward that repeats the whole pick on each trailing window and
scores the following year. The second is the direct test of "optimise, then
trade" as a procedure, rather than of one configuration.

The simulator is not a model of the engine, it is the engine's arithmetic for
this one strategy — whole shares, the same order sizing, rounding, fees and
rejections — because a search run on a friendlier simulator would select
parameters for a market the bot does not trade in. A test holds the two equal.

Out-of-sample data is not loaded by the search at all. `evaluate_window` is
the only way past the cutoff and takes one configuration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date
from itertools import product
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from tradingbot.research.walk_forward import WalkForwardWindow
from tradingbot.strategies.pid_reversion import PidParams, target_weights

TRADING_DAYS = 252

# The swept axes, in the order a configuration's grid position is written.
AXES = ("trend_days", "integral_days", "kp", "ki", "kd", "base_weight")


@dataclass(frozen=True)
class ExecutionModel:
    """The costs and limits the backtest engine applies, read from the same config."""

    initial_cash: float
    slippage_bps: float
    commission_rate: float
    sec_fee_rate: float
    taf_per_share: float
    taf_cap: float
    min_cash_buffer_pct: float
    max_position_pct: float

    @classmethod
    def from_config(cls, config: dict[str, Any], market: str) -> "ExecutionModel":
        if market.upper() != "US":
            # KR adds a sell tax and a price-dependent tick table; neither is
            # mirrored here, and a silent approximation would defeat the point.
            raise ValueError("The PID sweep simulator mirrors US execution only")
        fees = config.get("fees", {}).get("US", {})
        risk = config.get("risk", {})
        # The engine's stop-loss liquidates a position 5% under its average
        # cost and its daily-loss limit blocks buying after a bad day. Both
        # act against a rule whose whole job is to add on weakness, and
        # neither is reproduced below — so a config that enables them would
        # be searched on one set of rules and traded on another.
        if float(risk.get("stop_loss_pct", 0.0)) > 0 or float(risk.get("max_daily_loss_pct", 1.0)) < 1.0:
            raise ValueError(
                "The PID sweep simulator does not mirror the engine stop-loss or daily-loss "
                "limit; use a config with [risk] stop_loss_pct = 0 and max_daily_loss_pct = 1.0 "
                "(config/pid_soxx.toml)."
            )
        return cls(
            initial_cash=float(config.get("backtest", {}).get("initial_cash_us", 0.0)),
            slippage_bps=float(config.get("execution", {}).get("slippage_bps", 0.0)),
            commission_rate=float(fees.get("commission_rate", 0.0)),
            sec_fee_rate=float(fees.get("sec_fee_rate", 0.0)),
            taf_per_share=float(fees.get("finra_taf_per_share", 0.0)),
            taf_cap=float(fees.get("finra_taf_cap", 0.0)),
            min_cash_buffer_pct=float(risk.get("min_cash_buffer_pct", 0.0)),
            max_position_pct=float(risk.get("max_position_pct", 1.0)),
        )


@dataclass(frozen=True)
class SimResult:
    equity: np.ndarray      # at each close
    weight: np.ndarray      # position value / equity at each close
    bought: np.ndarray      # gross purchases filled that day
    fills: np.ndarray       # fills that day (0 or 1)


def simulate(
    opens: Sequence[float],
    closes: Sequence[float],
    targets: Sequence[float],
    *,
    band: float,
    execution: ExecutionModel,
) -> SimResult:
    """One instrument through the backtest engine's order path, without the engine.

    A decision made at a close fills at the next open. Every sizing rule,
    rounding step and rejection below is the engine's, in the engine's order;
    the comments name the function each one comes from.
    """
    n = len(closes)
    equity_out = np.empty(n)
    weight_out = np.empty(n)
    bought_out = np.zeros(n)
    fills_out = np.zeros(n)

    cash = float(execution.initial_cash)
    qty = 0
    pending_side = 0  # +1 buy, -1 sell, 0 none
    pending_qty = 0
    slip = execution.slippage_bps / 10_000

    for t in range(n):
        if pending_side:
            base = float(opens[t])
            if pending_side > 0:
                # fees.apply_slippage + round_execution_price (US tick 0.01, buys round up)
                price = math.ceil((base * (1 + slip) - 1e-12) / 0.01) * 0.01
                gross = price * pending_qty
                fee = gross * execution.commission_rate
                # BacktestBroker._fill: an order the cash cannot cover is rejected whole.
                if gross + fee <= cash + 1e-9:
                    cash -= gross + fee
                    qty += pending_qty
                    bought_out[t] = gross
                    fills_out[t] = 1
            else:
                fill_qty = min(pending_qty, qty)
                if fill_qty > 0:
                    price = math.floor((base * (1 - slip) + 1e-12) / 0.01) * 0.01
                    gross = price * fill_qty
                    taf = fill_qty * execution.taf_per_share
                    if execution.taf_cap > 0:
                        taf = min(taf, execution.taf_cap)
                    fee = gross * execution.commission_rate + gross * execution.sec_fee_rate + taf
                    cash += gross - fee
                    qty -= fill_qty
                    fills_out[t] = 1
            pending_side = 0
            pending_qty = 0

        close = float(closes[t])
        equity = cash + qty * close
        equity_out[t] = equity
        current = qty * close / equity if equity > 0 else 0.0
        weight_out[t] = current

        target = targets[t]
        if target != target or equity <= 0:  # NaN: no view yet
            continue
        delta = target - current
        if abs(delta) <= band:  # allocation.rebalance.plan_rebalance
            continue
        if delta < 0:
            if qty <= 0:
                continue
            sell_qty = qty if target <= 0 else round(qty * (-delta) / current)
            if sell_qty >= 1:
                pending_side, pending_qty = -1, int(sell_qty)
        else:
            # EngineContext._resolve_qty
            budget = min(equity * delta, cash - equity * execution.min_cash_buffer_pct)
            buy_qty = int(max(0.0, budget) // close)
            if buy_qty == 0:
                continue
            # RiskManager.validate
            if (qty + buy_qty) * close > equity * execution.max_position_pct + 1e-9:
                continue
            if cash - buy_qty * close < equity * execution.min_cash_buffer_pct - 1e-9:
                continue
            pending_side, pending_qty = 1, buy_qty

    return SimResult(equity_out, weight_out, bought_out, fills_out)


def default_grid() -> dict[str, tuple[float, ...]]:
    """The registered search space. Changing it changes what was pre-registered."""
    return {
        "trend_days": (250, 500),
        "integral_days": (120, 250),
        "kp": (0.0, 0.1, 0.2, 0.4),
        "ki": (0.0, 0.1, 0.2, 0.4),
        "kd": (0.0, 0.1, 0.2, 0.4),
        "base_weight": (0.5, 0.75, 1.0),
    }


def grid_configs(grid: dict[str, Sequence[float]]) -> list[tuple[int, ...]]:
    """Every grid position, as indices into each axis."""
    missing = [axis for axis in AXES if axis not in grid]
    if missing:
        raise ValueError(f"Grid is missing axes: {missing}")
    return list(product(*(range(len(grid[axis])) for axis in AXES)))


def params_at(
    position: Sequence[int], grid: dict[str, Sequence[float]], base: PidParams
) -> PidParams:
    values = {axis: grid[axis][index] for axis, index in zip(AXES, position)}
    values["trend_days"] = int(values["trend_days"])
    values["integral_days"] = int(values["integral_days"])
    # A base weight above the cap cannot be held; the cap wins.
    values["base_weight"] = min(float(values["base_weight"]), base.max_weight)
    return replace(base, **values)


@dataclass(frozen=True)
class SweepResult:
    dates: pd.DatetimeIndex
    asset_return: np.ndarray            # close-to-close, aligned with dates
    positions: list[tuple[int, ...]]
    grid: dict[str, tuple[float, ...]]
    base: PidParams
    equity: np.ndarray                  # config x day
    weight: np.ndarray
    bought: np.ndarray
    fills: np.ndarray
    initial_cash: float


def run_grid(
    prices: pd.DataFrame,
    *,
    start: date,
    end: date,
    grid: dict[str, Sequence[float]],
    base: PidParams,
    execution: ExecutionModel,
) -> SweepResult:
    """Simulate every grid position once over [start, end].

    Refuses a frame that extends past `end`. The cut is the caller's to make,
    explicitly, so no code path here can read a row the search was not
    supposed to see.
    """
    if prices.index.max() > pd.Timestamp(end):
        raise ValueError(
            f"Price frame runs to {prices.index.max().date()}, past the search cutoff {end}; "
            "cut it before searching."
        )
    closes_all = prices["close"]
    window = prices.loc[pd.Timestamp(start) :]
    if window.empty:
        raise ValueError(f"No prices on or after {start}")
    offset = len(prices) - len(window)
    opens = window["open"].to_numpy(dtype=float).tolist()
    closes = window["close"].to_numpy(dtype=float).tolist()

    positions = grid_configs(grid)
    shape = (len(positions), len(window))
    equity, weight = np.empty(shape), np.empty(shape)
    bought, fills = np.zeros(shape), np.zeros(shape)

    cache: dict[tuple, list[float]] = {}
    for row, position in enumerate(positions):
        params = params_at(position, grid, base)
        key = _signal_key(params)
        if key not in cache:
            # Targets come from the full history so the trend window is warm
            # on the first simulated day, then are cut to the window.
            cache[key] = target_weights(closes_all, params)["target"].to_numpy()[offset:].tolist()
        result = simulate(opens, closes, cache[key], band=params.band, execution=execution)
        equity[row], weight[row] = result.equity, result.weight
        bought[row], fills[row] = result.bought, result.fills

    asset_return = window["close"].pct_change().fillna(0.0).to_numpy()
    return SweepResult(
        dates=window.index,
        asset_return=asset_return,
        positions=positions,
        grid={axis: tuple(grid[axis]) for axis in AXES},
        base=base,
        equity=equity,
        weight=weight,
        bought=bought,
        fills=fills,
        initial_cash=execution.initial_cash,
    )


def _signal_key(params: PidParams) -> tuple:
    """Parameters that change the target path; an unused window is not one of them."""
    return (
        params.trend_days,
        params.integral_days if params.ki != 0 else None,
        params.derivative_days if params.kd != 0 else None,
        params.kp,
        params.ki,
        params.kd,
        params.base_weight,
        params.min_weight,
        params.max_weight,
        params.z_cap,
    )


def window_metrics(result: SweepResult, start: date, end: date) -> pd.DataFrame:
    """Per-configuration metrics over [start, end], one row per grid position.

    Returns are measured from the close before the window, so a window's
    first day counts; the very first simulated day is measured from the
    starting cash.
    """
    mask = (result.dates >= pd.Timestamp(start)) & (result.dates <= pd.Timestamp(end))
    idx = np.flatnonzero(mask)
    if len(idx) < 2:
        raise ValueError(f"Window {start}..{end} holds fewer than two trading days")
    first, last = idx[0], idx[-1]
    span = slice(first, last + 1)

    if first == 0:
        prior = np.full(len(result.positions), result.initial_cash)
    else:
        prior = result.equity[:, first - 1]
    path = np.concatenate([prior[:, None], result.equity[:, span]], axis=1)
    returns = path[:, 1:] / path[:, :-1] - 1
    asset = result.asset_return[span]
    weight = result.weight[:, span]

    n = returns.shape[1]
    years = (result.dates[last] - result.dates[first]).days / 365.25
    avg_weight = weight.mean(axis=1)
    active = returns - avg_weight[:, None] * asset[None, :]
    active_mean = active.mean(axis=1)
    active_std = active.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        t_stat = np.where(active_std > 0, active_mean / (active_std / math.sqrt(n)), np.nan)
        ret_std = returns.std(axis=1, ddof=1)
        sharpe = np.where(ret_std > 0, returns.mean(axis=1) / ret_std * math.sqrt(TRADING_DAYS), np.nan)
    total = path[:, -1] / path[:, 0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else np.full_like(total, np.nan)
    drawdown = (path / np.maximum.accumulate(path, axis=1) - 1).min(axis=1)
    turnover = (
        result.bought[:, span].sum(axis=1) / result.equity[:, span].mean(axis=1) / years
        if years > 0
        else np.full_like(total, np.nan)
    )

    frame = pd.DataFrame(
        {
            "alpha": active_mean * TRADING_DAYS,
            "alpha_t": t_stat,
            "avg_weight": avg_weight,
            "total_return": total,
            "cagr": cagr,
            "sharpe": sharpe,
            "max_drawdown": drawdown,
            "turnover": turnover,
            "fills": result.fills[:, span].sum(axis=1),
        }
    )
    for column, axis in enumerate(AXES):
        frame.insert(
            column, axis, [result.grid[axis][position[column]] for position in result.positions]
        )
    frame.attrs["days"] = n
    frame.attrs["years"] = years
    return frame


def buy_and_hold_metrics(result: SweepResult, start: date, end: date) -> dict[str, float]:
    """The instrument held outright over the window, costs ignored in its favour."""
    mask = (result.dates >= pd.Timestamp(start)) & (result.dates <= pd.Timestamp(end))
    idx = np.flatnonzero(mask)
    asset = result.asset_return[idx[0] : idx[-1] + 1]
    path = np.concatenate([[1.0], np.cumprod(1 + asset)])
    years = (result.dates[idx[-1]] - result.dates[idx[0]]).days / 365.25
    std = asset.std(ddof=1)
    return {
        "total_return": float(path[-1] - 1),
        "cagr": float(path[-1] ** (1 / years) - 1) if years > 0 else float("nan"),
        "sharpe": float(asset.mean() / std * math.sqrt(TRADING_DAYS)) if std > 0 else float("nan"),
        "max_drawdown": float((path / np.maximum.accumulate(path) - 1).min()),
    }


def _neighbours(position: tuple[int, ...], sizes: Sequence[int]) -> Iterable[tuple[int, ...]]:
    """Grid positions one step away along exactly one axis."""
    for axis, index in enumerate(position):
        for step in (-1, 1):
            moved = index + step
            if 0 <= moved < sizes[axis]:
                yield position[:axis] + (moved,) + position[axis + 1 :]


def plateau_scores(
    metrics: pd.DataFrame, positions: Sequence[tuple[int, ...]], sizes: Sequence[int]
) -> np.ndarray:
    """The worse of a configuration's own alpha and its neighbourhood's median.

    The neighbourhood is the configuration and every grid position one step
    away along one axis. Taking the median alone is not enough: a dull cell
    at the edge of the grid, with one spike for a neighbour, would inherit
    half the spike. The minimum makes a configuration only as good as the
    weaker of itself and its surroundings, so neither a lone spike nor the
    cell beside it can win.
    """
    lookup = {position: row for row, position in enumerate(positions)}
    alpha = metrics["alpha"].to_numpy()
    scores = np.empty(len(positions))
    for row, position in enumerate(positions):
        members = [row] + [lookup[other] for other in _neighbours(position, sizes)]
        scores[row] = min(float(alpha[row]), float(np.median(alpha[members])))
    return scores


@dataclass(frozen=True)
class SelectionRule:
    """Who may be picked. Fixed before any result exists."""

    min_fills: int = 10
    max_turnover: float = 6.0


def select(
    metrics: pd.DataFrame,
    positions: Sequence[tuple[int, ...]],
    sizes: Sequence[int],
    rule: SelectionRule,
) -> int | None:
    """Row of the eligible configuration on the highest plateau, or None.

    None when nothing is eligible. Returning the least-bad ineligible row
    would hand back a controller that barely trades and call it a choice.

    Ties go to the smaller total gain: between two controllers the data
    cannot separate, the one that moves the position less is the one that
    assumes less.
    """
    eligible = (metrics["fills"] >= rule.min_fills) & (metrics["turnover"] <= rule.max_turnover)
    if not eligible.any():
        return None
    scores = plateau_scores(metrics, positions, sizes)
    gain = (metrics["kp"] + metrics["ki"] + metrics["kd"]).to_numpy()
    candidates = np.flatnonzero(eligible.to_numpy())
    order = sorted(candidates, key=lambda row: (-scores[row], gain[row], row))
    return int(order[0])


def _sizes(result: SweepResult) -> tuple[int, ...]:
    return tuple(len(result.grid[axis]) for axis in AXES)


def walk_forward(
    result: SweepResult, windows: Sequence[WalkForwardWindow], rule: SelectionRule
) -> pd.DataFrame:
    """Pick on each training window, score the pick on the window after it.

    The configuration's path is continuous across the boundary: the position
    it carries into the test year is the one its own rule had built by then,
    not a fresh start from cash, which no investor following it would have.
    """
    sizes = _sizes(result)
    rows = []
    for window in windows:
        train = window_metrics(result, window.train_start, window.train_end)
        chosen = select(train, result.positions, sizes, rule)
        row: dict[str, Any] = {
            "train_start": window.train_start,
            "train_end": window.train_end,
            "test_start": window.test_start,
            "test_end": window.test_end,
        }
        if chosen is None:
            row["selected"] = None
            rows.append(row)
            continue
        test = window_metrics(result, window.test_start, window.test_end)
        hold = buy_and_hold_metrics(result, window.test_start, window.test_end)
        row.update(
            {
                "selected": chosen,
                **{axis: train.loc[chosen, axis] for axis in AXES},
                "train_alpha": float(train.loc[chosen, "alpha"]),
                "test_alpha": float(test.loc[chosen, "alpha"]),
                "test_days": int(test.attrs["days"]),
                "test_avg_weight": float(test.loc[chosen, "avg_weight"]),
                "test_return": float(test.loc[chosen, "total_return"]),
                "hold_return": float(hold["total_return"]),
                # What every configuration averaged that year: separates "the
                # pick was good" from "anything on the grid worked that year".
                "test_alpha_grid_median": float(test["alpha"].median()),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def walk_forward_summary(results: pd.DataFrame) -> dict[str, float]:
    """Day-weighted mean test alpha, its t-statistic across windows, and the hit rate."""
    measured = results.dropna(subset=["test_alpha"]) if "test_alpha" in results else results.iloc[0:0]
    if measured.empty:
        return {"windows": 0, "mean_test_alpha": float("nan"), "t_stat": float("nan"), "positive_share": float("nan")}
    alpha = measured["test_alpha"].to_numpy(dtype=float)
    days = measured["test_days"].to_numpy(dtype=float)
    mean = float(np.average(alpha, weights=days))
    spread = float(alpha.std(ddof=1)) if len(alpha) > 1 else float("nan")
    t_stat = mean / (spread / math.sqrt(len(alpha))) if spread and spread > 0 else float("nan")
    return {
        "windows": int(len(alpha)),
        "mean_test_alpha": mean,
        "t_stat": float(t_stat),
        "positive_share": float((alpha > 0).mean()),
    }


def evaluate_window(
    prices: pd.DataFrame,
    params: PidParams,
    *,
    sim_start: date,
    start: date,
    end: date,
    execution: ExecutionModel,
) -> dict[str, Any]:
    """One configuration over [start, end], simulated continuously from `sim_start`.

    The single-configuration path, and the only one allowed past the search
    cutoff. It reports the strategy, the instrument held outright, and the
    mirrored controller — every gain negated, so it buys what this one sells.
    If the mirror does as well, the direction of the rule is not what earned
    the result.
    """
    cut = prices.loc[: pd.Timestamp(end)]
    grid = {
        "trend_days": (params.trend_days,),
        "integral_days": (params.integral_days,),
        "kp": (params.kp, -params.kp),
        "ki": (params.ki, -params.ki),
        "kd": (params.kd, -params.kd),
        "base_weight": (params.base_weight,),
    }
    result = run_grid(cut, start=sim_start, end=end, grid=grid, base=params, execution=execution)
    metrics = window_metrics(result, start, end)
    own = result.positions.index((0, 0, 0, 0, 0, 0))
    mirror = result.positions.index((0, 0, 1, 1, 1, 0))
    return {
        "start": start,
        "end": end,
        "days": int(metrics.attrs["days"]),
        "strategy": {k: float(v) for k, v in metrics.loc[own].items() if k not in AXES},
        "mirror": {k: float(v) for k, v in metrics.loc[mirror].items() if k not in AXES},
        "buy_and_hold": buy_and_hold_metrics(result, start, end),
    }


def decide(
    *,
    in_sample_alpha: float,
    validation_alpha: float,
    validation_mirror_alpha: float,
    walk_forward_mean_alpha: float,
) -> tuple[str, str]:
    """The registered decision rule. Returns (verdict, reason).

    Both keys must turn: the one configuration must carry its sign into
    validation, and the procedure that found it must have paid on average
    when repeated through time. Either alone is easy to get by luck.
    """
    if any(math.isnan(v) for v in (in_sample_alpha, validation_alpha, walk_forward_mean_alpha)):
        return "unmeasured", "A required quantity could not be measured."
    if validation_alpha <= 0:
        return "rejected", "Validation timing alpha is not positive."
    if in_sample_alpha <= 0:
        return "rejected", "The selected configuration had no positive alpha in sample to carry forward."
    if walk_forward_mean_alpha <= 0:
        return "rejected", "Repeating the selection through time did not pay on average."
    if not math.isnan(validation_mirror_alpha) and validation_mirror_alpha >= validation_alpha:
        return "rejected", "The mirrored controller did at least as well, so the rule's direction earned nothing."
    return "consistent", "Sign held in validation, the procedure paid in walk-forward, and the mirror did worse."


def static_mix_metrics(result: SweepResult, start: date, end: date, weight: float) -> dict[str, float]:
    """A fixed `weight` in the instrument, rebalanced daily at no cost.

    The benchmark alpha is measured against, spelled out as a path so its
    return and drawdown can sit beside the strategy's.
    """
    mask = (result.dates >= pd.Timestamp(start)) & (result.dates <= pd.Timestamp(end))
    idx = np.flatnonzero(mask)
    returns = weight * result.asset_return[idx[0] : idx[-1] + 1]
    path = np.concatenate([[1.0], np.cumprod(1 + returns)])
    years = (result.dates[idx[-1]] - result.dates[idx[0]]).days / 365.25
    std = returns.std(ddof=1)
    return {
        "total_return": float(path[-1] - 1),
        "cagr": float(path[-1] ** (1 / years) - 1) if years > 0 else float("nan"),
        "sharpe": float(returns.mean() / std * math.sqrt(TRADING_DAYS)) if std > 0 else float("nan"),
        "max_drawdown": float((path / np.maximum.accumulate(path) - 1).min()),
    }


def term_breakdown(metrics: pd.DataFrame, scores: np.ndarray, eligible: pd.Series) -> pd.DataFrame:
    """Alpha by which of the three terms a configuration actually uses."""
    label = (
        np.where(metrics["kp"] != 0, "P", "")
        + pd.Series(np.where(metrics["ki"] != 0, "I", ""), index=metrics.index)
        + pd.Series(np.where(metrics["kd"] != 0, "D", ""), index=metrics.index)
    ).replace("", "none")
    frame = pd.DataFrame(
        {"terms": label, "alpha": metrics["alpha"], "score": scores, "eligible": eligible}
    )
    frame = frame[frame["eligible"]]
    order = ["P", "I", "D", "PI", "PD", "ID", "PID", "none"]
    table = frame.groupby("terms").agg(
        configs=("alpha", "size"),
        median_alpha=("alpha", "median"),
        positive_share=("alpha", lambda a: float((a > 0).mean())),
        best_score=("score", "max"),
    )
    return table.reindex([name for name in order if name in table.index]).reset_index()


def build_report(
    prices: pd.DataFrame,
    *,
    symbol: str,
    in_sample: tuple[date, date],
    validation: tuple[date, date],
    walk_forward_windows_: Sequence[WalkForwardWindow],
    grid: dict[str, Sequence[float]],
    base: PidParams,
    execution: ExecutionModel,
    rule: SelectionRule,
) -> dict[str, Any]:
    """Run the registered search and judge it. Reads nothing after validation ends."""
    is_start, is_end = in_sample
    val_start, val_end = validation
    cut = prices.loc[: pd.Timestamp(val_end)]
    result = run_grid(cut, start=is_start, end=val_end, grid=grid, base=base, execution=execution)
    sizes = _sizes(result)

    metrics = window_metrics(result, is_start, is_end)
    scores = plateau_scores(metrics, result.positions, sizes)
    eligible = (metrics["fills"] >= rule.min_fills) & (metrics["turnover"] <= rule.max_turnover)
    chosen = select(metrics, result.positions, sizes, rule)

    table = walk_forward(result, walk_forward_windows_, rule)
    wf = walk_forward_summary(table)

    report: dict[str, Any] = {
        "symbol": symbol,
        "data": {
            "first": prices.index.min().date(),
            "search_cutoff": val_end,
            "rows_used": int(len(cut)),
        },
        "in_sample": in_sample,
        "validation": validation,
        "grid": {axis: list(result.grid[axis]) for axis in AXES},
        "fixed": {
            "derivative_days": base.derivative_days,
            "band": base.band,
            "z_cap": base.z_cap,
            "min_weight": base.min_weight,
            "max_weight": base.max_weight,
        },
        "rule": {"min_fills": rule.min_fills, "max_turnover": rule.max_turnover},
        "grid_summary": {
            "configs": int(len(metrics)),
            "eligible": int(eligible.sum()),
            "positive_alpha_share": float((metrics.loc[eligible, "alpha"] > 0).mean()) if eligible.any() else float("nan"),
            "median_alpha": float(metrics.loc[eligible, "alpha"].median()) if eligible.any() else float("nan"),
            "best_alpha": float(metrics.loc[eligible, "alpha"].max()) if eligible.any() else float("nan"),
            "best_alpha_t": float(metrics.loc[eligible, "alpha_t"].max()) if eligible.any() else float("nan"),
        },
        "terms": term_breakdown(metrics, scores, eligible),
        "in_sample_hold": buy_and_hold_metrics(result, is_start, is_end),
        "walk_forward": table,
        "walk_forward_summary": wf,
        "metrics": metrics.assign(score=scores, eligible=eligible),
    }
    if chosen is None:
        report.update({"selected": None, "verdict": "unmeasured", "reason": "No configuration met the eligibility rule in sample."})
        return report

    params = params_at(result.positions[chosen], result.grid, base)
    in_eval = evaluate_window(cut, params, sim_start=is_start, start=is_start, end=is_end, execution=execution)
    val_eval = evaluate_window(cut, params, sim_start=is_start, start=val_start, end=val_end, execution=execution)
    for evaluation, (start, end) in ((in_eval, in_sample), (val_eval, validation)):
        evaluation["static_mix"] = static_mix_metrics(result, start, end, evaluation["strategy"]["avg_weight"])
    verdict, reason = decide(
        in_sample_alpha=in_eval["strategy"]["alpha"],
        validation_alpha=val_eval["strategy"]["alpha"],
        validation_mirror_alpha=val_eval["mirror"]["alpha"],
        walk_forward_mean_alpha=wf["mean_test_alpha"],
    )
    report.update(
        {
            "selected": params,
            "selected_score": float(scores[chosen]),
            "in_sample_eval": in_eval,
            "validation_eval": val_eval,
            "verdict": verdict,
            "reason": reason,
        }
    )
    return report


def _pct(value: float, digits: int = 1) -> str:
    return "측정 불가" if value != value else f"{value * 100:+.{digits}f}%"


def _num(value: float, digits: int = 2) -> str:
    return "측정 불가" if value != value else f"{value:.{digits}f}"


def _comparison_rows(evaluation: dict[str, Any]) -> list[str]:
    strategy, mix, hold, mirror = (
        evaluation["strategy"], evaluation["static_mix"], evaluation["buy_and_hold"], evaluation["mirror"]
    )
    rows = [
        "| 구분 | 연환산 수익 | 최대 낙폭 | 샤프 | 평균 비중 | 타이밍 알파 (t) |",
        "|---|---|---|---|---|---|",
        f"| 전략 | {_pct(strategy['cagr'])} | {_pct(strategy['max_drawdown'])} | {_num(strategy['sharpe'])} "
        f"| {strategy['avg_weight'] * 100:.0f}% | {_pct(strategy['alpha'])} ({_num(strategy['alpha_t'])}) |",
        f"| 같은 평균 비중 고정 보유 | {_pct(mix['cagr'])} | {_pct(mix['max_drawdown'])} | {_num(mix['sharpe'])} "
        f"| {strategy['avg_weight'] * 100:.0f}% | 기준 |",
        f"| 전량 매수 후 보유 | {_pct(hold['cagr'])} | {_pct(hold['max_drawdown'])} | {_num(hold['sharpe'])} | 100% | — |",
        f"| 거울 전략 (부호 반대) | {_pct(mirror['cagr'])} | {_pct(mirror['max_drawdown'])} | {_num(mirror['sharpe'])} "
        f"| {mirror['avg_weight'] * 100:.0f}% | {_pct(mirror['alpha'])} ({_num(mirror['alpha_t'])}) |",
    ]
    return rows


VERDICT_LABELS = {
    "consistent": "일관됨 (채택 후보)",
    "rejected": "기각",
    "unmeasured": "측정 불가",
}


def render_markdown(report: dict[str, Any], out_of_sample: dict[str, Any] | None = None) -> str:
    """The search, what it picked, and whether the pick held up."""
    is_start, is_end = report["in_sample"]
    val_start, val_end = report["validation"]
    summary = report["grid_summary"]
    lines = [
        f"# PID 비중 조절 전략 탐색 — {report['symbol']}",
        "",
        f"**판정: {VERDICT_LABELS.get(report['verdict'], report['verdict'])}** — {report['reason']}",
        "",
        "타이밍 알파는 전략의 일별 수익에서, 같은 기간 평균 비중만큼을 그대로 들고 있었을 때의 수익을",
        "뺀 값(연환산)이다. 비중을 줄이기만 해도 낙폭은 작아지므로, 비중을 *바꾼 것*이 번 돈만 본다.",
        "",
        "## 탐색 범위",
        "",
        f"- 학습 구간 {is_start} ~ {is_end}, 검증 구간 {val_start} ~ {val_end}",
        f"- 가격 이력 {report['data']['first']}부터, 탐색이 읽은 마지막 날 {report['data']['search_cutoff']}",
        f"- 격자: " + ", ".join(f"{axis}={report['grid'][axis]}" for axis in AXES),
        f"- 고정값: " + ", ".join(f"{k}={v}" for k, v in report["fixed"].items()),
        f"- 구성 {summary['configs']}개 중 적격 {summary['eligible']}개 "
        f"(체결 {report['rule']['min_fills']}회 이상, 연 회전율 {report['rule']['max_turnover']:.0f}배 이하)",
        "",
        "## 학습 구간에서 격자 전체가 보인 모습",
        "",
        f"- 알파가 양수인 구성: 적격의 {_pct(summary['positive_alpha_share'], 0).lstrip('+')}",
        f"- 알파 중앙값 {_pct(summary['median_alpha'])}, 최고 {_pct(summary['best_alpha'])} "
        f"(t {_num(summary['best_alpha_t'])})",
        "",
        "수백 개를 돌리면 최고값은 실력이 없어도 높게 나온다. 그래서 최고값이 아니라 이웃까지 고른",
        "값으로 하나를 뽑고, 그 하나를 보지 않은 구간에서 판정한다.",
        "",
        "| 쓰는 항 | 구성 수 | 알파 중앙값 | 양수 비율 | 최고 고원 점수 |",
        "|---|---|---|---|---|",
    ]
    for _, row in report["terms"].iterrows():
        lines.append(
            f"| {row['terms']} | {int(row['configs'])} | {_pct(row['median_alpha'])} "
            f"| {row['positive_share'] * 100:.0f}% | {_pct(row['best_score'])} |"
        )
    lines.append("")

    params = report.get("selected")
    if params is None:
        lines += ["## 선택된 구성", "", "적격 구성이 없어 선택하지 못했다.", ""]
    else:
        lines += [
            "## 선택된 구성",
            "",
            f"- 추세 창 {params.trend_days}일, 적분 창 {params.integral_days}일, 미분 간격 {params.derivative_days}일",
            f"- Kp {params.kp}, Ki {params.ki}, Kd {params.kd}, 기본 비중 {params.base_weight}",
            f"- 고원 점수 {_pct(report['selected_score'])}",
            "",
            f"### 학습 구간 ({is_start} ~ {is_end}) — 이 구간으로 골랐으므로 증거가 아니다",
            "",
            *_comparison_rows(report["in_sample_eval"]),
            "",
            f"### 검증 구간 ({val_start} ~ {val_end}) — 고를 때 보지 않은 구간",
            "",
            *_comparison_rows(report["validation_eval"]),
            "",
        ]

    table = report["walk_forward"]
    wf = report["walk_forward_summary"]
    lines += [
        "## 워크포워드 — '과거로 최적화하고 다음 해에 쓴다'를 반복했을 때",
        "",
        "| 검증 연도 | 뽑힌 구성 (추세/적분/Kp/Ki/Kd/기본) | 학습 알파 | 다음 해 알파 | 그해 격자 중앙값 | 전략 수익 | 보유 수익 |",
        "|---|---|---|---|---|---|---|",
    ]
    for _, row in table.iterrows():
        if row.get("selected") is None or pd.isna(row.get("test_alpha")):
            lines.append(f"| {row['test_start']} ~ {row['test_end']} | 적격 없음 | — | — | — | — | — |")
            continue
        picked = f"{int(row['trend_days'])}/{int(row['integral_days'])}/{row['kp']}/{row['ki']}/{row['kd']}/{row['base_weight']}"
        lines.append(
            f"| {row['test_start'].year} | {picked} | {_pct(row['train_alpha'])} | {_pct(row['test_alpha'])} "
            f"| {_pct(row['test_alpha_grid_median'])} | {_pct(row['test_return'])} | {_pct(row['hold_return'])} |"
        )
    lines += [
        "",
        f"- 측정된 연도 {wf['windows']}개, 다음 해 알파 평균 {_pct(wf['mean_test_alpha'])} "
        f"(연도 간 t {_num(wf['t_stat'])}), 양수였던 해 {_pct(wf['positive_share'], 0).lstrip('+')}",
        "",
    ]

    lines += ["## 표본 외 구간", ""]
    if out_of_sample is None:
        lines += [
            "읽지 않았다. 판정이 '일관됨'일 때만, 선택된 한 구성에 대해 한 번 연다.",
            "",
        ]
    else:
        lines += [
            f"### {out_of_sample['start']} ~ {out_of_sample['end']} — 한 번만 여는 구간",
            "",
            *_comparison_rows(out_of_sample),
            "",
        ]
    return "\n".join(lines)
