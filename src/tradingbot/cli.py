from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import date as _date
from datetime import datetime as _datetime

from tradingbot.broker.paper import PaperBroker
from tradingbot.config import load_config, resolve_project_path
from tradingbot.report.report import generate_backtest_report
from tradingbot.services import build_paper_session, run_backtest, update_data
from tradingbot.strategies.registry import list_strategies
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

    pipeline_parser = data_subparsers.add_parser(
        "pipeline", help="Run the daily collection batch (prices, flows, valuation, macro, fundamentals)"
    )
    pipeline_parser.add_argument("--market", choices=["KR", "US"], required=True)
    pipeline_parser.add_argument(
        "--symbols", nargs="+", default=None, help="Override config pipeline.symbols"
    )
    pipeline_parser.add_argument("--processed-root", default=None)
    pipeline_parser.add_argument("--log-root", default=None)
    pipeline_parser.set_defaults(handler=cmd_data_pipeline)

    backtest_parser = subparsers.add_parser("backtest", help="Run offline backtest")
    add_market_symbols(backtest_parser)
    backtest_parser.add_argument("--strategy", required=True)
    backtest_parser.add_argument("--start", required=True)
    backtest_parser.add_argument("--end", default=None)
    backtest_parser.add_argument("--data-root", default=None)
    backtest_parser.add_argument("--reports-root", default="reports")
    backtest_parser.add_argument("--no-report", action="store_true")
    backtest_parser.set_defaults(handler=cmd_backtest)

    paper_parser = subparsers.add_parser("paper", help="Run paper trading once or as a polling loop")
    add_market_symbols(paper_parser)
    paper_parser.add_argument("--name", required=True, help="Paper account state name")
    paper_parser.add_argument("--strategy", required=True)
    paper_parser.add_argument("--start", required=True, help="Warmup history start date")
    paper_parser.add_argument("--end", default=None, help="Optional history end date for reproducible dry runs")
    paper_parser.add_argument("--data-root", default=None)
    paper_parser.add_argument("--state-dir", default=None)
    paper_parser.add_argument("--loop", action="store_true", help="Keep polling until interrupted")
    paper_parser.add_argument("--sleep-seconds", type=int, default=None, help="Sleep interval for --loop")
    paper_parser.set_defaults(handler=cmd_paper)

    strategies_parser = subparsers.add_parser("strategies", help="List built-in strategies")
    strategies_parser.set_defaults(handler=cmd_strategies)

    research_parser = subparsers.add_parser("research", help="Factor research commands")
    research_subparsers = research_parser.add_subparsers(dest="research_command")
    factor_report_parser = research_subparsers.add_parser(
        "report", help="IC / quantile / walk-forward factor report"
    )
    factor_report_parser.add_argument("--research-config", default=None, help="research.toml path")
    factor_report_parser.add_argument(
        "--factors", nargs="+", default=None, help="Factor names (default: all registered)"
    )
    factor_report_parser.add_argument("--start", default=None, help="Evaluation start (default: in_sample_start)")
    factor_report_parser.add_argument("--end", default=None, help="Evaluation end (default: validation_end)")
    factor_report_parser.add_argument("--data-root", default=None)
    factor_report_parser.add_argument("--out", default="reports/research")
    factor_report_parser.set_defaults(handler=cmd_research_report)

    fundamentals_parser = subparsers.add_parser("fundamentals", help="DART fundamentals commands")
    fundamentals_subparsers = fundamentals_parser.add_subparsers(dest="fundamentals_command")
    fund_update_parser = fundamentals_subparsers.add_parser(
        "update", help="Fetch one DART financial report into a point-in-time record"
    )
    fund_update_parser.add_argument("--corp-code", required=True, help="8-digit DART corp_code")
    fund_update_parser.add_argument("--year", type=int, required=True, help="Business year")
    fund_update_parser.add_argument(
        "--report", choices=["annual", "q1", "half", "q3"], default="annual"
    )
    fund_update_parser.add_argument("--market", choices=["KR", "US"], default="KR")
    fund_update_parser.set_defaults(handler=cmd_fundamentals_update)

    gui_parser = subparsers.add_parser("gui", help="Launch the desktop GUI")
    gui_parser.set_defaults(handler=cmd_gui)
    return parser


def add_market_symbols(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--symbols", nargs="+", required=True)


def cmd_data_update(args) -> int:
    config = load_config(args.config)
    for result in update_data(
        config,
        market=args.market,
        symbols=args.symbols,
        start=args.start,
        end=args.end,
        data_root=args.data_root if hasattr(args, "data_root") else None,
    ):
        print(f"{args.market} {result.symbol}: {result.rows} rows -> {result.path}")
    return 0


def cmd_backtest(args) -> int:
    config = load_config(args.config)
    result = run_backtest(
        config,
        market=args.market,
        symbols=args.symbols,
        strategy_name=args.strategy,
        start=args.start,
        end=args.end,
        data_root=args.data_root,
    )

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


def cmd_paper(args) -> int:
    config = load_config(args.config)
    session = build_paper_session(
        config,
        name=args.name,
        market=args.market,
        symbols=args.symbols,
        strategy_name=args.strategy,
        start=args.start,
        end=args.end,
        data_root=args.data_root,
        state_dir=args.state_dir,
    )
    engine = session.engine
    broker = session.broker
    sleep_seconds = int(args.sleep_seconds or session.poll_interval_seconds)

    if args.loop:
        print(f"모의투자 루프 시작: {args.name}")
        print(f"상태 파일: {broker.state_path}")
        try:
            while True:
                try:
                    snapshot = engine.run_once()
                    print_paper_snapshot(args, broker, snapshot, compact=True)
                except Exception as exc:
                    LOGGER.exception("Paper loop iteration failed; continuing")
                    print(f"모의투자 루프 오류: {exc}")
                time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            print("모의투자 루프 종료")
            return 130

    snapshot = engine.run_once()
    print_paper_snapshot(args, broker, snapshot)
    return 0


def print_paper_snapshot(args, broker: PaperBroker, snapshot: dict[str, object], *, compact: bool = False) -> None:
    actions = snapshot.get("actions", [])
    action_text = ", ".join(str(action) for action in actions) if actions else "none"
    if compact:
        print(
            f"[{snapshot['now']}] actions={action_text} "
            f"cash={snapshot['cash']:,.2f} equity={snapshot['equity']:,.2f} "
            f"open_orders={snapshot['open_orders']}"
        )
        return

    positions = snapshot.get("positions", {})
    if isinstance(positions, dict) and positions:
        position_text = ", ".join(f"{symbol}:{qty}" for symbol, qty in sorted(positions.items()))
    else:
        position_text = "없음"

    print(f"모의투자: {args.name}")
    print(f"전략: {args.strategy}")
    print(f"시장: {args.market}")
    print(f"종목: {', '.join(args.symbols)}")
    print(f"상태 파일: {broker.state_path}")
    print(f"시각: {snapshot['now']}")
    print(f"동작: {action_text}")
    print(f"현금: {snapshot['cash']:,.2f}")
    print(f"평가자산: {snapshot['equity']:,.2f}")
    print(f"포지션: {position_text}")
    print(f"미체결 주문: {snapshot['open_orders']}")
    print(f"거부 주문: {len(broker.rejected_orders)}")
    for reason, count in Counter(order.reject_reason or "unknown" for order in broker.rejected_orders).items():
        print(f"  - {reason}: {count}")
    print(f"만료 주문: {len(broker.expired_orders)}")


def cmd_strategies(args) -> int:
    for name in list_strategies():
        print(name)
    return 0


def cmd_gui(args) -> int:
    from tradingbot.gui import run_gui

    return run_gui(config_path=args.config)


def cmd_fundamentals_update(args) -> int:
    from datetime import date as _d

    from tradingbot.data.fundamentals import (
        REPORT_CODES,
        DartClient,
        api_key_from_env,
        fetch_fundamental_record,
        requests_transport,
    )

    client = DartClient(api_key=api_key_from_env(), transport=requests_transport())
    record = fetch_fundamental_record(
        client,
        args.corp_code,
        args.year,
        REPORT_CODES[args.report],
        args.market,
        # Wide window: reports for a business year are filed within the next year.
        search_start=_d(args.year, 1, 1),
        search_end=_d(args.year + 1, 6, 30),
    )
    print(f"기업: {record.corp_code} ({record.currency})")
    print(f"보고서 기준일: {record.report_period}")
    print(f"공시일: {record.announcement_date}  사용가능일(available_at): {record.available_at}")
    print(f"매출액: {record.revenue}")
    print(f"영업이익: {record.operating_income}")
    print(f"감가상각: {record.depreciation_amortization}")
    print(f"CAPEX: {record.capex}")
    print(f"순차입금: {record.net_debt}")
    return 0


def cmd_research_report(args) -> int:
    from tradingbot.data.cache import ParquetCache
    from tradingbot.data.store import ParquetDataStore
    from tradingbot.factors import get_factor, list_factors
    from tradingbot.research.dates import month_end_trading_days
    from tradingbot.research.experiment import record_experiment
    from tradingbot.research.gate import load_gate_thresholds, load_research_config
    from tradingbot.research.report import build_factor_report, render_markdown
    from tradingbot.research.walk_forward import walk_forward_windows

    research = load_research_config(args.research_config)
    market = research["universe"]["market"]
    universe = research["universe"]["symbols"]
    thresholds = load_gate_thresholds(research)
    periods = research["periods"]
    start = _date.fromisoformat(args.start or periods["in_sample_start"])
    end = _date.fromisoformat(args.end or periods["validation_end"])

    store = ParquetDataStore(ParquetCache(resolve_project_path(args.data_root or "data/cache")), market)
    factor_names = args.factors or list_factors()
    factors = [get_factor(name) for name in factor_names]
    dates = month_end_trading_days(market, start, end)
    wf_config = research["walk_forward"]
    windows = walk_forward_windows(
        start,
        end,
        train_years=int(wf_config["train_years"]),
        test_years=int(wf_config["test_years"]),
        step_years=int(wf_config["step_years"]),
    )

    report = build_factor_report(
        store=store,
        market=market,
        universe=universe,
        factors=factors,
        dates=dates,
        windows=windows,
        thresholds=thresholds,
    )
    markdown = render_markdown(report)
    print(markdown)

    out_dir = resolve_project_path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{_datetime.now():%Y%m%d_%H%M%S}_factor_report.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(f"리포트 저장: {out_path}")

    experiment_path = record_experiment(
        resolve_project_path("data/experiments"),
        kind="factor_report",
        params={
            "market": market,
            "universe": universe,
            "factors": factor_names,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "horizon_days": thresholds.horizon_days,
            "n_quantiles": thresholds.n_quantiles,
        },
        metrics={
            name: data["ic"] | {"gate_passed": data["gate"]["passed"]}
            for name, data in report["factors"].items()
        },
    )
    print(f"실험 기록: {experiment_path}")
    return 0


def cmd_data_pipeline(args) -> int:
    from tradingbot.data.pipeline import run_pipeline

    config = load_config(args.config)
    result = run_pipeline(
        config,
        market=args.market,
        symbols=args.symbols,
        processed_root=args.processed_root,
        log_root=args.log_root,
    )

    print(f"데이터 수집 배치: {result.market}")
    for source in result.results:
        label = {"ok": "성공", "failed": "실패", "skipped": "생략"}.get(source.status, source.status)
        line = f"  - {source.name}: {label} ({source.rows}행)"
        if source.message:
            line += f" — {source.message}"
        print(line)
    print(f"전체 결과: {'정상' if result.ok else '일부 실패'}")
    return 0 if result.ok else 1
