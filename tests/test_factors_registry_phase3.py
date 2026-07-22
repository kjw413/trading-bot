from __future__ import annotations

from tradingbot.factors import get_factor, list_factors


def test_phase3_factors_are_registered():
    for name in [
        "foreign_net_20d",
        "foreign_net_60d",
        "institution_net_20d",
        "earnings_yield",
        "book_to_market",
    ]:
        assert name in list_factors()
        assert get_factor(name).name == name


def test_momentum_factors_still_registered():
    for name in ["momentum_3m", "momentum_6m", "momentum_12m", "momentum_12m_ex1m"]:
        assert name in list_factors()
