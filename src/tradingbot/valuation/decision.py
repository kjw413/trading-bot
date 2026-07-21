from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from tradingbot.valuation.scenario import ScenarioValues


class Signal(str, Enum):
    """Action zones from framework §5, ordered cheap -> expensive."""

    ACCUMULATE = "ACCUMULATE"
    PARTIAL = "PARTIAL"
    HOLD_OR_TRIM = "HOLD_OR_TRIM"
    EXIT = "EXIT"


@dataclass(frozen=True)
class Decision:
    signal: Signal
    current_price: float
    max_buy: ScenarioValues
    reason: str


def decide(current_price: float, max_buy: ScenarioValues) -> Decision:
    """Map the current price against the scenario MaxBuy prices (framework §5).

    max_buy holds the required-return MaxBuyPrice for each scenario, so it is
    ordered conservative <= base <= optimistic. Inputs are the current price
    and those bounds only — the cost basis never enters (framework rule 2).

        price <= MaxBuy(conservative)  -> ACCUMULATE  (met even worst case)
        price <= MaxBuy(base)          -> PARTIAL      (base met, worst not)
        price <= MaxBuy(optimistic)    -> HOLD_OR_TRIM (only optimistic met)
        price >  MaxBuy(optimistic)    -> EXIT         (unmet even best case)
    """
    if current_price <= max_buy.conservative:
        signal = Signal.ACCUMULATE
        reason = "price at/below conservative MaxBuy: required return met even in the worst case"
    elif current_price <= max_buy.base:
        signal = Signal.PARTIAL
        reason = "price between conservative and base MaxBuy: base met, conservative not"
    elif current_price <= max_buy.optimistic:
        signal = Signal.HOLD_OR_TRIM
        reason = "price between base and optimistic MaxBuy: relies on the optimistic case"
    else:
        signal = Signal.EXIT
        reason = "price above optimistic MaxBuy: required return unmet even in the best case"
    return Decision(signal=signal, current_price=current_price, max_buy=max_buy, reason=reason)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: list[str]


def accumulate_gate(price_ok: bool, thesis_intact: bool, survival_ok: bool) -> GateResult:
    """Three-condition AND gate for adding to a position (framework §5).

    Price alone never justifies buying more: the investment thesis must still
    hold and the company must be able to survive to the value being realized.
    """
    reasons: list[str] = []
    if not price_ok:
        reasons.append("price: IRR below required return")
    if not thesis_intact:
        reasons.append("thesis: share/margin/ROIC/balance-sheet/cash-flow impaired")
    if not survival_ok:
        reasons.append("survival: dilution or default risk before value is realized")
    return GateResult(passed=not reasons, reasons=reasons)


class CompanyType(str, Enum):
    """Routing key for the per-type valuation model (framework §4)."""

    STABLE = "STABLE"          # manufacturing / consumer / services
    BANK = "BANK"              # bank / insurer
    CYCLICAL = "CYCLICAL"      # semiconductor / chemical / steel / shipping
    SAAS = "SAAS"              # SaaS / platform / high-growth
    REIT = "REIT"             # REIT / real estate
    HOLDING = "HOLDING"        # holding company
    BIO = "BIO"                # clinical-stage biotech


MODEL_BY_TYPE: dict[CompanyType, str] = {
    CompanyType.STABLE: "FCFF_DCF",
    CompanyType.BANK: "RIM",
    CompanyType.CYCLICAL: "MID_CYCLE_EARNINGS",
    CompanyType.SAAS: "MULTI_STAGE_DCF",
    CompanyType.REIT: "NAV",
    CompanyType.HOLDING: "SOTP",
    CompanyType.BIO: "RNPV",
}

# Only the FCFF DCF family is built in this scope; the rest is a routing skeleton.
SUPPORTED_MODELS: frozenset[str] = frozenset({"FCFF_DCF"})


def primary_model(company_type: CompanyType) -> str:
    """Model identifier for a company type (framework §4 table)."""
    return MODEL_BY_TYPE[company_type]


def ensure_supported(company_type: CompanyType) -> None:
    """Raise unless the company type's primary model is implemented yet."""
    model = primary_model(company_type)
    if model not in SUPPORTED_MODELS:
        raise NotImplementedError(
            f"{model} for {company_type.value} is not implemented yet; only "
            f"{sorted(SUPPORTED_MODELS)} are available in this scope"
        )
