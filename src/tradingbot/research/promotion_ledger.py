"""Machine-readable promotion verdicts for evaluated strategies."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

PROMOTION_SCHEMA_VERSION = 2
PROMOTION_DIRNAME = "promotion"
PROMOTION_FILENAME = "ledger.json"


class Verdict(Enum):
    PASS = "pass"
    FAIL = "fail"
    UNMEASURABLE = "unmeasurable"


class PromotionTrack(str, Enum):
    TRACK_A = "track_a"
    TRACK_B = "track_b"


@dataclass(frozen=True)
class CriterionResult:
    name: str
    threshold: float
    measured: float | None
    passed: bool | None


@dataclass(frozen=True)
class PromotionRecord:
    strategy: str
    market: str
    universe: tuple[str, ...]
    track: PromotionTrack
    profile_name: str
    verdict: Verdict
    criteria: tuple[CriterionResult, ...]
    cadence: str
    rejected_orders: int
    total_orders: int
    evaluated_at: datetime
    commit: str
    report_path: str


def _promotion_path(root: str | Path) -> Path:
    return Path(root) / PROMOTION_DIRNAME / PROMOTION_FILENAME


def _criterion_payload(criterion: CriterionResult) -> dict[str, object]:
    return {
        "name": criterion.name,
        "threshold": criterion.threshold,
        "measured": criterion.measured,
        "passed": criterion.passed,
    }


def _record_payload(record: PromotionRecord) -> dict[str, object]:
    return {
        "strategy": record.strategy,
        "market": record.market,
        "universe": list(record.universe),
        "track": record.track.value,
        "profile_name": record.profile_name,
        "verdict": record.verdict.value,
        "criteria": [_criterion_payload(criterion) for criterion in record.criteria],
        "cadence": record.cadence,
        "rejected_orders": record.rejected_orders,
        "total_orders": record.total_orders,
        "evaluated_at": record.evaluated_at.isoformat(),
        "commit": record.commit,
        "report_path": record.report_path,
    }


def _criterion_from_payload(row: dict[str, object]) -> CriterionResult:
    measured = row["measured"]
    return CriterionResult(
        name=row["name"],
        threshold=float(row["threshold"]),
        measured=None if measured is None else float(measured),
        passed=row["passed"],
    )


def _record_from_payload(row: dict[str, object]) -> PromotionRecord:
    return PromotionRecord(
        strategy=row["strategy"],
        market=row["market"],
        universe=tuple(row["universe"]),
        track=PromotionTrack(row["track"]),
        profile_name=row["profile_name"],
        verdict=Verdict(row["verdict"]),
        criteria=tuple(
            _criterion_from_payload(criterion) for criterion in row["criteria"]
        ),
        cadence=row["cadence"],
        rejected_orders=int(row["rejected_orders"]),
        total_orders=int(row["total_orders"]),
        evaluated_at=datetime.fromisoformat(row["evaluated_at"]),
        commit=row["commit"],
        report_path=row["report_path"],
    )


def _load_records(root: str | Path) -> tuple[PromotionRecord, ...]:
    path = _promotion_path(root)
    if not path.exists():
        return ()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["schema_version"] != PROMOTION_SCHEMA_VERSION:
            raise ValueError("unknown promotion schema")
        return tuple(_record_from_payload(row) for row in payload["records"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Corrupt promotion ledger: {path}") from exc


def record_promotion(record: PromotionRecord, root: str | Path) -> Path:
    path = _promotion_path(root)
    records = (*_load_records(root), record)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "records": [_record_payload(stored) for stored in records],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _evaluated_at_utc(record: PromotionRecord) -> datetime:
    evaluated_at = record.evaluated_at
    if evaluated_at.tzinfo is None:
        return evaluated_at.replace(tzinfo=UTC)
    return evaluated_at.astimezone(UTC)


def _latest_records(
    records: tuple[PromotionRecord, ...],
) -> dict[tuple[str, str, PromotionTrack], PromotionRecord]:
    latest: dict[tuple[str, str, PromotionTrack], PromotionRecord] = {}
    for record in records:
        key = (record.strategy, record.market, record.track)
        previous = latest.get(key)
        if previous is None or _evaluated_at_utc(record) >= _evaluated_at_utc(previous):
            latest[key] = record
    return latest


def latest_promotion(
    strategy: str,
    market: str,
    root: str | Path,
    *,
    track: PromotionTrack | None = None,
) -> PromotionRecord | None:
    matches = (
        record
        for record in _load_records(root)
        if record.strategy == strategy
        and record.market == market
        and (track is None or record.track is track)
    )
    return max(matches, key=_evaluated_at_utc, default=None)


def _is_passing(record: PromotionRecord) -> bool:
    return record.verdict is Verdict.PASS and all(
        criterion.measured is not None and criterion.passed is True
        for criterion in record.criteria
    )


def passing_strategies(
    root: str | Path, *, current_commit: str
) -> tuple[PromotionRecord, ...]:
    latest = _latest_records(_load_records(root))
    return tuple(
        record
        for record in latest.values()
        if record.track is PromotionTrack.TRACK_A
        and record.commit == current_commit
        and _is_passing(record)
    )
