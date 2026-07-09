from __future__ import annotations

from dataclasses import dataclass
from math import inf, sqrt

import pandas as pd

from tradingbot.engine.engine import BacktestResult
from tradingbot.models import Fill, OrderSide


@dataclass(frozen=True)
class ClosedTrade:
    symbol: str
    entry_dt: object
    exit_dt: object
    qty: int
    entry_price: float
    exit_price: float
    pnl: float
    return_pct: float


@dataclass(frozen=True)
class BacktestMetrics:
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe: float
    win_rate_pct: float
    profit_factor: float
    exposure_pct: float
    closed_trades: int


def calculate_metrics(result: BacktestResult) -> tuple[BacktestMetrics, list[ClosedTrade], pd.DataFrame]:
    equity_curve = result.equity_curve.copy()
    metric_curve = _curve_with_initial_point(result)
    if metric_curve.empty:
        drawdown = pd.DataFrame(columns=["date", "drawdown"])
    else:
        metric_curve["equity"] = metric_curve["equity"].astype(float)
        peak = metric_curve["equity"].cummax()
        drawdown = metric_curve[["date"]].copy()
        drawdown["drawdown"] = metric_curve["equity"] / peak - 1

    closed_trades = build_closed_trades(result.fills)
    metrics = BacktestMetrics(
        total_return_pct=result.return_pct,
        cagr_pct=_cagr_pct(result),
        max_drawdown_pct=_max_drawdown_pct(drawdown),
        sharpe=_sharpe(equity_curve),
        win_rate_pct=_win_rate_pct(closed_trades),
        profit_factor=_profit_factor(closed_trades),
        exposure_pct=_exposure_pct(result.fills, equity_curve),
        closed_trades=len(closed_trades),
    )
    return metrics, closed_trades, drawdown


def build_closed_trades(fills: list[Fill]) -> list[ClosedTrade]:
    lots: dict[str, list[dict]] = {}
    trades: list[ClosedTrade] = []
    for fill in fills:
        symbol_lots = lots.setdefault(fill.symbol, [])
        if fill.side is OrderSide.BUY:
            symbol_lots.append(
                {
                    "dt": fill.dt,
                    "qty": fill.qty,
                    "price": fill.price,
                    "fee_per_share": fill.fee / fill.qty if fill.qty else 0.0,
                }
            )
            continue

        remaining = fill.qty
        sell_fee_per_share = fill.fee / fill.qty if fill.qty else 0.0
        while remaining > 0 and symbol_lots:
            lot = symbol_lots[0]
            qty = min(remaining, int(lot["qty"]))
            entry_cost = (float(lot["price"]) + float(lot["fee_per_share"])) * qty
            exit_value = (fill.price - sell_fee_per_share) * qty
            pnl = exit_value - entry_cost
            return_pct = pnl / entry_cost * 100 if entry_cost else 0.0
            trades.append(
                ClosedTrade(
                    symbol=fill.symbol,
                    entry_dt=lot["dt"],
                    exit_dt=fill.dt,
                    qty=qty,
                    entry_price=float(lot["price"]),
                    exit_price=fill.price,
                    pnl=pnl,
                    return_pct=return_pct,
                )
            )
            lot["qty"] -= qty
            remaining -= qty
            if lot["qty"] == 0:
                symbol_lots.pop(0)
    return trades


def closed_trades_frame(trades: list[ClosedTrade]) -> pd.DataFrame:
    return pd.DataFrame([trade.__dict__ for trade in trades])


def _curve_with_initial_point(result: BacktestResult) -> pd.DataFrame:
    curve = result.equity_curve.copy()
    if curve.empty:
        return curve
    first_date = curve["date"].iloc[0]
    initial = pd.DataFrame([{"date": first_date, "equity": float(result.initial_cash)}])
    return pd.concat([initial, curve], ignore_index=True)


def _cagr_pct(result: BacktestResult) -> float:
    curve = result.equity_curve
    if curve.empty or result.initial_cash <= 0 or result.final_equity <= 0:
        return 0.0
    start = pd.to_datetime(curve["date"].iloc[0])
    end = pd.to_datetime(curve["date"].iloc[-1])
    days = max((end - start).days, 1)
    years = days / 365.25
    return ((result.final_equity / result.initial_cash) ** (1 / years) - 1) * 100


def _max_drawdown_pct(drawdown: pd.DataFrame) -> float:
    if drawdown.empty:
        return 0.0
    return float(drawdown["drawdown"].min() * 100)


def _sharpe(equity_curve: pd.DataFrame) -> float:
    if len(equity_curve) < 2:
        return 0.0
    returns = equity_curve["equity"].pct_change().dropna()
    std = returns.std(ddof=1)
    if std == 0 or pd.isna(std):
        return 0.0
    return float(returns.mean() / std * sqrt(252))


def _win_rate_pct(trades: list[ClosedTrade]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for trade in trades if trade.pnl > 0)
    return wins / len(trades) * 100


def _profit_factor(trades: list[ClosedTrade]) -> float:
    gains = sum(trade.pnl for trade in trades if trade.pnl > 0)
    losses = -sum(trade.pnl for trade in trades if trade.pnl < 0)
    if losses == 0:
        return inf if gains > 0 else 0.0
    return gains / losses


def _exposure_pct(fills: list[Fill], equity_curve: pd.DataFrame) -> float:
    if equity_curve.empty:
        return 0.0
    fills_by_date: dict[object, list[Fill]] = {}
    for fill in fills:
        fills_by_date.setdefault(fill.dt, []).append(fill)

    positions: dict[str, int] = {}
    exposed_days = 0
    for dt in equity_curve["date"]:
        for fill in fills_by_date.get(dt, []):
            qty = positions.get(fill.symbol, 0)
            if fill.side is OrderSide.BUY:
                qty += fill.qty
            else:
                qty = max(0, qty - fill.qty)
            if qty:
                positions[fill.symbol] = qty
            else:
                positions.pop(fill.symbol, None)
        if any(qty > 0 for qty in positions.values()):
            exposed_days += 1
    return exposed_days / len(equity_curve) * 100
