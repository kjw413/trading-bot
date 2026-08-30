from __future__ import annotations

import re
import tomllib
from datetime import date
from pathlib import Path

import pandas as pd

from tradingbot.cli import build_parser
from tradingbot.data.universe import get_theme, members
from tradingbot.factors.base import Factor
from tradingbot.research.gate import load_research_config
from tradingbot.research.report import MembershipAwareFactor


ROOT = Path(__file__).parents[1]

REFERENCE_SYMBOLS = {
    "SPY",
    "QQQ",
    "IWM",
    "EFA",
    "EEM",
    "TLT",
    "IEF",
    "LQD",
    "GLD",
    "DBC",
    "VNQ",
}

EXPANSION_SYMBOLS = {
    "EWJ",
    "IJH",
    "IJR",
    "XLE",
    "XLF",
    "XLK",
    "XLV",
    "XLU",
    "XLP",
    "TIP",
    "SHY",
    "HYG",
    "SLV",
    "USO",
}


def test_research_periods_have_one_authoritative_source():
    owners = []
    for path in sorted((ROOT / "config").glob("*.toml")):
        with path.open("rb") as stream:
            if "periods" in tomllib.load(stream):
                owners.append(path.relative_to(ROOT).as_posix())

    assert owners == ["config/research.toml"]
    assert load_research_config()["periods"] == {
        "in_sample_start": "2007-01-01",
        "in_sample_end": "2018-12-31",
        "validation_start": "2019-01-01",
        "validation_end": "2021-12-31",
        "out_of_sample_start": "2022-01-01",
    }

    spec = (ROOT / "docs" / "quant_research_spec.md").read_text(encoding="utf-8")
    period_section = spec.split("## 4. 실험 기간 구분", 1)[1].split("## 5.", 1)[0]
    assert re.search(r"\b20\d{2}-\d{2}-\d{2}\b", period_section) is None

    args = build_parser().parse_args(["research", "report"])
    assert not hasattr(args, "start")
    assert not hasattr(args, "end")

    evaluation = build_parser().parse_args(
        [
            "research",
            "evaluate",
            "--promotion-profile",
            "default",
            "--strategy",
            "theme_multifactor",
            "--market",
            "US",
            "--theme",
            "us_asset_rotation",
            "--period",
            "out_of_sample",
        ]
    )
    assert evaluation.theme == "us_asset_rotation"
    assert evaluation.symbols is None
    assert evaluation.period == "out_of_sample"


def test_expanded_member_is_excluded_before_its_inception_date():
    candidate = get_theme("us_asset_rotation")
    universe = [member.symbol for member in candidate.members]

    class EchoFactor(Factor):
        name = "echo"

        def compute(self, dt, active, data_store):
            return pd.Series(1.0, index=active, name=self.name)

    factor = MembershipAwareFactor(EchoFactor(), lambda dt: members(candidate, dt))

    assert "HYG" not in members(candidate, date(2007, 4, 10))
    assert "HYG" in members(candidate, date(2007, 4, 11))
    assert pd.isna(factor.compute(date(2007, 4, 10), universe, object()).loc["HYG"])
    assert factor.compute(date(2007, 4, 11), universe, object()).loc["HYG"] == 1.0


def test_candidate_and_reference_universe_layers_stay_separate():
    candidate = get_theme("us_asset_rotation")
    reference = get_theme("us_asset_rotation_reference")
    candidate_symbols = {member.symbol for member in candidate.members}
    reference_symbols = {member.symbol for member in reference.members}

    assert reference_symbols == REFERENCE_SYMBOLS
    assert candidate_symbols == REFERENCE_SYMBOLS | EXPANSION_SYMBOLS
    assert candidate_symbols - reference_symbols == EXPANSION_SYMBOLS

    research_universe = load_research_config()["universe"]
    assert research_universe["candidate_theme"] == candidate.key
    assert research_universe["reference_theme"] == reference.key

    for filename in ("us_etf_rotation.toml", "us_etf_benchmark.toml"):
        with (ROOT / "config" / filename).open("rb") as stream:
            config = tomllib.load(stream)
        assert config["strategies"]["theme_multifactor"]["theme"] == candidate.key
        assert set(config["pipeline"]["symbols"]) == candidate_symbols

    with (ROOT / "config" / "us_etf_benchmark.toml").open("rb") as stream:
        benchmark = tomllib.load(stream)
    assert benchmark["strategies"]["theme_multifactor"]["top_n"] == len(candidate_symbols)
    assert benchmark["risk"]["max_positions"] == len(candidate_symbols)
