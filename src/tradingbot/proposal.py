"""Per-holding permission gate for weekly rebalance commentary.

This module classifies evidence.  It deliberately has no order, target-weight,
share-count, or execution model; those belong to a later milestone.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from tradingbot.account.base import AccountSnapshot
from tradingbot.instruments import INSTRUMENTS, LeverageState
from tradingbot.research.promotion_ledger import (
    PromotionRecord,
    PromotionTrack,
    Verdict,
    _latest_records,
    _load_records,
)


class NoProposalReason(str, Enum):
    NEVER_EVALUATED = "never_evaluated"
    DID_NOT_PASS = "did_not_pass"
    UNMEASURABLE = "unmeasurable"
    STALE_RECORD = "stale_record"
    CADENCE_MISMATCH = "cadence_mismatch"
    DISCRETIONARY_HOLDING = "discretionary_holding"


class ProposalReason(str, Enum):
    PASSED_NO_CHANGE = "passed_no_change"


@dataclass(frozen=True)
class Refusal:
    symbol: str
    reason: NoProposalReason
    detail: str
    basis: PromotionRecord | None = None


@dataclass(frozen=True)
class PassedNoChange:
    symbol: str
    basis: PromotionRecord
    detail: str
    reason: ProposalReason = ProposalReason.PASSED_NO_CHANGE


SymbolDecision = Refusal | PassedNoChange


@dataclass(frozen=True)
class Proposal:
    """One mutually exclusive decision variant for each held symbol."""

    decisions: tuple[SymbolDecision, ...]

    @property
    def refusals(self) -> tuple[Refusal, ...]:
        return tuple(
            decision for decision in self.decisions if isinstance(decision, Refusal)
        )

    @property
    def holds(self) -> tuple[PassedNoChange, ...]:
        return tuple(
            decision
            for decision in self.decisions
            if isinstance(decision, PassedNoChange)
        )


def render_decision(decision: SymbolDecision) -> str:
    """Return the user-facing sentence chosen by the decision variant."""
    return decision.detail


def _normalized_symbols(record: PromotionRecord) -> set[str]:
    return {symbol.strip().upper() for symbol in record.universe}


def _record_time(record: PromotionRecord) -> datetime:
    evaluated_at = record.evaluated_at
    if evaluated_at.tzinfo is None:
        return evaluated_at.replace(tzinfo=UTC)
    return evaluated_at.astimezone(UTC)


def _record_is_pass(record: PromotionRecord) -> bool:
    return (
        record.track is PromotionTrack.TRACK_A
        and bool(record.criteria)
        and record.verdict is Verdict.PASS
        and all(
            criterion.measured is not None and criterion.passed is True
            for criterion in record.criteria
        )
    )


def _discretionary_detail(symbol: str) -> str | None:
    instrument = INSTRUMENTS.get(symbol)
    if instrument is None:
        return (
            f"{symbol}: discretionary holding — the instrument is not in the "
            "registry, so its leverage cannot be reasoned about."
        )
    if instrument.leverage is LeverageState.UNKNOWN:
        return (
            f"{symbol}: discretionary holding — unknown leverage in the instrument "
            "registry keeps it permanently outside the bot's judgement."
        )
    if instrument.leverage_verification_note is not None:
        return (
            f"{symbol}: discretionary holding — the instrument registry identifies "
            "a structural history gap, so it remains outside the bot's judgement "
            f"({instrument.leverage_verification_note})."
        )
    if instrument.kind == "etn" and instrument.benchmark_index is None:
        return (
            f"{symbol}: discretionary holding — this ETN has no registered benchmark "
            "and cannot cover the required direct validation history, so it remains "
            "outside the bot's judgement."
        )
    return None


def _failed_detail(symbol: str, record: PromotionRecord) -> str:
    failed = [criterion for criterion in record.criteria if criterion.passed is False]
    if not failed:
        return (
            f"{symbol}: the latest evaluation did not pass, but its ledger record "
            "does not identify a failed criterion."
        )
    criteria = ", ".join(
        f"{criterion.name} (measured {criterion.measured:g}; "
        f"threshold {criterion.threshold:g})"
        for criterion in failed
        if criterion.measured is not None
    )
    return f"{symbol}: the evaluation did not pass: {criteria}."


def _unmeasurable_detail(symbol: str, record: PromotionRecord) -> str:
    unmeasured = [
        criterion.name
        for criterion in record.criteria
        if criterion.measured is None or criterion.passed is None
    ]
    if not record.criteria:
        cause = "no criteria were checked"
    elif unmeasured:
        cause = "unmeasured criteria: " + ", ".join(unmeasured)
    else:
        cause = "the ledger verdict is unmeasurable"
    return (
        f"{symbol}: the evaluation is unmeasurable ({cause}); no criterion failed, "
        "but this is not a pass."
    )


def _decision_from_record(
    symbol: str,
    record: PromotionRecord,
    *,
    current_commit: str,
    requested_cadence: str,
) -> SymbolDecision:
    failed_criterion = any(
        criterion.passed is False for criterion in record.criteria
    )
    unmeasured_criterion = not record.criteria or any(
        criterion.measured is None or criterion.passed is None
        for criterion in record.criteria
    )

    if record.verdict is Verdict.FAIL or failed_criterion:
        return Refusal(
            symbol,
            NoProposalReason.DID_NOT_PASS,
            _failed_detail(symbol, record),
            record,
        )
    if record.verdict is Verdict.UNMEASURABLE or unmeasured_criterion:
        return Refusal(
            symbol,
            NoProposalReason.UNMEASURABLE,
            _unmeasurable_detail(symbol, record),
            record,
        )
    if record.commit != current_commit:
        return Refusal(
            symbol,
            NoProposalReason.STALE_RECORD,
            (
                f"{symbol}: the evaluation passed at commit {record.commit}, but the "
                f"running commit is {current_commit}; re-run the evaluation."
            ),
            record,
        )
    if record.cadence != requested_cadence:
        return Refusal(
            symbol,
            NoProposalReason.CADENCE_MISMATCH,
            (
                f"{symbol}: the evaluation passed at {record.cadence} cadence, but "
                f"{requested_cadence} was requested; evaluate that cadence first."
            ),
            record,
        )
    if not _record_is_pass(record):
        return Refusal(
            symbol,
            NoProposalReason.UNMEASURABLE,
            _unmeasurable_detail(symbol, record),
            record,
        )
    return PassedNoChange(
        symbol=symbol,
        basis=record,
        detail=(
            f"{symbol}: the evaluation passed for this commit and cadence; hold as is "
            "because no change is needed this week."
        ),
    )


def propose_rebalance(
    snapshot: AccountSnapshot,
    *,
    ledger_root: str | Path,
    current_commit: str,
    price_history: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    requested_cadence: str = "weekly",
) -> Proposal:
    """Classify each holding; never calculate or place a trade."""
    # Both inputs belong to later proposal stages.  Neither may weaken this
    # permission gate before proxy measurement and freshness rules exist.
    del price_history, now

    latest = tuple(_latest_records(_load_records(ledger_root)).values())
    decisions: list[SymbolDecision] = []

    for holding in snapshot.holdings:
        symbol = holding.symbol.strip().upper()
        discretionary = _discretionary_detail(symbol)
        if discretionary is not None:
            decisions.append(
                Refusal(
                    symbol,
                    NoProposalReason.DISCRETIONARY_HOLDING,
                    discretionary,
                )
            )
            continue

        covered_records = [
            record
            for record in latest
            if record.market.strip().upper() == holding.market.strip().upper()
            and symbol in _normalized_symbols(record)
        ]
        candidates = [
            record
            for record in covered_records
            if record.track is PromotionTrack.TRACK_A
        ]
        if not candidates:
            if covered_records:
                detail = (
                    f"{symbol}: Track B execution-risk measurements exist, but no "
                    "Track A signal validation covers this holding; nothing can be "
                    "proposed."
                )
            else:
                detail = (
                    f"{symbol}: no evaluation record covers this holding; "
                    "nothing can be proposed."
                )
            decisions.append(
                Refusal(
                    symbol,
                    NoProposalReason.NEVER_EVALUATED,
                    detail,
                )
            )
            continue

        active = [
            record
            for record in candidates
            if _record_is_pass(record)
            and record.commit == current_commit
            and record.cadence == requested_cadence
        ]
        record = max(active or candidates, key=_record_time)
        decisions.append(
            _decision_from_record(
                symbol,
                record,
                current_commit=current_commit,
                requested_cadence=requested_cadence,
            )
        )

    return Proposal(tuple(decisions))
