from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot.allocation.weights import tilt_weights, volatility_target_exposure
from tradingbot.data.cache import ParquetCache
from tradingbot.data.store import ParquetDataStore
from tradingbot.strategies.theme_multifactor import ThemeMultifactorStrategy


AS_OF = date(2024, 6, 28)
HISTORY_DAYS = 70
ROOT = Path(__file__).parents[1]

RESEARCH_TOML = """
[factor_weights]
momentum_3m = 1.0

[risk_limits]
max_position_weight = 0.40
min_cash_weight = 0.02
"""


@pytest.fixture
def research_config(tmp_path):
    path = tmp_path / "research.toml"
    path.write_text(RESEARCH_TOML, encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path):
    return ParquetDataStore(
        ParquetCache(tmp_path / "cache"), "KR", processed_root=tmp_path / "processed"
    )


def write_prices(store, symbol: str, start: float, end: float) -> None:
    closes = list(np.linspace(start, end, HISTORY_DAYS))
    index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=HISTORY_DAYS)
    store.cache.write(
        "KR",
        symbol,
        pd.DataFrame(
            {
                "open": closes,
                "high": [close * 1.01 for close in closes],
                "low": [close * 0.99 for close in closes],
                "close": closes,
                "volume": [1000.0] * HISTORY_DAYS,
            },
            index=index,
        ),
    )


def make_strategy(research_config, **overrides) -> ThemeMultifactorStrategy:
    params = {
        "research_config": str(research_config),
        "top_n": 2,
        "weighting": "equal",
    }
    params.update(overrides)
    return ThemeMultifactorStrategy(**params)


def test_zero_strength_returns_base_weights_exactly():
    base = {"A": 0.5, "B": 0.3, "FILTERED": 0.0, "C": 0.2}
    assert tilt_weights(base, {"A": 3.0, "B": 0.0, "C": -3.0}, 0.0) == base


def test_tilt_is_positive_normalized_monotonic_and_score_clipped():
    base = {"HIGH": 1 / 3, "MID": 1 / 3, "LOW": 1 / 3}
    tilted = tilt_weights(base, {"HIGH": 3.0, "MID": 0.0, "LOW": -3.0}, 0.5)
    beyond_clip = tilt_weights(
        base, {"HIGH": 30.0, "MID": 0.0, "LOW": -30.0}, 0.5
    )

    assert tilted["HIGH"] > tilted["MID"] > tilted["LOW"] > 0
    assert sum(tilted.values()) == pytest.approx(1.0)
    assert beyond_clip == pytest.approx(tilted)


def test_zero_base_weight_stays_zero_and_remainder_is_renormalized():
    tilted = tilt_weights(
        {"A": 0.5, "B": 0.5, "FILTERED": 0.0},
        {"A": 1.0, "B": -1.0, "FILTERED": 3.0},
        0.5,
    )
    assert tilted["FILTERED"] == 0.0
    assert tilted["A"] + tilted["B"] == pytest.approx(1.0)


def test_volatility_target_never_leverages_and_scales_high_volatility():
    assert volatility_target_exposure(0.10, 0.15) == 1.0
    assert volatility_target_exposure(0.30, 0.15) == pytest.approx(0.5)


def test_disabled_switches_reproduce_reference_targets_exactly(store, research_config):
    for symbol, end in (("WIN1", 200.0), ("WIN2", 150.0), ("LOSE", 80.0)):
        write_prices(store, symbol, 100.0, end)

    reference = make_strategy(research_config).generate_targets(
        AS_OF, ["WIN1", "WIN2", "LOSE"], store
    )
    explicitly_disabled = make_strategy(
        research_config,
        selection="top_n",
        tilt_strength=1.0,
        target_vol=0.0,
    ).generate_targets(AS_OF, ["WIN1", "WIN2", "LOSE"], store)

    assert explicitly_disabled == reference


def test_tilt_holds_all_scoreable_names_and_caps_after_tilting(store, research_config):
    symbols = ["BEST", "GOOD", "FLAT", "WEAK"]
    for symbol, end in zip(symbols, (220.0, 170.0, 110.0, 80.0), strict=True):
        write_prices(store, symbol, 100.0, end)

    targets = make_strategy(
        research_config, selection="tilt", tilt_strength=1.0
    ).generate_targets(AS_OF, symbols, store)

    assert set(targets) == set(symbols)
    assert targets["BEST"] >= targets["GOOD"] > targets["FLAT"] > targets["WEAK"]
    assert max(targets.values()) <= 0.40
    assert sum(targets.values()) <= 0.98


def test_absolute_momentum_is_zero_weight_inside_tilt(store, research_config):
    write_prices(store, "RISER1", 100.0, 200.0)
    write_prices(store, "RISER2", 100.0, 170.0)
    write_prices(store, "RISER3", 100.0, 140.0)
    falling = [200.0] * (HISTORY_DAYS - 10) + [100.0] * 10
    index = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=HISTORY_DAYS)
    store.cache.write(
        "KR",
        "FALLER",
        pd.DataFrame(
            {
                "open": falling,
                "high": falling,
                "low": falling,
                "close": falling,
                "volume": [1000.0] * HISTORY_DAYS,
            },
            index=index,
        ),
    )

    targets = make_strategy(
        research_config,
        selection="tilt",
        tilt_strength=0.1,
        abs_momentum_ma_days=60,
    ).generate_targets(AS_OF, ["RISER1", "RISER2", "RISER3", "FALLER"], store)

    assert targets["FALLER"] == 0.0
    assert sum(targets.values()) == pytest.approx(0.98)


def test_positive_target_vol_reduces_high_volatility_exposure(store, research_config):
    symbols = ["A", "B", "C", "D"]
    for symbol, end in zip(symbols, (200.0, 180.0, 160.0, 140.0), strict=True):
        write_prices(store, symbol, 100.0, end)

    unscaled = make_strategy(
        research_config, selection="tilt", tilt_strength=0.0, target_vol=0.0
    ).generate_targets(AS_OF, symbols, store)
    targeted = make_strategy(
        research_config, selection="tilt", tilt_strength=0.0, target_vol=0.005
    ).generate_targets(AS_OF, symbols, store)

    assert sum(targeted.values()) < sum(unscaled.values())
    assert sum(targeted.values()) > 0


def test_candidate_config_exposes_switches_without_narrowing_the_tilt():
    with (ROOT / "config" / "us_etf_rotation.toml").open("rb") as stream:
        config = tomllib.load(stream)

    strategy = config["strategies"]["theme_multifactor"]
    assert strategy["selection"] == "top_n"
    assert strategy["tilt_strength"] == pytest.approx(0.5)
    assert strategy["target_vol"] == 0.0
    assert config["risk"]["max_positions"] == 25
