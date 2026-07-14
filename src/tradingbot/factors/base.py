from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Sequence

import pandas as pd

from tradingbot.data.store import PriceDataStore


class Factor(ABC):
    """Cross-sectional factor: one value per symbol as of a given date.

    Contract:
    - Only data available at `dt` may be used (the data store enforces the
      date cutoff; factors must not bypass it).
    - Symbols that cannot be scored (missing data, short history) get NaN —
      they are not silently dropped, so the caller can distinguish
      "excluded" from "not in universe".
    """

    name: str

    @abstractmethod
    def compute(self, dt: date, universe: Sequence[str], data_store: PriceDataStore) -> pd.Series:
        """Return factor values indexed by symbol, named after the factor."""
        raise NotImplementedError

    def _empty(self, universe: Sequence[str]) -> pd.Series:
        return pd.Series(
            [float("nan")] * len(universe),
            index=[symbol.upper() for symbol in universe],
            name=self.name,
            dtype=float,
        )
