from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequiredReturn:
    """Required annual return, decomposed CAPM-style (framework §2).

    r_required = risk_free + equity_risk_premium + firm_specific_premium

    Each component is a per-annum rate. None may be negative: a risk premium
    that lowers the hurdle below the risk-free rate is a modelling error, not
    a legitimate input.
    """

    risk_free: float
    equity_risk_premium: float
    firm_specific_premium: float

    def __post_init__(self) -> None:
        for name in ("risk_free", "equity_risk_premium", "firm_specific_premium"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")

    def rate(self) -> float:
        return self.risk_free + self.equity_risk_premium + self.firm_specific_premium


def irr(p0: float, p_t: float, dividends: float, years: float) -> float:
    """Annualized expected return from buying at p0 (framework §2).

    IRR = ((p_t + dividends) / p0)^(1/years) - 1

    Note p0 is the *current* price the decision is made at — never a cost
    basis. Raises when p0 or years is non-positive.
    """
    if p0 <= 0:
        raise ValueError("p0 must be positive")
    if years <= 0:
        raise ValueError("years must be positive")
    return ((p_t + dividends) / p0) ** (1.0 / years) - 1.0


def max_buy_price(p_t: float, dividends: float, r_required: float, years: float) -> float:
    """Highest price at which the required return is still met (framework §2).

    MaxBuyPrice = (p_t + dividends) / (1 + r_required)^years

    Buying at exactly this price yields an IRR equal to r_required.
    """
    if years <= 0:
        raise ValueError("years must be positive")
    return (p_t + dividends) / (1.0 + r_required) ** years
