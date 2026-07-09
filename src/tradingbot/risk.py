from __future__ import annotations

from dataclasses import dataclass

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.models import Bar, Order, OrderSide


@dataclass(frozen=True)
class RiskLimits:
    max_position_pct: float = 0.20
    max_positions: int = 5
    max_daily_loss_pct: float = 0.03
    stop_loss_pct: float = 0.05
    min_cash_buffer_pct: float = 0.02

    @classmethod
    def from_config(cls, config: dict) -> "RiskLimits":
        risk = config.get("risk", {})
        return cls(
            max_position_pct=float(risk.get("max_position_pct", cls.max_position_pct)),
            max_positions=int(risk.get("max_positions", cls.max_positions)),
            max_daily_loss_pct=float(risk.get("max_daily_loss_pct", cls.max_daily_loss_pct)),
            stop_loss_pct=float(risk.get("stop_loss_pct", cls.stop_loss_pct)),
            min_cash_buffer_pct=float(risk.get("min_cash_buffer_pct", cls.min_cash_buffer_pct)),
        )


class RiskManager:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()
        self.day_start_equity: float | None = None
        self.block_new_entries = False

    @classmethod
    def from_config(cls, config: dict) -> "RiskManager":
        return cls(RiskLimits.from_config(config))

    def start_day(self, equity: float) -> None:
        self.day_start_equity = equity
        self.block_new_entries = False

    def update_daily_loss(self, equity: float) -> None:
        if self.day_start_equity is None or self.day_start_equity <= 0:
            return
        loss_pct = equity / self.day_start_equity - 1
        if loss_pct <= -self.limits.max_daily_loss_pct:
            self.block_new_entries = True

    def validate(self, order: Order, broker: BacktestBroker, estimated_price: float) -> str | None:
        if order.side is OrderSide.SELL:
            return None
        if self.block_new_entries:
            return "daily loss limit reached"

        position = broker.position(order.symbol)
        open_positions = sum(1 for pos in broker.portfolio.positions.values() if pos.qty > 0)
        if position.qty == 0 and open_positions >= self.limits.max_positions:
            return "max positions exceeded"

        equity = broker.equity
        if equity <= 0:
            return "equity is not positive"

        projected_value = (position.qty + order.qty) * estimated_price
        if projected_value > equity * self.limits.max_position_pct + 1e-9:
            return "max position size exceeded"

        gross = order.qty * estimated_price
        min_cash = equity * self.limits.min_cash_buffer_pct
        if broker.cash - gross < min_cash - 1e-9:
            return "minimum cash buffer breached"
        return None

    def stop_loss_symbols(self, broker: BacktestBroker, bars: dict[str, Bar]) -> list[str]:
        symbols: list[str] = []
        if self.limits.stop_loss_pct <= 0:
            return symbols
        for symbol, position in broker.portfolio.positions.items():
            bar = bars.get(symbol)
            if bar is None or position.qty <= 0 or position.avg_price <= 0:
                continue
            if bar.close <= position.avg_price * (1 - self.limits.stop_loss_pct):
                symbols.append(symbol)
        return symbols
