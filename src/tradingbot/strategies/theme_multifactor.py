"""Theme multifactor strategy: combined factor score -> target weights -> orders.

Decision flow (spec §9):
    theme members at dt -> factor scores (weights config drives WHICH factors)
    -> standardize -> combine -> top N -> equal or inverse-vol weights
    -> regime exposure scaling -> concentration/cash constraints -> targets

The factor-weights config is the single source of truth for which factors
run: every key is resolved through the registry up front, so a typo'd name
raises immediately instead of being silently zero-weighted.

An empty targets dict is a deliberate signal: nothing was scoreable at dt
(stale or missing data), and the caller must skip rebalancing rather than
trade on nothing.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

from tradingbot.allocation.constraints import apply_constraints
from tradingbot.allocation.ranking import select_top
from tradingbot.allocation.rebalance import is_rebalance_date, plan_rebalance
from tradingbot.allocation.weights import (
    equal_weights,
    inverse_volatility_weights,
    realized_volatility,
    scale_weights,
)
from tradingbot.config import resolve_project_path
from tradingbot.data.universe import get_theme, members as theme_members
from tradingbot.engine.calendar import get_calendar
from tradingbot.factors.registry import get_factor
from tradingbot.factors.transform import combine, standardize
from tradingbot.research.gate import load_research_config
from tradingbot.research.regime import equity_exposure, market_regime
from tradingbot.strategies.base import Strategy
from tradingbot.strategies.signals import SignalLedger, make_signal_id
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

WEIGHTINGS = ("equal", "inverse_volatility")


class ThemeMultifactorStrategy(Strategy):
    name = "theme_multifactor"
    default_params = {
        "theme": "ai_semiconductor",
        "market": "KR",
        "rebalance": "monthly",
        "top_n": 3,
        "weighting": "inverse_volatility",
        "volatility_days": 60,
        "band": 0.005,
        "min_factors": 1,
        "bear_exposure": 0.5,
        "regime_series": "kospi",
        "regime_ma_days": 200,
        "data_root": "data/cache",
        "processed_root": "data/processed",
        "research_config": None,
        "themes_path": None,
    }

    def __init__(self, **params) -> None:
        super().__init__(**params)
        if self.params["weighting"] not in WEIGHTINGS:
            raise ValueError(
                f"Unknown weighting: {self.params['weighting']}. "
                f"Available: {', '.join(WEIGHTINGS)}"
            )
        self._research: dict | None = None
        self._factor_weights: dict[str, float] | None = None
        self._data_store = None
        self._last_seen_date: date | None = None
        self._last_rebalance_date: date | None = None
        self._last_targets: dict[str, float] = {}
        self._ledger: SignalLedger | None = None

    @property
    def research(self) -> dict:
        if self._research is None:
            self._research = load_research_config(self.params["research_config"])
        return self._research

    @property
    def factor_weights(self) -> dict[str, float]:
        """[factor_weights] keys drive which factors run; typos fail loudly."""
        if self._factor_weights is None:
            raw = self.research.get("factor_weights", {})
            if not raw:
                raise ValueError("research config has no [factor_weights] section")
            for factor_name in raw:
                get_factor(factor_name)  # raises ValueError on unknown names
            self._factor_weights = {name: float(value) for name, value in raw.items()}
        return self._factor_weights

    def generate_targets(
        self, dt: date, universe: Sequence[str], data_store
    ) -> dict[str, float]:
        """Target equity weights as of dt's close. Empty dict = skip rebalance."""
        if not universe:
            return {}

        scores = {
            name: standardize(get_factor(name).compute(dt, universe, data_store))
            for name in self.factor_weights
        }
        combined = combine(
            scores, self.factor_weights, min_factors=int(self.params["min_factors"])
        )
        if combined.dropna().empty:
            LOGGER.warning(
                "theme_multifactor: no scoreable symbol at %s (stale or missing data); "
                "skipping rebalance",
                dt,
            )
            return {}

        selected = select_top(combined, int(self.params["top_n"]))
        if not selected:
            return {}

        if self.params["weighting"] == "equal":
            base = equal_weights(selected)
        else:
            vol_days = int(self.params["volatility_days"])
            volatilities = {}
            for symbol in selected:
                try:
                    history = data_store.price_history(symbol, dt, vol_days + 1)
                except (FileNotFoundError, KeyError):
                    volatilities[symbol] = float("nan")
                    continue
                volatilities[symbol] = realized_volatility(history["close"], vol_days)
            base = inverse_volatility_weights(volatilities)

        regime_state = market_regime(
            data_store,
            dt,
            series=str(self.params["regime_series"]),
            ma_days=int(self.params["regime_ma_days"]),
        )
        exposure = equity_exposure(regime_state, bear=float(self.params["bear_exposure"]))
        scaled = scale_weights(base, exposure)

        limits = self.research.get("risk_limits", {})
        return apply_constraints(
            scaled,
            max_weight=float(limits.get("max_position_weight", 0.40)),
            cash_buffer=float(limits.get("min_cash_weight", 0.02)),
        )

    def _store(self):
        """Lazily built local-only data store (prices + PIT panels)."""
        if self._data_store is None:
            from tradingbot.data.cache import ParquetCache
            from tradingbot.data.store import ParquetDataStore

            self._data_store = ParquetDataStore(
                ParquetCache(resolve_project_path(self.params["data_root"])),
                str(self.params["market"]),
                processed_root=resolve_project_path(self.params["processed_root"]),
            )
        return self._data_store

    def on_bar(self, ctx, bar) -> None:
        """Once-per-day driver: the engine calls this per symbol, so the
        first call of a new date does the day's work and the rest no-op.

        Orders are plain MARKET at the CLOSE phase — the engine fills them
        at the next session open, which is the established no-lookahead flow.
        """
        dt = bar.dt
        if dt == self._last_seen_date:
            return
        self._last_seen_date = dt

        calendar = get_calendar(str(self.params["market"]))
        if not is_rebalance_date(dt, str(self.params["rebalance"]), calendar):
            return

        theme = get_theme(str(self.params["theme"]), self.params["themes_path"])
        universe = theme_members(theme, dt)
        targets = self.generate_targets(dt, universe, self._store())
        if not targets:
            self.persist_state()
            return

        equity = ctx.equity()
        # Scan the whole theme universe, not just targets ∪ last_targets:
        # after a state-loss restart last_targets is empty, and an exit for a
        # held-but-unselected symbol must still be evaluated.
        candidates = sorted(set(universe) | set(self._last_targets))
        current_weights: dict[str, float] = {}
        positions: dict[str, int] = {}
        for symbol in candidates:
            position = ctx.position(symbol)
            positions[symbol] = position.qty
            current_weights[symbol] = (
                position.market_value / equity if equity > 0 and position.qty > 0 else 0.0
            )

        plan = plan_rebalance(
            targets=targets,
            current_weights=current_weights,
            positions=positions,
            band=float(self.params["band"]),
        )
        ledger = self._signal_ledger()
        for intent in plan:
            target_weight = targets.get(intent.symbol, 0.0)
            signal_id = make_signal_id(
                self.name, dt, intent.symbol, intent.side, target_weight
            )
            if not ledger.claim(signal_id):
                continue
            if intent.side == "SELL":
                ctx.sell(intent.symbol, qty=intent.qty)
            else:
                ctx.buy(intent.symbol, weight=intent.weight)

        self._last_rebalance_date = dt
        self._last_targets = dict(targets)
        self.persist_state()

    def _signal_ledger(self) -> SignalLedger:
        if self._ledger is None:
            self._ledger = SignalLedger(self.name, self._state_store)
        return self._ledger

    def snapshot_state(self) -> dict:
        return {
            "last_seen_date": self._last_seen_date.isoformat() if self._last_seen_date else None,
            "last_rebalance_date": (
                self._last_rebalance_date.isoformat() if self._last_rebalance_date else None
            ),
            "last_targets": dict(self._last_targets),
        }

    def restore_state(self, state: dict) -> None:
        seen = state.get("last_seen_date")
        rebalanced = state.get("last_rebalance_date")
        self._last_seen_date = date.fromisoformat(seen) if seen else None
        self._last_rebalance_date = date.fromisoformat(rebalanced) if rebalanced else None
        self._last_targets = {
            str(symbol): float(weight)
            for symbol, weight in (state.get("last_targets") or {}).items()
        }
