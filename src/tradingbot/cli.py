from __future__ import annotations

import argparse
import math
import sys
import time
from collections import Counter
from datetime import date as _date
from datetime import datetime as _datetime
from typing import Any

from tradingbot.broker.paper import PaperBroker
from tradingbot.config import load_config, resolve_project_path
from tradingbot.env_file import load_env_file
from tradingbot.report.report import generate_backtest_report
from tradingbot.services import build_paper_session, run_backtest, update_data
from tradingbot.strategies.registry import list_strategies
from tradingbot.utils.log import get_logger, setup_logging

LOGGER = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    configure_console()
    setup_logging()
    load_env_file()
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
    pipeline_parser.add_argument(
        "--macro-start",
        type=_date.fromisoformat,
        default=None,
        help="Backfill macro history from YYYY-MM-DD",
    )
    pipeline_parser.add_argument("--processed-root", default=None)
    pipeline_parser.add_argument("--log-root", default=None)
    pipeline_parser.set_defaults(handler=cmd_data_pipeline)

    preflight_parser = data_subparsers.add_parser(
        "preflight", help="Check this host can reach every collection source"
    )
    preflight_parser.add_argument("--data-root", default="data")
    preflight_parser.set_defaults(handler=cmd_data_preflight)

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
    factor_report_parser.add_argument("--data-root", default=None)
    factor_report_parser.add_argument("--out", default="reports/research")
    factor_report_parser.add_argument(
        "--theme", default=None, help="Resolve the universe from config/themes.toml"
    )
    factor_report_parser.set_defaults(handler=cmd_research_report)

    ceiling_parser = research_subparsers.add_parser(
        "ceiling", help="Information-ratio ceiling factor report"
    )
    ceiling_parser.add_argument(
        "--theme", default=None, help="Resolve the universe from config/themes.toml"
    )
    ceiling_parser.add_argument(
        "--factors", nargs="+", default=None, help="Factor names (default: all registered)"
    )
    ceiling_parser.add_argument("--horizon-days", type=int, default=20)
    ceiling_parser.add_argument("--research-config", default=None, help="research.toml path")
    ceiling_parser.add_argument("--data-root", default=None)
    ceiling_parser.add_argument("--out", default="reports/research")
    ceiling_parser.set_defaults(handler=cmd_research_ceiling)

    participation_parser = research_subparsers.add_parser(
        "flow-participation", help="A/B test institutional buying by retail participation"
    )
    participation_parser.add_argument("--symbols", nargs="+", required=True)
    participation_parser.add_argument("--data-root", default="data/cache")
    participation_parser.add_argument("--processed-root", default="data/processed")
    participation_parser.add_argument("--out", default="reports/research/flow_participation")
    participation_parser.add_argument("--cost-bps", type=float, default=30.0)
    participation_parser.set_defaults(handler=cmd_research_flow_participation)

    evaluate_parser = research_subparsers.add_parser(
        "evaluate", help="Measure a strategy against the promotion criteria"
    )
    evaluate_parser.add_argument("--strategy", required=True)
    evaluate_parser.add_argument("--market", choices=["KR", "US"], required=True)
    evaluation_universe = evaluate_parser.add_mutually_exclusive_group(required=True)
    evaluation_universe.add_argument("--symbols", nargs="+")
    evaluation_universe.add_argument(
        "--theme", help="Resolve one recorded universe layer from config/themes.toml"
    )
    evaluate_parser.add_argument(
        "--period",
        choices=["in_sample", "validation", "out_of_sample"],
        required=True,
        help="Named window from config/research.toml [periods]",
    )
    evaluate_parser.add_argument(
        "--promotion-profile",
        required=True,
        help="Named threshold profile from [promotion.<name>] in research.toml",
    )
    evaluate_parser.add_argument(
        "--benchmark-config", default=None, help="Benchmark TOML (default: same as --config)"
    )
    evaluate_parser.add_argument("--research-config", default=None)
    evaluate_parser.add_argument("--data-root", default=None)
    evaluate_parser.add_argument("--out", default="reports/evaluation")
    evaluate_parser.set_defaults(handler=cmd_research_evaluate)

    survivorship_parser = research_subparsers.add_parser(
        "survivorship", help="Measure how much of the past the candidate pool is missing"
    )
    survivorship_parser.add_argument("--start-year", type=int, default=2015)
    survivorship_parser.add_argument("--end-year", type=int, default=None)
    survivorship_parser.add_argument("--data-root", default="data/cache")
    survivorship_parser.add_argument("--out", default="reports/research")
    survivorship_parser.set_defaults(handler=cmd_research_survivorship)

    event_study_parser = research_subparsers.add_parser(
        "event-study", help="Bucket events by pre-runup and reaction; report what followed"
    )
    event_study_parser.add_argument("--market", choices=["KR", "US"], default="US")
    event_study_parser.add_argument("--benchmark", default="SPY")
    event_study_parser.add_argument("--event-kind", default="provisional")
    event_study_parser.add_argument("--quantiles", type=int, default=5)
    event_study_parser.add_argument("--pre-days", type=int, default=60)
    event_study_parser.add_argument("--post-days", type=int, default=20)
    event_study_parser.add_argument("--data-root", default="data/cache")
    event_study_parser.add_argument("--processed-root", default="data/processed")
    event_study_parser.add_argument("--out", default="reports/research")
    event_study_parser.set_defaults(handler=cmd_research_event_study)

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

    briefing_parser = subparsers.add_parser("briefing", help="계좌 현황 브리핑")
    briefing_subparsers = briefing_parser.add_subparsers(dest="briefing_command")
    weekly_parser = briefing_subparsers.add_parser(
        "weekly", help="주간 계좌 브리핑을 만들어 보낸다"
    )
    weekly_parser.add_argument(
        "--dry-run", action="store_true", help="렌더까지만 하고 보내지 않는다"
    )
    weekly_parser.add_argument(
        "--no-notify", action="store_true", help="전송 생략 (--dry-run과 동일)"
    )
    weekly_parser.add_argument(
        "--skip-update", action="store_true", help="가격 캐시 갱신 생략"
    )
    weekly_parser.add_argument(
        "--no-news", action="store_true", help="새 소식 수집 및 표시 생략"
    )
    weekly_parser.add_argument(
        "--no-proposal", action="store_true", help="주간 판단 생성 및 표시 생략"
    )
    weekly_parser.add_argument(
        "--no-reconciliation",
        action="store_true",
        help="실현 수익과 예상 비교 생성 및 표시 생략",
    )
    weekly_parser.set_defaults(handler=cmd_briefing_weekly)

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


def cmd_briefing_weekly(args) -> int:
    """Build the account briefing and send it to the phone.

    The full text is printed whatever happens to delivery: the user is sitting
    at this console, and a briefing that only exists in a failed HTTP request
    helps nobody.
    """
    from tradingbot.briefing_service import build_account_reader, run_briefing
    from tradingbot.data.credentials import MissingCredentialsError
    from tradingbot.data.news import build_fetchers
    from tradingbot.notify.telegram import build_notifier
    from tradingbot.research.experiment import current_git_commit
    from tradingbot.services import build_cache

    config = load_config(args.config)
    notify = not (args.dry_run or args.no_notify)
    state_root = resolve_project_path(config.get("paper", {}).get("state_dir", "state"))

    try:
        reader = build_account_reader(state_root)
        notifier = build_notifier() if notify else None
    except MissingCredentialsError as exc:
        # Not a transient failure: retrying changes nothing, so say what to set
        # rather than reporting a crash.
        print("주간 브리핑을 실행할 준비가 아직 되지 않았습니다.")
        print(f"  {exc}")
        return 1

    news_fetchers = None if args.no_news else build_fetchers()
    proposal_enabled = not args.no_proposal
    ledger_root = resolve_project_path("reports") if proposal_enabled else None
    current_commit = (
        current_git_commit(cwd=resolve_project_path("."))
        if proposal_enabled
        else None
    )
    cache = build_cache(config)

    result = run_briefing(
        config,
        reader=reader,
        notifier=notifier,
        cache=cache,
        state_root=state_root,
        skip_update=args.skip_update,
        notify=notify,
        news=not args.no_news,
        news_fetchers=news_fetchers,
        proposal=proposal_enabled,
        ledger_root=ledger_root,
        current_commit=current_commit,
        reconciliation=not args.no_reconciliation,
    )

    if result.text:
        print(result.text)
        print()
    for message in result.messages:
        print(f"[알림] {message}")
    if result.snapshot_path:
        print(f"계좌 기록을 저장했습니다: {result.snapshot_path}")
    if not notify:
        print("전송은 생략했습니다.")
    elif result.sent:
        print("텔레그램으로 보냈습니다.")
    else:
        print("텔레그램으로 보내지 못했습니다. 위 내용을 이 화면에서 읽어주세요.")
    return 0 if result.ok else 1


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
    from tradingbot.research.dates import month_end_trading_days, research_period
    from tradingbot.research.experiment import record_experiment
    from tradingbot.research.gate import load_gate_thresholds, load_research_config
    from tradingbot.research.report import build_factor_report, render_markdown
    from tradingbot.research.walk_forward import walk_forward_windows

    research = load_research_config(args.research_config)
    thresholds = load_gate_thresholds(research)
    start, end = research_period(research, "in_sample")
    if end is None:  # The canonical in-sample window is always closed.
        raise ValueError("in_sample period must have an end date")

    from tradingbot.data.universe import get_theme, members as theme_members

    theme_key = args.theme or research["universe"]["candidate_theme"]
    theme = get_theme(theme_key)
    market = theme.market
    universe = theme_members(theme, end)
    if not universe:
        print(f"테마 {theme_key}에 {end} 기준 종목이 없습니다.")
        return 1

    store = ParquetDataStore(
        ParquetCache(resolve_project_path(args.data_root or "data/cache")),
        market,
        processed_root=resolve_project_path("data/processed"),
    )
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
        members_on=lambda dt: theme_members(theme, dt),
    )
    report["universe_layer"] = theme.key
    markdown = render_markdown(report)
    print(markdown)

    out_dir = resolve_project_path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{_datetime.now():%Y%m%d_%H%M%S}_{theme.key}_factor_report.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(f"리포트 저장: {out_path}")

    experiment_path = record_experiment(
        resolve_project_path("data/experiments"),
        kind="factor_report",
        params={
            "market": market,
            "universe_layer": theme.key,
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


def cmd_research_ceiling(args) -> int:
    from tradingbot.data.cache import ParquetCache
    from tradingbot.data.store import ParquetDataStore
    from tradingbot.factors import get_factor, list_factors
    from tradingbot.research.ceiling import build_ceiling_report, render_markdown
    from tradingbot.research.dates import month_end_trading_days, research_period
    from tradingbot.research.experiment import record_experiment
    from tradingbot.research.gate import load_research_config

    research = load_research_config(args.research_config)
    start, end = research_period(research, "in_sample")
    if end is None:  # The canonical in-sample window is always closed.
        raise ValueError("in_sample period must have an end date")

    from tradingbot.data.universe import get_theme, members as theme_members

    theme_key = args.theme or research["universe"]["candidate_theme"]
    theme = get_theme(theme_key)
    market = theme.market
    universe = theme_members(theme, end)
    if not universe:
        print(f"테마 {theme_key}에 {end} 기준 종목이 없습니다.")
        return 1

    store = ParquetDataStore(
        ParquetCache(resolve_project_path(args.data_root or "data/cache")),
        market,
        processed_root=resolve_project_path("data/processed"),
    )
    factor_names = args.factors or list_factors()
    factors = [get_factor(name) for name in factor_names]
    dates = month_end_trading_days(market, start, end)

    report = build_ceiling_report(
        store=store,
        market=market,
        universe=universe,
        factors=factors,
        dates=dates,
        horizon_days=args.horizon_days,
        members_on=lambda dt: theme_members(theme, dt),
    )
    report["universe_layer"] = theme.key
    markdown = render_markdown(report)
    print(markdown)

    out_dir = resolve_project_path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{_datetime.now():%Y%m%d_%H%M%S}_{theme.key}_ceiling_report.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(f"리포트 저장: {out_path}")

    experiment_path = record_experiment(
        resolve_project_path("data/experiments"),
        kind="ceiling_report",
        params={
            "market": market,
            "universe_layer": theme.key,
            "universe": universe,
            "factors": factor_names,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "horizon_days": args.horizon_days,
            "nw_lag": report["nw_lag"],
        },
        metrics=report["factors"],
    )
    print(f"실험 기록: {experiment_path}")
    return 0


def cmd_data_preflight(args) -> int:
    from tradingbot.data.preflight import render, run_preflight

    results = run_preflight(data_root=resolve_project_path(args.data_root))
    print(render(results))
    return 0 if all(result.passed for result in results) else 1

def cmd_research_flow_participation(args) -> int:
    import pandas as pd

    from tradingbot.data.cache import ParquetCache
    from tradingbot.data.panel import PanelStore
    from tradingbot.research.flow_participation import analyze_participation_hypothesis

    symbols = [str(symbol).upper() for symbol in args.symbols]
    flows = PanelStore(resolve_project_path(args.processed_root), "flows", "KR").read(
        symbols=symbols
    )
    cache = ParquetCache(resolve_project_path(args.data_root))
    price_frames = []
    for symbol in symbols:
        history = cache.read("KR", symbol).reset_index()
        history = history.rename(columns={history.columns[0]: "date"})
        history["symbol"] = symbol
        price_frames.append(history)
    prices = pd.concat(price_frames, ignore_index=True) if price_frames else pd.DataFrame()
    study = analyze_participation_hypothesis(
        flows,
        prices,
        round_trip_cost_bps=args.cost_bps,
    )

    out_dir = resolve_project_path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{_datetime.now():%Y%m%d_%H%M%S}"
    observations_path = out_dir / f"{stamp}_observations.csv"
    summary_path = out_dir / f"{stamp}_summary.csv"
    study.observations.to_csv(observations_path, index=False, encoding="utf-8-sig")
    study.summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    if study.summary.empty:
        print("분석 가능한 사례가 없습니다. 수급 이력과 가격 이력을 확인하세요.")
    else:
        print(study.summary.to_string(index=False))
    print(f"사례 저장: {observations_path}")
    print(f"요약 저장: {summary_path}")
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
        macro_start=args.macro_start,
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


def _json_safe(value: Any) -> Any:
    """Non-finite floats (NaN/inf) become None: `json.dumps` happily emits
    bare `NaN`/`Infinity` tokens, which Python reads back but `jq` and
    `JSON.parse` reject as invalid JSON. `None` round-trips everywhere."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def cmd_research_event_study(args) -> int:
    """Describe what followed announcements, bucketed two ways.

    Deliberately descriptive: nothing is fitted here, so there is nothing to
    overfit. If no effect shows in this table over a few thousand events, a
    model over forty features would be finding noise.
    """
    from tradingbot.data.cache import ParquetCache
    from tradingbot.data.panel import PanelStore
    from tradingbot.research.event_study import (
        EventWindow,
        build_event_panel,
        quantile_table,
        render_markdown,
    )

    cache = ParquetCache(resolve_project_path(args.data_root))
    events = PanelStore(
        resolve_project_path(args.processed_root), "events", args.market
    ).read()
    if events.empty:
        print("이벤트 패널이 비어 있습니다. 먼저 `data pipeline`으로 수집하세요.")
        return 1

    if "event_kind" in events.columns and args.event_kind:
        events = events[events["event_kind"] == args.event_kind]
    # The reaction date is what everything is measured from. Panels collected
    # before that column existed fall back to the filing date, which misdates
    # every after-close announcement by a session — so say so rather than
    # quietly producing a table nobody can trust.
    if "reaction_date" not in events.columns:
        print("경고: reaction_date가 없는 옛 패널입니다. 접수일로 대체하며, 장 마감 후")
        print("      발표가 하루씩 밀려 측정됩니다. 재수집을 권합니다.")
        events = events.assign(reaction_date=events["date"])
    print(f"이벤트 {len(events):,}건 ({args.event_kind})")

    symbols = sorted(events["symbol"].astype(str).str.upper().unique())
    closes: dict[str, Any] = {}
    for symbol in symbols:
        try:
            closes[symbol] = cache.read(args.market, symbol)["close"].dropna()
        except (FileNotFoundError, KeyError):
            continue
    print(f"가격 확보 {len(closes):,}/{len(symbols):,} 종목")

    try:
        benchmark = cache.read(args.market, args.benchmark)["close"].dropna()
    except (FileNotFoundError, KeyError):
        print(f"벤치마크 {args.benchmark}의 가격이 없습니다. 초과수익을 계산할 수 없습니다.")
        return 1

    window = EventWindow(
        pre_start=-abs(args.pre_days), pre_end=-1, post_start=1, post_end=abs(args.post_days)
    )
    panel = build_event_panel(events, closes, benchmark, window)
    table = quantile_table(panel, n_quantiles=args.quantiles)
    markdown = render_markdown(table, panel)
    print()
    print(markdown)

    out_dir = resolve_project_path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{_datetime.now():%Y%m%d_%H%M%S}_event_study_{args.market}.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(f"이벤트 스터디 리포트: {out_path}")
    return 0


def cmd_research_survivorship(args) -> int:
    """Size the survivorship bias in the candidate pool and write it down.

    The pool is today's listings, so every result computed on it is biased
    toward companies that survived. This does not remove that; it says how
    large it is per year, which is what lets a reader discount the years that
    deserve it.
    """
    from datetime import date as _d

    from tradingbot.data.cik import CikStore
    from tradingbot.data.listings import UsCommonStockListing
    from tradingbot.research.survivorship import (
        render_markdown,
        requests_fetcher,
        survival_by_year,
    )

    cache_root = resolve_project_path(args.data_root)
    tickers = UsCommonStockListing(cache_root).load()
    ciks = CikStore(cache_root).cik_for(tickers)
    if not ciks:
        print("후보 풀의 CIK를 하나도 해석하지 못했습니다. 측정할 수 없습니다.")
        return 1
    print(f"후보 풀 {len(tickers)}종목 중 {len(ciks)}개의 CIK 확보")

    end_year = args.end_year or _d.today().year - 1
    rates = survival_by_year(
        range(args.start_year, end_year + 1), set(ciks.values()), fetcher=requests_fetcher()
    )
    markdown = render_markdown(rates)
    print(markdown)

    out_dir = resolve_project_path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{_datetime.now():%Y%m%d_%H%M%S}_survivorship.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(f"생존율 리포트: {out_path}")
    return 0


def cmd_research_evaluate(args) -> int:
    from datetime import UTC, datetime as _dt

    from tradingbot.research.evaluation import (
        evaluate_strategy,
        promotion_record_from_report,
        render_markdown,
    )
    from tradingbot.research.experiment import current_git_commit, record_experiment
    from tradingbot.research.gate import load_research_config
    from tradingbot.research.dates import research_period
    from tradingbot.research.promotion_ledger import record_promotion

    config = load_config(args.config)
    benchmark_config = (
        load_config(args.benchmark_config) if args.benchmark_config else config
    )
    research = load_research_config(args.research_config)
    period_start, period_end = research_period(research, args.period)
    start = period_start.isoformat()
    end = period_end.isoformat() if period_end else None
    evaluated_at = _dt.now(UTC)
    evaluated_commit = current_git_commit(cwd=resolve_project_path("."))

    universe_layer = None
    symbols = args.symbols
    if args.theme:
        from tradingbot.data.universe import get_theme

        theme = get_theme(args.theme)
        if theme.market != args.market:
            raise ValueError(
                f"Theme {theme.key} is market {theme.market}, not {args.market}"
            )
        for label, selected_config in (
            ("strategy", config),
            ("benchmark", benchmark_config),
        ):
            configured_theme = selected_config["strategies"][args.strategy].get("theme")
            if configured_theme != theme.key:
                raise ValueError(
                    f"{label} config uses theme {configured_theme!r}, not {theme.key!r}"
                )
        universe_layer = theme.key
        symbols = [member.symbol for member in theme.members]

    report = evaluate_strategy(
        config=config,
        benchmark_config=benchmark_config,
        research=research,
        promotion_profile=args.promotion_profile,
        market=args.market,
        symbols=symbols,
        strategy_name=args.strategy,
        start=start,
        end=end,
        data_root=args.data_root,
        config_path=args.config,
        benchmark_config_path=args.benchmark_config,
    )
    if universe_layer:
        report["universe_layer"] = universe_layer
    report["period_name"] = args.period
    markdown = render_markdown(report)
    print(markdown)

    out_dir = resolve_project_path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (
        f"{_dt.now():%Y%m%d_%H%M%S}_{args.strategy}_{args.market.upper()}.md"
    )
    out_path.write_text(markdown, encoding="utf-8")
    print(f"평가 리포트: {out_path}")

    promotion_record = promotion_record_from_report(
        report,
        evaluated_at=evaluated_at,
        commit=evaluated_commit,
        report_path=out_path,
    )
    promotion_path = record_promotion(promotion_record, out_dir.parent)
    print(f"승격 판정 원장: {promotion_path}")

    metrics = {
        "promoted": report["verdict"]["promoted"],
        "unmeasured": report["verdict"]["unmeasured"],
        "excess_return_pct": report["excess_return_pct"],
        "walk_forward_win_rate": report["walk_forward"]["win_rate"],
        "annual_turnover": report["strategy"]["annual_turnover"],
    }
    metrics = {key: _json_safe(value) for key, value in metrics.items()}

    experiment_path = record_experiment(
        resolve_project_path("data/experiments"),
        kind="strategy_evaluation",
        params={
            "strategy": args.strategy,
            "market": args.market,
            "universe_layer": universe_layer,
            "symbols": symbols,
            "promotion_profile": args.promotion_profile,
            "period": args.period,
            "start": start,
            "end": end,
            "benchmark_config": args.benchmark_config,
        },
        metrics=metrics,
    )
    print(f"실험 기록: {experiment_path}")
    return 0 if report["verdict"]["promoted"] else 1
