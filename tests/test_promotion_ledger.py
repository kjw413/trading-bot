import json
from datetime import UTC, datetime, timedelta

import pytest

from tradingbot.research.promotion_ledger import (
    CriterionResult,
    PromotionRecord,
    Verdict,
    latest_promotion,
    passing_strategies,
    record_promotion,
)


def promotion_record(
    *,
    verdict: Verdict = Verdict.PASS,
    evaluated_at: datetime | None = None,
    commit: str = "abc123",
    criteria: tuple[CriterionResult, ...] | None = None,
) -> PromotionRecord:
    return PromotionRecord(
        strategy="us_asset_rotation",
        market="US",
        universe=("SPY", "QQQ", "TLT"),
        verdict=verdict,
        criteria=(
            CriterionResult(
                name="excess_return",
                threshold=0.0,
                measured=1.25,
                passed=True,
            ),
        )
        if criteria is None
        else criteria,
        cadence="monthly",
        rejected_orders=2,
        total_orders=100,
        evaluated_at=evaluated_at or datetime(2026, 8, 29, 9, 30, tzinfo=UTC),
        commit=commit,
        report_path="reports/evaluation/us_asset_rotation.md",
    )


class TestPromotionLedger:
    def test_a_recorded_verdict_round_trips(self, tmp_path):
        record = promotion_record()

        path = record_promotion(record, tmp_path)

        assert path == tmp_path / "promotion" / "ledger.json"
        assert path.exists()
        assert latest_promotion(record.strategy, record.market, tmp_path) == record
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1

    def test_an_unmeasurable_criterion_is_not_a_pass(self, tmp_path):
        criterion = CriterionResult(
            name="walk_forward_win_rate",
            threshold=0.5,
            measured=None,
            passed=None,
        )
        record = promotion_record(
            verdict=Verdict.UNMEASURABLE,
            criteria=(criterion,),
        )

        record_promotion(record, tmp_path)

        loaded = latest_promotion(record.strategy, record.market, tmp_path)
        assert loaded is not None
        assert loaded.verdict is Verdict.UNMEASURABLE
        assert loaded.criteria[0].measured is None
        assert loaded.criteria[0].passed is None
        assert passing_strategies(tmp_path, current_commit=record.commit) == ()

    def test_the_latest_record_wins_when_a_strategy_is_reevaluated(self, tmp_path):
        evaluated_at = datetime(2026, 8, 29, 9, 30, tzinfo=UTC)
        older_pass = promotion_record(evaluated_at=evaluated_at)
        newer_fail = promotion_record(
            verdict=Verdict.FAIL,
            evaluated_at=evaluated_at + timedelta(hours=1),
            criteria=(
                CriterionResult(
                    name="excess_return",
                    threshold=0.0,
                    measured=-2.93,
                    passed=False,
                ),
            ),
        )

        # A recovered report can be appended after a newer evaluation already exists.
        record_promotion(newer_fail, tmp_path)
        record_promotion(older_pass, tmp_path)

        assert latest_promotion(
            older_pass.strategy, older_pass.market, tmp_path
        ) == newer_fail
        assert passing_strategies(tmp_path, current_commit=older_pass.commit) == ()


class TestInterlock:
    def test_a_stale_commit_is_not_a_basis(self, tmp_path):
        record = promotion_record(commit="evaluated-commit")
        record_promotion(record, tmp_path)

        assert passing_strategies(
            tmp_path, current_commit="current-commit"
        ) == ()
        assert passing_strategies(
            tmp_path, current_commit="evaluated-commit"
        ) == (record,)


class TestStore:
    def test_a_corrupt_ledger_is_reported_not_silently_empty(self, tmp_path):
        assert latest_promotion("missing", "US", tmp_path) is None
        path = record_promotion(promotion_record(), tmp_path)
        path.write_text("{not valid json", encoding="utf-8")

        with pytest.raises(ValueError, match="Corrupt promotion ledger"):
            passing_strategies(tmp_path, current_commit="abc123")
