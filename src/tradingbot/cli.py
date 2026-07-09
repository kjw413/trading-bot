from __future__ import annotations

import argparse
from pathlib import Path

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.config import (
    load_config,
    market_commission_rate,
    market_initial_cash,
    resolve_project_path,
)
from tradingbot.data.cache import ParquetCache
from tradingbot.data.feed import HistoricalDataFeed
from tradingbot.engine.engine import BacktestEngine
from tradingbot.strategies.registry import get_strategy, list_strategies


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    return args.handler(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tradingbot")
    parser.add_argument("--config", default=None, help="TOML config path")
    subparsers = parser.add_subparsers(dest="command")

    data_parser = subparsers.add_parser("data", help="Data cache commands")
    data_subparsers = data_parser.add_subparsers(dest="data_command")
    update_parser = data_subparsers.add_parser("update", help="Update parquet cache")
    add_market_symbols(update_parser)
    update_parser.add_argument("--start", default=None)
    update_parser.add_argument("--end", default=None)
    update_parser.set_defaults(handler=cmd_data_update)

    backtest_parser = subparsers.add_parser("backtest", help="Run offline backtest")
    add_market_symbols(backtest_parser)
    backtest_parser.add_argument("--strategy", required=True)
    backtest_parser.add_argument("--start", required=True)
    backtest_parser.add_argument("--end", default=None)
    backtest_parser.add_argument("--data-root", default=None)
    backtest_parser.set_defaults(handler=cmd_backtest)

    strategies_parser = subparsers.add_parser("strategies", help="List built-in strategies")
    strategies_parser.set_defaults(handler=cmd_strategies)
    return parser


def add_market_symbols(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--symbols", nargs="+", required=True)


def cache_from_args(args, config: dict) -> ParquetCache:
    root = args.data_root if getattr(args, "data_root", None) else config["data"]["cache_dir"]
    return ParquetCache(resolve_project_path(root))


def cmd_data_update(args) -> int:
    config = load_config(args.config)
    cache = cache_from_args(args, config)
    for symbol in args.symbols:
        df = cache.update(args.market, symbol, start=args.start, end=args.end)
        path = cache.path(args.market, symbol)
        print(f"{args.market} {symbol}: {len(df)} rows -> {path}")
    return 0


def cmd_backtest(args) -> int:
    config = load_config(args.config)
    cache = cache_from_args(args, config)
    strategy_cls = get_strategy(args.strategy)
    strategy_params = config.get("strategies", {}).get(args.strategy, {})
    strategy = strategy_cls(**strategy_params)
    initial_cash = market_initial_cash(config, args.market)
    commission_rate = market_commission_rate(config, args.market)

    feed = HistoricalDataFeed(
        cache=cache,
        market=args.market,
        symbols=args.symbols,
        start=args.start,
        end=args.end,
    )
    broker = BacktestBroker(initial_cash=initial_cash, commission_rate=commission_rate)
    result = BacktestEngine(feed=feed, broker=broker, strategy=strategy).run()

    print(f"전략: {args.strategy}")
    print(f"시장: {args.market}")
    print(f"종목: {', '.join(args.symbols)}")
    print(f"최종 자산: {result.final_equity:,.2f}")
    print(f"수익률: {result.return_pct:,.2f}%")
    print(f"거래수: {result.trade_count}")
    return 0


def cmd_strategies(args) -> int:
    for name in list_strategies():
        print(name)
    return 0
