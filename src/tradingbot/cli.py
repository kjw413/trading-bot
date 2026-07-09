from __future__ import annotations

import argparse
import sys
from collections import Counter

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.broker.fees import FeeModel
from tradingbot.config import load_config, market_initial_cash, resolve_project_path
from tradingbot.data.cache import ParquetCache
from tradingbot.data.feed import HistoricalDataFeed
from tradingbot.engine.engine import BacktestEngine
from tradingbot.report.report import generate_backtest_report
from tradingbot.risk import RiskManager
from tradingbot.strategies.registry import get_strategy, list_strategies
from tradingbot.utils.log import get_logger, setup_logging

LOGGER = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    configure_console()
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    return args.handler(args)


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


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
    backtest_parser.add_argument("--reports-root", default="reports")
    backtest_parser.add_argument("--no-report", action="store_true")
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
    if initial_cash <= 0:
        raise ValueError(f"Initial cash is not configured for market {args.market}")

    fee_model = FeeModel.from_config(args.market, config)
    slippage_bps = float(config.get("execution", {}).get("slippage_bps", 0.0))
    risk_manager = RiskManager.from_config(config)
    feed = HistoricalDataFeed(
        cache=cache,
        market=args.market,
        symbols=args.symbols,
        start=args.start,
        end=args.end,
    )
    broker = BacktestBroker(
        initial_cash=initial_cash,
        market=args.market,
        fee_model=fee_model,
        slippage_bps=slippage_bps,
    )
    result = BacktestEngine(
        feed=feed,
        broker=broker,
        strategy=strategy,
        risk_manager=risk_manager,
    ).run()

    print(f"전략: {args.strategy}")
    print(f"시장: {args.market}")
    print(f"종목: {', '.join(args.symbols)}")
    print(f"최종 자산: {result.final_equity:,.2f}")
    print(f"수익률: {result.return_pct:,.2f}%")
    print(f"체결수: {result.trade_count}")
    print(f"거부 주문: {len(result.rejected_orders)}")
    for reason, count in Counter(order.reject_reason or "unknown" for order in result.rejected_orders).items():
        print(f"  - {reason}: {count}")
        LOGGER.warning("Rejected orders: %s = %s", reason, count)
    print(f"만료 주문: {len(result.expired_orders)}")

    if not args.no_report:
        report_path = generate_backtest_report(
            result,
            strategy_name=args.strategy,
            market=args.market,
            symbols=args.symbols,
            reports_root=resolve_project_path(args.reports_root),
        )
        print(f"리포트: {report_path}")
    return 0


def cmd_strategies(args) -> int:
    for name in list_strategies():
        print(name)
    return 0
