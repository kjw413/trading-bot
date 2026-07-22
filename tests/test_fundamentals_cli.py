from __future__ import annotations

from tradingbot.cli import build_parser, cmd_fundamentals_update


def test_parser_wires_fundamentals_update():
    parser = build_parser()
    args = parser.parse_args(
        ["fundamentals", "update", "--corp-code", "00126380", "--year", "2023", "--report", "annual"]
    )
    assert args.handler is cmd_fundamentals_update
    assert args.corp_code == "00126380"
    assert args.year == 2023
    assert args.report == "annual"
    assert args.market == "KR"  # default


def test_report_choices_restricted():
    parser = build_parser()
    args = parser.parse_args(
        ["fundamentals", "update", "--corp-code", "x", "--year", "2023", "--report", "q3"]
    )
    assert args.report == "q3"
