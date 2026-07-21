from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConsistencyCheck:
    """Growth-vs-reinvestment sanity (framework §2: g ~= reinvestment * ROIC).

    Reported, not enforced: real filings rarely satisfy it exactly, so the bot
    surfaces the mismatch rather than blocking valuation on it.
    """

    implied_g: float
    expected_g: float
    within_tolerance: bool


@dataclass(frozen=True)
class DcfInputs:
    """Inputs for a single-scenario FCFF DCF (framework §2).

    Currency labels the cash flows; it must match the price currency the value
    is compared against (enforced in dcf_value via price_currency). g_terminal
    is hard-clamped to g_terminal_cap — the long-run nominal growth ceiling —
    because terminal value dominates and is hypersensitive to it.
    """

    fcff_0: float
    growth: float
    wacc: float
    g_terminal: float
    years: int
    net_debt: float
    minority_interest: float
    non_operating_assets: float
    diluted_shares: float
    reinvestment_rate: float
    roic: float
    currency: str = "KRW"
    g_terminal_cap: float = 0.03
    consistency_tol: float = 0.01


@dataclass(frozen=True)
class DcfResult:
    enterprise_value: float
    equity_value: float
    value_per_share: float
    terminal_value_share: float
    consistency: ConsistencyCheck


def dcf_value(inputs: DcfInputs, *, price_currency: str | None = None) -> DcfResult:
    """Value one scenario. Returns EV, the equity bridge, and per-share value.

    Cash flows must be discounted in their own currency; a price_currency that
    differs from inputs.currency is a currency mismatch and raises (framework
    §2: FX belongs in the portfolio layer, not valuation).
    """
    if price_currency is not None and price_currency != inputs.currency:
        raise ValueError(
            f"currency mismatch: cash flows in {inputs.currency}, price in {price_currency}"
        )
    if inputs.diluted_shares <= 0:
        raise ValueError("diluted_shares must be positive (framework §2: dilution)")
    if inputs.years < 1:
        raise ValueError("years must be at least 1")

    g_terminal = min(inputs.g_terminal, inputs.g_terminal_cap)
    if inputs.wacc - g_terminal <= 0:
        raise ValueError(
            f"wacc ({inputs.wacc}) must exceed clamped terminal growth ({g_terminal})"
        )

    # Explicit-period FCFF, discounted.
    pv_explicit = 0.0
    fcff_n = inputs.fcff_0
    for t in range(1, inputs.years + 1):
        fcff_n = inputs.fcff_0 * (1.0 + inputs.growth) ** t
        pv_explicit += fcff_n / (1.0 + inputs.wacc) ** t

    # Terminal value at year N from the first post-horizon cash flow.
    fcff_next = fcff_n * (1.0 + g_terminal)
    terminal_value = fcff_next / (inputs.wacc - g_terminal)
    pv_terminal = terminal_value / (1.0 + inputs.wacc) ** inputs.years

    enterprise_value = pv_explicit + pv_terminal
    equity_value = (
        enterprise_value
        - inputs.net_debt
        - inputs.minority_interest
        + inputs.non_operating_assets
    )
    value_per_share = equity_value / inputs.diluted_shares
    terminal_value_share = pv_terminal / enterprise_value if enterprise_value else float("nan")

    expected_g = inputs.reinvestment_rate * inputs.roic
    consistency = ConsistencyCheck(
        implied_g=inputs.growth,
        expected_g=expected_g,
        within_tolerance=abs(inputs.growth - expected_g) <= inputs.consistency_tol,
    )

    return DcfResult(
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        value_per_share=value_per_share,
        terminal_value_share=terminal_value_share,
        consistency=consistency,
    )
