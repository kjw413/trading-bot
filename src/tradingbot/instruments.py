"""Structural facts about leveraged products known to the trading bot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LeverageState(Enum):
    """Non-numeric leverage states that callers must handle explicitly."""

    UNKNOWN = "unknown"

    def __bool__(self) -> bool:
        raise TypeError(
            "Unknown leverage has no truth value; "
            "handle LeverageState.UNKNOWN explicitly"
        )


Leverage = float | LeverageState


@dataclass(frozen=True)
class Instrument:
    symbol: str
    leverage: Leverage
    kind: str
    expense_ratio: float | None
    benchmark_index: str | None
    leverage_verification_note: str | None = None


# This is the only registry of leverage in the application.  Values that need
# a precise, maintained source stay unknown instead of being filled from memory.
INSTRUMENTS: dict[str, Instrument] = {
    "FNGU": Instrument("FNGU", 3.0, "etn", None, None),
    "GGLL": Instrument(
        "GGLL",
        2.0,
        "single_stock_levered",
        None,
        None,
        leverage_verification_note=(
            "unverified against GOOGL; underlying history is not cached"
        ),
    ),
    "LABU": Instrument("LABU", 3.0, "etf", None, None),
    "SPCX": Instrument("SPCX", LeverageState.UNKNOWN, "etf", None, None),
    "SPXL": Instrument("SPXL", 3.0, "etf", None, None),
    "SQQQ": Instrument("SQQQ", -3.0, "etf", None, None),
    "SOXL": Instrument("SOXL", 3.0, "etf", None, None),
    "SOXS": Instrument("SOXS", -3.0, "etf", None, None),
    "TECL": Instrument("TECL", 3.0, "etf", None, None),
    "TECS": Instrument("TECS", -3.0, "etf", None, None),
    "TQQQ": Instrument("TQQQ", 3.0, "etf", None, None),
}


def leverage_of(symbol: str) -> Leverage:
    """Return registered leverage state, refusing to guess for a missing symbol."""
    normalized = symbol.strip().upper()
    try:
        return INSTRUMENTS[normalized].leverage
    except KeyError:
        shown = normalized or "<empty>"
        raise KeyError(
            f"Instrument {shown!r} is not registered; refusing to assume 1.0 leverage"
        ) from None
