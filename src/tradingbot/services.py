from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.broker.fees import FeeModel
from tradingbot.broker.paper import PaperBroker
from tradingbot.config import market_initial_cash, resolve_project_path
from tradingbot.data.cache import ParquetCache
from tradingbot.data.feed import HistoricalDataFeed
from tradingbot.data.polling import PollingDataFeed
from tradingbot.engine.clock import TradingSessionClock
from tradingbot.engine.engine import BacktestEngine, BacktestResult
from tradingbot.engine.paper import PaperTradingEngine
from tradingbot.strategies.state import JsonStateStore
from tradingbot.risk import RiskManager
from tradingbot.strategies.base import Strategy
from tradingbot.strategies.registry import get_strategy


@dataclass
class DataUpdateResult:
    symbol: str
    rows: int
    path: Path


@dataclass
class PaperSession:
    engine: PaperTradingEngine
    broker: PaperBroker
    poll_interval_seconds: int


def build_cache(config: dict[str, Any], data_root: str | Path | None = None) -> ParquetCache:
    root = data_root if data_root else config["data"]["cache_dir"]
    return ParquetCache(resolve_project_path(root))


def build_strategy(config: dict[str, Any], strategy_name: str) -> Strategy:
    strategy_cls = get_strategy(strategy_name)
    strategy_params = config.get("strategies", {}).get(strategy_name, {})
    return strategy_cls(**strategy_params)


def require_initial_cash(config: dict[str, Any], market: str) -> float:
    initial_cash = market_initial_cash(config, market)
    if initial_cash <= 0:
        raise ValueError(f"Initial cash is not configured for market {market}")
    return initial_cash


def update_data(
    config: dict[str, Any],
    *,
    market: str,
    symbols: list[str],
    start: str | None = None,
    end: str | None = None,
    data_root: str | Path | None = None,
) -> list[DataUpdateResult]:
    cache = build_cache(config, data_root)
    results = []
    for symbol in symbols:
        df = cache.update(market, symbol, start=start, end=end)
        results.append(DataUpdateResult(symbol=symbol, rows=len(df), path=cache.path(market, symbol)))
    return results


def run_backtest(
    config: dict[str, Any],
    *,
    market: str,
    symbols: list[str],
    strategy_name: str,
    start: str,
    end: str | None = None,
    data_root: str | Path | None = None,
) -> BacktestResult:
    cache = build_cache(config, data_root)
    strategy = build_strategy(config, strategy_name)
    initial_cash = require_initial_cash(config, market)

    feed = HistoricalDataFeed(
        cache=cache,
        market=market,
        symbols=symbols,
        start=start,
        end=end,
    )
    broker = BacktestBroker(
        initial_cash=initial_cash,
        market=market,
        fee_model=FeeModel.from_config(market, config),
        slippage_bps=float(config.get("execution", {}).get("slippage_bps", 0.0)),
    )
    return BacktestEngine(
        feed=feed,
        broker=broker,
        strategy=strategy,
        risk_manager=RiskManager.from_config(config),
    ).run()


def build_paper_session(
    config: dict[str, Any],
    *,
    name: str,
    market: str,
    symbols: list[str],
    strategy_name: str,
    start: str,
    end: str | None = None,
    data_root: str | Path | None = None,
    state_dir: str | Path | None = None,
) -> PaperSession:
    cache = build_cache(config, data_root)
    strategy = build_strategy(config, strategy_name)
    initial_cash = require_initial_cash(config, market)

    paper_config = config.get("paper", {})
    resolved_state_dir = resolve_project_path(state_dir or paper_config.get("state_dir", "state"))
    poll_interval_seconds = int(paper_config.get("poll_interval_seconds", 300))

    clock = TradingSessionClock(market, poll_interval=timedelta(seconds=poll_interval_seconds))
    history_feed = HistoricalDataFeed(cache, market, symbols, start=start, end=end)
    polling_feed = PollingDataFeed(market, symbols, clock)
    broker = PaperBroker(
        name=name,
        state_dir=resolved_state_dir,
        initial_cash=initial_cash,
        market=market,
        fee_model=FeeModel.from_config(market, config),
        slippage_bps=float(config.get("execution", {}).get("slippage_bps", 0.0)),
    )
    state_store = JsonStateStore(resolved_state_dir / f"{name}.strategy.json")
    engine = PaperTradingEngine(
        history_feed=history_feed,
        polling_feed=polling_feed,
        broker=broker,
        strategy=strategy,
        risk_manager=RiskManager.from_config(config),
        state_store=state_store,
    )
    return PaperSession(engine=engine, broker=broker, poll_interval_seconds=poll_interval_seconds)
