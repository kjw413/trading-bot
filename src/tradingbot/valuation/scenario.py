from __future__ import annotations

from dataclasses import dataclass

from tradingbot.valuation.dcf import DcfInputs, dcf_value


@dataclass(frozen=True)
class ScenarioValues:
    """Per-share fair value as a (conservative, base, optimistic) tuple.

    Framework §1 rule 3: valuation is never a single scalar. The bounds must be
    ordered — an optimistic case below the base case means the scenario
    assumptions were mislabeled.
    """

    conservative: float
    base: float
    optimistic: float

    def __post_init__(self) -> None:
        if not (self.conservative <= self.base <= self.optimistic):
            raise ValueError(
                "scenario values must satisfy conservative <= base <= optimistic; "
                f"got ({self.conservative}, {self.base}, {self.optimistic})"
            )


def scenario_values(
    conservative_inputs: DcfInputs,
    base_inputs: DcfInputs,
    optimistic_inputs: DcfInputs,
) -> ScenarioValues:
    """Run each scenario's DCF and collect the per-share values into a tuple."""
    return ScenarioValues(
        conservative=dcf_value(conservative_inputs).value_per_share,
        base=dcf_value(base_inputs).value_per_share,
        optimistic=dcf_value(optimistic_inputs).value_per_share,
    )
