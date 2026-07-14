from __future__ import annotations

from datetime import date, datetime, timezone

from tradingbot.strategies.state import StrategyStateStore

_LEDGER_SUFFIX = "#signals"


def make_signal_id(
    strategy_name: str,
    signal_date: date,
    symbol: str,
    side: str,
    target_weight: float | None = None,
) -> str:
    """Deterministic idempotency key for one strategy decision.

    signal_id = strategy_name + rebalance_date + symbol + side + target_weight.
    The weight is rounded to 6 decimals so float noise cannot produce a
    different id for the same decision.
    """
    weight_part = "NA" if target_weight is None else f"{float(target_weight):.6f}"
    return "|".join(
        [
            strategy_name,
            signal_date.isoformat(),
            symbol.upper(),
            side.upper(),
            weight_part,
        ]
    )


class SignalLedger:
    """Records processed signal ids so re-running the same decision is a no-op.

    Persisted through a StrategyStateStore under its own namespace key, so a
    process restart cannot re-submit orders for signals already handled.
    """

    def __init__(
        self,
        strategy_name: str,
        store: StrategyStateStore | None = None,
        max_entries: int = 10000,
    ) -> None:
        self.strategy_name = strategy_name
        self.max_entries = max_entries
        self._store = store
        self._key = f"{strategy_name}{_LEDGER_SUFFIX}"
        self._processed: dict[str, str] = {}
        if store is not None:
            raw = store.load(self._key).get("processed", {})
            self._processed = {str(signal_id): str(ts) for signal_id, ts in raw.items()}

    def is_processed(self, signal_id: str) -> bool:
        return signal_id in self._processed

    def claim(self, signal_id: str) -> bool:
        """Atomically check-and-mark. Returns True only for the first caller.

        Usage: ``if ledger.claim(signal_id): submit_order(...)`` — the second
        run of the same decision returns False and must not order.
        """
        if signal_id in self._processed:
            return False
        self._processed[signal_id] = datetime.now(timezone.utc).isoformat()
        self._trim()
        self._persist()
        return True

    def _trim(self) -> None:
        overflow = len(self._processed) - self.max_entries
        if overflow > 0:
            # dict preserves insertion order: drop the oldest entries.
            for signal_id in list(self._processed)[:overflow]:
                del self._processed[signal_id]

    def _persist(self) -> None:
        if self._store is not None:
            self._store.save(self._key, {"processed": dict(self._processed)})
