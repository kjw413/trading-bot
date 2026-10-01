from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from tradingbot.proxy import CostState, ProxyMeasurement, ProxyStatus
from tradingbot.reconciliation import (
    RECONCILIATION_SCHEMA_VERSION,
    load_reconciliation,
    reconcile_holdings,
)


@pytest.fixture
def state_root(request):
    """Workspace-local state without pytest's inaccessible Windows temp ACL."""
    root = Path.cwd() / ".pytest_tmp_reconciliation" / request.node.name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root)


def prices(start: str, end: str, first: float, last: float) -> pd.Series:
    return pd.Series(
        [first, last],
        index=pd.to_datetime([start, end]),
        dtype=float,
    )


def measurement(
    traded_symbol: str,
    proxy_symbol: str,
    status: ProxyStatus,
    *,
    leverage: float = 3.0,
) -> ProxyMeasurement:
    if status is ProxyStatus.UNMEASURABLE:
        return ProxyMeasurement(
            traded_symbol=traded_symbol,
            proxy_symbol=proxy_symbol,
            leverage=leverage,
            beta=CostState.UNKNOWN,
            r_squared=CostState.UNKNOWN,
            observations=49,
            qualifies=False,
        )
    qualifies = status is ProxyStatus.QUALIFIED
    return ProxyMeasurement(
        traded_symbol=traded_symbol,
        proxy_symbol=proxy_symbol,
        leverage=leverage,
        beta=leverage if qualifies else leverage * 0.7,
        r_squared=0.99 if qualifies else 0.80,
        observations=252,
        qualifies=qualifies,
    )


def test_a_qualified_pair_produces_the_signed_gap_in_percentage_points(state_root):
    result = reconcile_holdings(
        period_start="2026-08-01",
        period_end="2026-08-08",
        holding_prices={"SOXL": prices("2026-08-01", "2026-08-08", 100, 108)},
        proxy_prices={"SOXX": prices("2026-08-01", "2026-08-08", 100, 102)},
        proxy_measurements={
            "SOXL": measurement("SOXL", "SOXX", ProxyStatus.QUALIFIED)
        },
        state_root=state_root,
    )

    row = result.entries[0]
    assert row.realised_return == pytest.approx(0.08)
    assert row.proxy_return == pytest.approx(0.02)
    assert row.expected_return == pytest.approx(0.06)
    assert row.gap_percentage_points == pytest.approx(2.0)
    assert row.cumulative_gap_percentage_points == pytest.approx(2.0)


def test_cumulative_gap_is_reloaded_and_accumulates_across_runs(state_root):
    qualified = {"SOXL": measurement("SOXL", "SOXX", ProxyStatus.QUALIFIED)}
    reconcile_holdings(
        period_start="2026-08-01",
        period_end="2026-08-08",
        holding_prices={"SOXL": prices("2026-08-01", "2026-08-08", 100, 108)},
        proxy_prices={"SOXX": prices("2026-08-01", "2026-08-08", 100, 102)},
        proxy_measurements=qualified,
        state_root=state_root,
    )

    second = reconcile_holdings(
        period_start="2026-08-08",
        period_end="2026-08-15",
        holding_prices={"SOXL": prices("2026-08-08", "2026-08-15", 108, 113.4)},
        proxy_prices={"SOXX": prices("2026-08-08", "2026-08-15", 102, 103.02)},
        proxy_measurements=qualified,
        state_root=state_root,
    )

    assert second.entries[0].gap_percentage_points == pytest.approx(2.0)
    assert second.entries[0].cumulative_gap_percentage_points == pytest.approx(4.0)
    stored = load_reconciliation(state_root)
    assert stored is not None
    assert len(stored.entries) == 2
    assert stored.entries[-1] == second.entries[0]


def test_a_holding_without_a_qualified_proxy_has_no_expectation_or_gap(state_root):
    result = reconcile_holdings(
        period_start="2026-08-01",
        period_end="2026-08-08",
        holding_prices={"FNGU": prices("2026-08-01", "2026-08-08", 100, 100)},
        proxy_prices={},
        proxy_measurements={
            "FNGU": measurement("FNGU", "FNGS", ProxyStatus.DISCRETIONARY_HOLDING)
        },
        state_root=state_root,
    )

    row = result.entries[0]
    assert row.realised_return == pytest.approx(0.0)
    assert row.proxy_return is None
    assert row.expected_return is None
    assert row.gap_percentage_points is None
    assert row.cumulative_gap_percentage_points is None


def test_unmeasurable_and_measured_but_rejected_pairs_stay_distinct(state_root):
    result = reconcile_holdings(
        period_start="2026-08-01",
        period_end="2026-08-08",
        holding_prices={
            "SPCX": prices("2026-08-01", "2026-08-08", 100, 104),
            "FNGU": prices("2026-08-01", "2026-08-08", 100, 104),
        },
        proxy_prices={},
        proxy_measurements={
            "SPCX": measurement("SPCX", "SPY", ProxyStatus.UNMEASURABLE),
            "FNGU": measurement("FNGU", "FNGS", ProxyStatus.DISCRETIONARY_HOLDING),
        },
        state_root=state_root,
    )

    assert result.entries[0].status is ProxyStatus.UNMEASURABLE
    assert result.entries[1].status is ProxyStatus.DISCRETIONARY_HOLDING
    assert result.entries[0].status is not result.entries[1].status


def test_the_store_is_versioned_under_the_state_root(state_root):
    reconcile_holdings(
        period_start="2026-08-01",
        period_end="2026-08-08",
        holding_prices={"SPCX": prices("2026-08-01", "2026-08-08", 100, 104)},
        proxy_prices={},
        proxy_measurements={
            "SPCX": measurement("SPCX", "SPY", ProxyStatus.UNMEASURABLE)
        },
        state_root=state_root,
    )

    path = state_root / "reconciliation" / "history.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == RECONCILIATION_SCHEMA_VERSION == 1
    assert payload["records"][0]["expected_return"] is None
    assert payload["records"][0]["gap_percentage_points"] is None


def test_a_corrupt_store_is_reported_not_silently_empty(state_root):
    path = state_root / "reconciliation" / "history.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ValueError, match="Corrupt reconciliation history"):
        load_reconciliation(state_root)
