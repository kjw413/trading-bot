from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from unittest.mock import Mock

import pandas as pd

from tradingbot.cli import build_parser, cmd_research_ceiling
from tradingbot.factors.base import Factor
from tradingbot.research import ceiling
from tradingbot.research.ceiling import build_ceiling_report, render_markdown
from tradingbot.research.horizon import HorizonDiagnostics


SYMBOLS = ["AAA", "BBB", "CCC", "DDD"]


class SyntheticStore:
    def __init__(self) -> None:
        index = pd.date_range("2024-01-01", periods=90, freq="D")
        self.closes = {
            symbol: pd.Series(
                [100.0 + slope * index_position for index_position in range(len(index))],
                index=index,
                dtype=float,
            )
            for slope, symbol in enumerate(SYMBOLS, start=1)
        }

    def close_series(self, symbol: str) -> pd.Series:
        return self.closes[symbol]


class RecordingFactor(Factor):
    name = "recording"

    def __init__(self) -> None:
        self.calls: list[tuple[date, tuple[str, ...]]] = []

    def compute(self, dt, universe, data_store):
        active = tuple(symbol.upper() for symbol in universe)
        self.calls.append((dt, active))
        scores = {symbol: float(position) for position, symbol in enumerate(SYMBOLS, start=1)}
        return pd.Series([scores[symbol] for symbol in active], index=active, name=self.name)


def diagnostics(marker: int) -> HorizonDiagnostics:
    value = float(marker)
    return HorizonDiagnostics(
        n_periods=marker,
        mean_ic=value + 0.1,
        sd_ic=value + 0.2,
        ir=value + 0.3,
        mean_sampling_variance=value + 0.4,
        true_sd=value + 0.5,
        ceiling=value + 0.6,
        signal_variance_exhausted=bool(marker % 2),
        se_ir=value + 0.7,
        ci95=(value + 0.8, value + 0.9),
    )


def evaluation_dates(count: int) -> list[date]:
    start = date(2024, 1, 1)
    return [start + timedelta(days=offset) for offset in range(count)]


def test_build_ceiling_report_splits_odd_dates_with_extra_in_earlier_half(monkeypatch):
    dates = evaluation_dates(5)
    ordered_sizes = [3, 4, 3, 4, 3]
    members_by_date = {
        dt: SYMBOLS[:size] for dt, size in zip(dates, ordered_sizes, strict=True)
    }
    factor = RecordingFactor()
    diagnose = Mock(side_effect=[diagnostics(10), diagnostics(20), diagnostics(30)])
    monkeypatch.setattr(ceiling, "diagnose", diagnose)

    report = build_ceiling_report(
        store=SyntheticStore(),
        market="US",
        universe=SYMBOLS,
        factors=[factor],
        dates=list(reversed(dates)),
        horizon_days=20,
        members_on=members_by_date.__getitem__,
    )

    overall = diagnose.call_args_list[0].args
    earlier = diagnose.call_args_list[1].args
    later = diagnose.call_args_list[2].args
    assert [dt for dt, _ in factor.calls] == dates
    assert overall[1] == ordered_sizes
    assert earlier[0] == overall[0][:3]
    assert earlier[1] == overall[1][:3]
    assert later[0] == overall[0][3:]
    assert later[1] == overall[1][3:]
    assert report["nw_lag"] == 0


def test_build_ceiling_report_splits_even_dates_equally(monkeypatch):
    dates = evaluation_dates(4)
    diagnose = Mock(side_effect=[diagnostics(10), diagnostics(20), diagnostics(30)])
    monkeypatch.setattr(ceiling, "diagnose", diagnose)

    build_ceiling_report(
        store=SyntheticStore(),
        market="US",
        universe=SYMBOLS,
        factors=[RecordingFactor()],
        dates=dates,
        horizon_days=20,
    )

    overall = diagnose.call_args_list[0].args
    earlier = diagnose.call_args_list[1].args
    later = diagnose.call_args_list[2].args
    assert earlier[0] == overall[0][:2]
    assert earlier[1] == overall[1][:2]
    assert later[0] == overall[0][2:]
    assert later[1] == overall[1][2:]


def test_build_ceiling_report_preserves_diagnose_outputs(monkeypatch):
    expected = [diagnostics(10), diagnostics(20), diagnostics(30)]
    monkeypatch.setattr(ceiling, "diagnose", Mock(side_effect=expected))

    report = build_ceiling_report(
        store=SyntheticStore(),
        market="US",
        universe=SYMBOLS,
        factors=[RecordingFactor()],
        dates=evaluation_dates(5),
        horizon_days=20,
    )

    assert report["factors"]["recording"] == {
        "overall": asdict(expected[0]),
        "first_half": asdict(expected[1]),
        "second_half": asdict(expected[2]),
    }


def test_build_ceiling_report_derives_newey_west_lag():
    report = build_ceiling_report(
        store=SyntheticStore(),
        market="US",
        universe=SYMBOLS,
        factors=[RecordingFactor()],
        dates=evaluation_dates(6),
        horizon_days=43,
    )

    assert report["nw_lag"] == 2


def test_render_markdown_contains_ceiling_table_and_lag(monkeypatch):
    monkeypatch.setattr(
        ceiling,
        "diagnose",
        Mock(side_effect=[diagnostics(10), diagnostics(20), diagnostics(30)]),
    )
    report = build_ceiling_report(
        store=SyntheticStore(),
        market="US",
        universe=SYMBOLS,
        factors=[RecordingFactor()],
        dates=evaluation_dates(5),
        horizon_days=20,
    )

    markdown = render_markdown(report)

    assert "| factor |" in markdown
    assert "| recording |" in markdown
    assert "- Newey-West lag: 0" in markdown


def test_cli_parser_wires_research_ceiling():
    parser = build_parser()
    args = parser.parse_args(
        [
            "research",
            "ceiling",
            "--theme",
            "ai_semiconductor",
            "--factors",
            "momentum_3m",
            "--horizon-days",
            "63",
            "--research-config",
            "custom.toml",
            "--data-root",
            "cache",
            "--out",
            "output",
        ]
    )

    assert args.handler is cmd_research_ceiling
    assert args.theme == "ai_semiconductor"
    assert args.factors == ["momentum_3m"]
    assert args.horizon_days == 63
    assert args.research_config == "custom.toml"
    assert args.data_root == "cache"
    assert args.out == "output"


def test_cli_parser_defaults_ceiling_horizon_to_twenty_days():
    args = build_parser().parse_args(["research", "ceiling"])

    assert args.horizon_days == 20
