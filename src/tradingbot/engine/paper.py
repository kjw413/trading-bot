from __future__ import annotations

from datetime import date, datetime
import time

import pandas as pd

from tradingbot.broker.paper import PaperBroker
from tradingbot.data.feed import HistoricalDataFeed
from tradingbot.data.polling import PollingDataFeed
from tradingbot.engine.engine import EngineContext
from tradingbot.models import Bar, Fill, OrderPhase
from tradingbot.risk import RiskManager
from tradingbot.strategies.base import Strategy
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)


class PaperTradingEngine:
    def __init__(
        self,
        history_feed: HistoricalDataFeed,
        polling_feed: PollingDataFeed,
        broker: PaperBroker,
        strategy: Strategy,
        risk_manager: RiskManager | None = None,
    ) -> None:
        self.history_feed = history_feed
        self.polling_feed = polling_feed
        self.clock = polling_feed.clock
        self.broker = broker
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.context = EngineContext(
            feed=history_feed,
            broker=broker,
            risk_manager=risk_manager,
            order_start=broker.next_order_number(),
        )
        self.strategy.init(self.context)

    def run_once(self, now: datetime | None = None) -> dict[str, object]:
        current = self.clock.localize(now) if now is not None else self.clock.now()
        actions: list[str] = []
        self.context.set_datetime(current.date())

        if self.clock.is_session_open(current):
            if self.broker.metadata.get("last_open_date") != current.date().isoformat():
                prices = self.polling_feed.fetch_prices()
                if prices:
                    self._handle_open(current, prices)
                    actions.append("open")

            tick = self.polling_feed.poll(current)
            if tick is not None:
                bars = _bars_from_prices(tick.dt, tick.prices)
                self._handle_intraday(current, bars)
                actions.append("poll")

        elif self.clock.is_after_close(current):
            if self.broker.metadata.get("last_close_date") != current.date().isoformat():
                bars, source = self._close_bars(current.date())
                if bars:
                    self._handle_close(current, bars)
                    actions.append("close" if source == "confirmed" else "close_fallback")

        return {
            "now": current.isoformat(),
            "actions": actions,
            "cash": self.broker.cash,
            "equity": self.broker.equity,
            "positions": {symbol: pos.qty for symbol, pos in self.broker.portfolio.positions.items()},
            "open_orders": len(self.broker.open_orders()),
        }

    def run_loop(self, sleep_seconds: int = 300) -> None:
        while True:
            try:
                self.run_once()
            except Exception:
                LOGGER.exception("Paper trading loop iteration failed; continuing")
            time.sleep(sleep_seconds)

    def _handle_open(self, current: datetime, opens: dict[str, float]) -> None:
        self.context.set_phase(OrderPhase.OPEN)
        self.context.set_opens(opens)
        fills = self.broker.on_session_open(current.date(), opens)
        self.broker.mark_to_market(opens)
        self._notify_fills(fills)
        if self.risk_manager is not None:
            self.risk_manager.start_day(self.broker.equity)
        self.strategy.on_open(self.context, current.date(), opens)
        self.broker.set_metadata("last_open_date", current.date().isoformat())

    def _handle_intraday(self, current: datetime, bars: dict[str, Bar]) -> None:
        self.context.set_phase(OrderPhase.INTRADAY)
        self.context.set_bars(bars)
        prices = {symbol: bar.close for symbol, bar in bars.items()}
        self.broker.mark_to_market(prices)
        fills = self.broker.on_intraday_bars(current.date(), bars)
        self._notify_fills(fills)

    def _handle_close(self, current: datetime, bars: dict[str, Bar]) -> None:
        self.context.set_bars(bars)
        prices = {symbol: bar.close for symbol, bar in bars.items()}
        self.broker.mark_to_market(prices)

        self.context.set_phase(OrderPhase.MOC)
        fills = self.broker.on_session_close(current.date(), bars)
        self._notify_fills(fills)
        self.broker.expire_day_orders(current.date())
        self.broker.mark_to_market(prices)

        if self.risk_manager is not None:
            self.risk_manager.update_daily_loss(self.broker.equity)

        self.context.set_phase(OrderPhase.CLOSE)
        for symbol in self.history_feed.symbols:
            bar = bars.get(symbol)
            if bar is not None:
                self.strategy.on_bar(self.context, bar)

        if self.risk_manager is not None:
            for symbol in self.risk_manager.stop_loss_symbols(self.broker, bars):
                position = self.broker.position(symbol)
                if position.qty > 0 and not self.context.has_open_order(symbol, side="SELL"):
                    self.context.sell(symbol, qty=position.qty)

        self.broker.set_metadata("last_close_date", current.date().isoformat())

    def _close_bars(self, dt: date) -> tuple[dict[str, Bar], str]:
        try:
            return self._confirmed_close_bars(dt), "confirmed"
        except Exception:
            LOGGER.exception("Failed to refresh confirmed daily bars; using polling snapshot fallback")
            prices = self.polling_feed.fetch_prices()
            if not prices:
                LOGGER.warning("No polling prices available for close fallback")
                return {}, "none"
            return _bars_from_prices(dt, prices), "fallback"

    def _confirmed_close_bars(self, dt: date) -> dict[str, Bar]:
        for symbol in self.history_feed.symbols:
            self.history_feed.cache.update(self.history_feed.market, symbol, start=dt, end=dt)
        self.history_feed.reload()

        bars: dict[str, Bar] = {}
        missing: list[str] = []
        key = pd.Timestamp(dt)
        for symbol in self.history_feed.symbols:
            df = self.history_feed.frames.get(symbol)
            if df is None or key not in df.index:
                missing.append(symbol)
                continue
            row = df.loc[key]
            bars[symbol] = Bar(
                symbol=symbol,
                dt=dt,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
        if missing:
            raise ValueError(f"Confirmed daily bars are missing for: {', '.join(missing)}")
        return bars

    def _notify_fills(self, fills: list[Fill]) -> None:
        for fill in fills:
            self.strategy.on_fill(self.context, fill)


def _bars_from_prices(dt, prices: dict[str, float]) -> dict[str, Bar]:
    return {
        symbol: Bar(symbol=symbol, dt=dt, open=price, high=price, low=price, close=price, volume=0.0)
        for symbol, price in prices.items()
    }
