from __future__ import annotations

import pytest

from tradingbot.research.gate import (
    GateThresholds,
    evaluate_gate,
    load_gate_thresholds,
    load_research_config,
)
from tradingbot.research.ic import ICSummary

THRESHOLDS = GateThresholds(
    horizon_days=20, n_quantiles=5, min_ic_mean=0.01, min_ic_ir=0.30, min_monotonicity=0.60
)


def make_summary(mean: float, ir: float) -> ICSummary:
    return ICSummary(mean=mean, std=0.05, ir=ir, positive_share=0.6, n_periods=24)


def test_gate_passes_when_all_thresholds_met():
    result = evaluate_gate(make_summary(0.02, 0.40), 0.75, THRESHOLDS)
    assert result.passed
    assert result.reasons == []


def test_gate_fails_low_ir_with_reason():
    result = evaluate_gate(make_summary(0.02, 0.10), 0.75, THRESHOLDS)
    assert not result.passed
    assert any("ic_ir" in reason for reason in result.reasons)


def test_gate_nan_metrics_fail_all_checks():
    nan = float("nan")
    result = evaluate_gate(ICSummary(nan, nan, nan, nan, 0), nan, THRESHOLDS)
    assert not result.passed
    assert len(result.reasons) == 3


def test_load_gate_thresholds_from_repo_config():
    thresholds = load_gate_thresholds(load_research_config())
    assert thresholds.n_quantiles >= 2
    assert 0 < thresholds.min_monotonicity <= 1


def test_load_research_config_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_research_config(tmp_path / "nope.toml")
