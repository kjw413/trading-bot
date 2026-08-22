"""Read-only view of a real brokerage account.

Deliberately separate from `broker.base.Broker`. That interface exists to
simulate fills — submit, cancel, expire, mark-to-market, eleven methods in
all — and none of it is needed to answer "what do I hold right now". Keeping
the read path apart means this milestone ships without a single line that
could place an order.

Everything here is what the broker said, not what we computed. Average price
especially: rebuilding it from fills drifts away from the app's number as
soon as fees, splits, or a trade made by hand in the app enter the picture,
and a report that disagrees with the app is a report nobody believes.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

SNAPSHOT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Holding:
    symbol: str
    market: str        # "KR" | "US"
    qty: float         # for arithmetic
    qty_display: str   # exactly as the broker printed it; use this on screen
    avg_price: float   # broker's figure, in `currency`
    last_price: float
    currency: str      # "KRW" | "USD"


@dataclass(frozen=True)
class AccountSnapshot:
    as_of: datetime                # the broker's timestamp, not our clock
    holdings: tuple[Holding, ...]
    cash: dict[str, float]         # currency -> deposit
    fx_to_krw: dict[str, float]    # currency -> won per unit
    fx_source: str                 # "broker" | "macro" — which rate this is

    def rate(self, currency: str) -> float:
        """Won per unit of `currency`. Missing is an error, not 1.0.

        Defaulting would value a US position at a 1350th of its worth and
        render it as a catastrophic loss.
        """
        return self.fx_to_krw[currency]

    def holding_value_krw(self, holding: Holding) -> float:
        return holding.qty * holding.last_price * self.rate(holding.currency)

    def cash_krw(self) -> float:
        return sum(amount * self.rate(cur) for cur, amount in self.cash.items())

    def value_krw(self) -> float:
        return sum(self.holding_value_krw(h) for h in self.holdings) + self.cash_krw()

    def weights_krw(self) -> dict[str, float]:
        """Each holding's share of total value — `plan_rebalance`'s input shape."""
        total = self.value_krw()
        if total <= 0:
            return {}
        return {h.symbol: self.holding_value_krw(h) / total for h in self.holdings}


class AccountReader(Protocol):
    """One method. That is the entire contract this milestone needs."""

    def snapshot(self) -> AccountSnapshot: ...


def save_snapshot(snapshot: AccountSnapshot, root: str | Path) -> Path:
    """Write one snapshot, keyed by when it was taken.

    Timestamped rather than keyed by ISO week because runs are manual and
    irregular: a week key would overwrite the eager weeks and leave holes in
    the quiet ones, and this file is the only record the return chain has.
    """
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "as_of": snapshot.as_of.isoformat(),
        "holdings": [asdict(h) for h in snapshot.holdings],
        "cash": snapshot.cash,
        "fx_to_krw": snapshot.fx_to_krw,
        "fx_source": snapshot.fx_source,
    }
    path = directory / f"{snapshot.as_of.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _load_one(path: Path) -> AccountSnapshot:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corrupt account snapshot: {path}") from exc

    version = payload.get("schema_version")
    if version != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"Unknown snapshot schema {version} in {path} "
            f"(this build reads {SNAPSHOT_SCHEMA_VERSION})"
        )
    return AccountSnapshot(
        as_of=datetime.fromisoformat(payload["as_of"]),
        holdings=tuple(Holding(**row) for row in payload["holdings"]),
        cash=dict(payload["cash"]),
        fx_to_krw=dict(payload["fx_to_krw"]),
        fx_source=payload["fx_source"],
    )


def load_snapshots(root: str | Path) -> list[AccountSnapshot]:
    """Every stored snapshot, oldest first.

    A corrupt or unreadable file raises rather than being skipped: a silently
    dropped snapshot would stretch the next interval across it and report a
    return for a period nobody measured.
    """
    directory = Path(root)
    if not directory.exists():
        return []
    snapshots = [_load_one(path) for path in sorted(directory.glob("*.json"))]
    return sorted(snapshots, key=lambda s: s.as_of)


def load_latest(root: str | Path) -> AccountSnapshot | None:
    snapshots = load_snapshots(root)
    return snapshots[-1] if snapshots else None
