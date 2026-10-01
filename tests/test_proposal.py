import ast
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import tradingbot.proposal as proposal_module
from tradingbot.account.base import AccountSnapshot, Holding
from tradingbot.proposal import (
    NoProposalReason,
    PassedNoChange,
    ProposalReason,
    Refusal,
    propose_rebalance,
    render_decision,
)
from tradingbot.research.promotion_ledger import (
    CriterionResult,
    PromotionRecord,
    PromotionTrack,
    Verdict,
    record_promotion,
)


def snapshot(symbol: str = "SOXL") -> AccountSnapshot:
    return AccountSnapshot(
        as_of=datetime(2026, 8, 29, 9, 0, tzinfo=UTC),
        holdings=(
            Holding(
                symbol=symbol,
                market="US",
                qty=10.0,
                qty_display="10",
                avg_price=30.0,
                last_price=32.0,
                currency="USD",
            ),
        ),
        cash={"USD": 100.0},
        fx_to_krw={"USD": 1_350.0},
        fx_source="broker",
    )


def promotion_record(
    symbol: str = "SOXL",
    *,
    verdict: Verdict = Verdict.PASS,
    commit: str = "running-commit",
    cadence: str = "weekly",
    track: PromotionTrack = PromotionTrack.TRACK_A,
    profile_name: str = "default",
) -> PromotionRecord:
    if verdict is Verdict.PASS:
        criterion = CriterionResult("sharpe", 0.5, 0.72, True)
    elif verdict is Verdict.FAIL:
        criterion = CriterionResult("max_drawdown", 0.25, 0.41, False)
    else:
        criterion = CriterionResult("walk_forward_win_rate", 0.6, None, None)
    return PromotionRecord(
        strategy="direct_holding_strategy",
        market="US",
        universe=(symbol,),
        track=track,
        profile_name=profile_name,
        verdict=verdict,
        criteria=(criterion,),
        cadence=cadence,
        rejected_orders=0,
        total_orders=20,
        evaluated_at=datetime(2026, 8, 29, 8, 0, tzinfo=UTC),
        commit=commit,
        report_path="reports/evaluation/direct_holding_strategy.md",
    )


def decision_for(tmp_path, symbol="SOXL", **kwargs):
    record = kwargs.pop("record", None)
    if record is not None:
        record_promotion(record, tmp_path)
    proposal = propose_rebalance(
        snapshot(symbol),
        ledger_root=tmp_path,
        current_commit=kwargs.pop("current_commit", "running-commit"),
        requested_cadence=kwargs.pop("requested_cadence", "weekly"),
        **kwargs,
    )
    assert len(proposal.decisions) == 1
    return proposal.decisions[0]


class TestRefusalVocabulary:
    def test_all_six_states_have_their_own_reason(self, tmp_path_factory):
        cases = (
            (
                "never evaluated",
                "SOXL",
                None,
                NoProposalReason.NEVER_EVALUATED,
            ),
            (
                "did not pass",
                "SOXL",
                promotion_record(verdict=Verdict.FAIL),
                NoProposalReason.DID_NOT_PASS,
            ),
            (
                "unmeasurable",
                "SOXL",
                promotion_record(verdict=Verdict.UNMEASURABLE),
                NoProposalReason.UNMEASURABLE,
            ),
            (
                "stale record",
                "SOXL",
                promotion_record(commit="evaluated-commit"),
                NoProposalReason.STALE_RECORD,
            ),
            (
                "discretionary holding",
                "SPCX",
                None,
                NoProposalReason.DISCRETIONARY_HOLDING,
            ),
            (
                "passed, no change",
                "SOXL",
                promotion_record(),
                ProposalReason.PASSED_NO_CHANGE,
            ),
        )

        decisions = []
        for label, symbol, record, expected in cases:
            root = tmp_path_factory.mktemp(label.replace(" ", "_"))
            decision = decision_for(root, symbol, record=record)
            assert decision.reason is expected
            decisions.append(decision)

        assert len({decision.reason.value for decision in decisions}) == 6

    def test_the_six_states_render_as_six_different_strings(self, tmp_path_factory):
        cases = (
            ("never", "SOXL", None),
            ("failed", "SOXL", promotion_record(verdict=Verdict.FAIL)),
            ("unmeasurable", "SOXL", promotion_record(verdict=Verdict.UNMEASURABLE)),
            ("stale", "SOXL", promotion_record(commit="evaluated-commit")),
            ("discretionary", "SPCX", None),
            ("hold", "SOXL", promotion_record()),
        )
        rendered = []
        for label, symbol, record in cases:
            root = tmp_path_factory.mktemp(label)
            rendered.append(render_decision(decision_for(root, symbol, record=record)))

        assert len(rendered) == 6
        assert len(set(rendered)) == 6
        assert "nothing can be proposed" in rendered[0]
        assert "hold as is" in rendered[-1]
        assert rendered[0] != rendered[-1]


class TestInterlock:
    def test_refusals_and_orders_cannot_both_be_present(self, tmp_path):
        proposal = propose_rebalance(
            snapshot(),
            ledger_root=tmp_path,
            current_commit="running-commit",
        )

        assert proposal.refusals
        assert not hasattr(proposal, "orders")
        assert all(not hasattr(decision, "orders") for decision in proposal.decisions)

    def test_a_stale_commit_is_stale_not_failed(self, tmp_path):
        decision = decision_for(
            tmp_path,
            record=promotion_record(commit="evaluated-commit"),
            current_commit="running-commit",
        )

        assert isinstance(decision, Refusal)
        assert decision.reason is NoProposalReason.STALE_RECORD
        assert "evaluated-commit" in decision.detail
        assert "running-commit" in decision.detail

    def test_a_monthly_verdict_refuses_a_weekly_request(self, tmp_path):
        decision = decision_for(
            tmp_path,
            record=promotion_record(cadence="monthly"),
            requested_cadence="weekly",
        )

        assert isinstance(decision, Refusal)
        assert decision.reason is NoProposalReason.CADENCE_MISMATCH
        assert "monthly" in decision.detail
        assert "weekly" in decision.detail

    def test_a_failed_verdict_names_the_failed_criterion(self, tmp_path):
        decision = decision_for(
            tmp_path,
            record=promotion_record(verdict=Verdict.FAIL),
        )

        assert isinstance(decision, Refusal)
        assert decision.reason is NoProposalReason.DID_NOT_PASS
        assert "max_drawdown" in decision.detail

    def test_a_current_weekly_pass_is_a_considered_hold(self, tmp_path):
        decision = decision_for(tmp_path, record=promotion_record())

        assert isinstance(decision, PassedNoChange)
        assert decision.reason is ProposalReason.PASSED_NO_CHANGE
        assert decision.basis.commit == "running-commit"

    def test_a_track_b_pass_cannot_be_a_considered_hold(self, tmp_path):
        decision = decision_for(
            tmp_path,
            record=promotion_record(
                track=PromotionTrack.TRACK_B,
                profile_name="leveraged",
            ),
        )

        assert isinstance(decision, Refusal)
        assert decision.reason is NoProposalReason.NEVER_EVALUATED
        assert "Track A" in decision.detail

    def test_a_mixed_account_is_decided_per_symbol(self, tmp_path):
        account = snapshot()
        spcx = replace(account.holdings[0], symbol="SPCX")
        account = replace(account, holdings=(*account.holdings, spcx))
        record_promotion(promotion_record("SOXL"), tmp_path)

        proposal = propose_rebalance(
            account,
            ledger_root=tmp_path,
            current_commit="running-commit",
        )

        by_symbol = {decision.symbol: decision for decision in proposal.decisions}
        assert isinstance(by_symbol["SOXL"], PassedNoChange)
        assert isinstance(by_symbol["SPCX"], Refusal)
        assert (
            by_symbol["SPCX"].reason
            is NoProposalReason.DISCRETIONARY_HOLDING
        )


class TestDiscretionaryHolding:
    def test_unknown_leverage_is_discretionary_even_after_a_failed_evaluation(
        self, tmp_path
    ):
        decision = decision_for(
            tmp_path,
            "SPCX",
            record=promotion_record("SPCX", verdict=Verdict.FAIL),
        )

        assert isinstance(decision, Refusal)
        assert decision.reason is NoProposalReason.DISCRETIONARY_HOLDING
        assert "unknown leverage" in decision.detail
        assert "failed" not in decision.detail

    def test_a_registry_history_gap_is_discretionary_not_failed(self, tmp_path):
        decision = decision_for(
            tmp_path,
            "GGLL",
            record=promotion_record("GGLL", verdict=Verdict.FAIL),
        )

        assert isinstance(decision, Refusal)
        assert decision.reason is NoProposalReason.DISCRETIONARY_HOLDING
        assert "structural history gap" in decision.detail
        assert "failed" not in decision.detail


class TestNoExecution:
    def test_the_proposal_module_has_no_order_or_execution_path(self):
        source = Path(proposal_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        definitions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        called = {
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        }

        assert "Order" not in definitions
        assert not any(
            module.startswith(("tradingbot.allocation", "tradingbot.broker"))
            for module in imports
        )
        assert called.isdisjoint(
            {"plan_rebalance", "submit_order", "buy", "sell", "cancel_order"}
        )
