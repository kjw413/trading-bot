"""Theme multifactor strategy: combined factor score -> target weights -> orders.

Decision flow (spec §9):
    theme members at dt -> factor scores (weights config drives WHICH factors)
    -> standardize -> combine -> top N -> equal or inverse-vol weights
    -> regime exposure scaling -> concentration/cash constraints -> targets

The factor-weights config is the single source of truth for which factors
run: every key is resolved through the registry up front, so a typo'd name
raises immediately instead of being silently zero-weighted.

`generate_targets` separates two things an empty portfolio could mean:
`None` is "I cannot judge" (stale or missing data) and the caller must skip
rebalancing rather than trade on nothing; `{}` is "I judged, and the answer
is hold nothing". Conflating them is how a defensive filter ends up carrying
risky positions through the fall it was built to avoid.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

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
        # Trading days of price staleness tolerated before a rebalance is
        # skipped. 3 absorbs a long weekend plus one failed collection run
        # while still catching a pipeline that has been dead for a week.
        # Negative disables the check.
        "max_staleness_days": 3,
        "min_factors": 1,
        # 사용할 팩터를 명시적으로 제한한다. None이면 [factor_weights]의 모든
        # 키를 쓴다(현행). 미국처럼 수급·가치 패널이 없는 시장은 여기에
        # 모멘텀만 적어, 조용히 퇴화하는 대신 무엇을 돌리는지 선언한다.
        "factors": None,
        # 종가가 자기 N일 이동평균 아래인 자산을 선정에서 제외한다. 0이면 비활성.
        # 상대 모멘텀만으로는 전 자산 하락장에서 "가장 덜 나쁜 것"을 강제로 사게
        # 된다 — 채권·원자재가 섞인 ETF 유니버스에서 특히 위험하다.
        "abs_momentum_ma_days": 0,
        # 국면 필터가 위험자산에서 빼낸 비중을 어디에 둘지. None이면 현금이다.
        # research.toml은 safe_asset = "IEF"를 선언해 두었지만 지금까지 아무도
        # 읽지 않았다 — 방어 구간의 자금이 0% 현금으로 놀면 방어 비용이
        # 그만큼 커진다. 안전자산도 자기 절대 모멘텀 검사를 통과해야 쓴다:
        # 2022년처럼 주식과 채권이 함께 빠지면 채권도 피난처가 아니다.
        "safe_asset": None,
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
        """[factor_weights] keys drive which factors run; typos fail loudly.

        `factors` narrows that set without moving the weights: one config file
        can carry both markets' weights while each market declares the subset
        it actually has data for.
        """
        if self._factor_weights is None:
            raw = self.research.get("factor_weights", {})
            if not raw:
                raise ValueError("research config has no [factor_weights] section")
            selected = self.params.get("factors")
            if selected is not None:
                missing = [name for name in selected if name not in raw]
                if missing:
                    available = ", ".join(sorted(raw))
                    raise ValueError(
                        f"factors {missing} have no weight in [factor_weights]. "
                        f"Available: {available}"
                    )
                raw = {name: raw[name] for name in selected}
            for factor_name in raw:
                get_factor(factor_name)  # raises ValueError on unknown names
            self._factor_weights = {name: float(value) for name, value in raw.items()}
        return self._factor_weights

    def generate_targets(
        self, dt: date, universe: Sequence[str], data_store
    ) -> dict[str, float] | None:
        """Target equity weights as of dt's close.

        `None` means the data does not support a judgement and the caller must
        skip rebalancing. `{}` means the judgement is "hold nothing".
        """
        if not universe:
            return None

        if self._is_price_data_stale(dt, universe, data_store):
            return None

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
            return None

        combined = self._apply_absolute_momentum(dt, combined, data_store)
        selected = select_top(combined, int(self.params["top_n"]))
        if not selected:
            # Every name failed its own trend test. That is a decision, not a
            # gap in the data — the scores above were computable. Skipping here
            # would carry the existing risky book straight through the decline
            # this filter exists to sidestep.
            LOGGER.info(
                "theme_multifactor: every name is below its own moving average at %s; "
                "going defensive",
                dt,
            )
            return self._defensive_targets(dt, data_store)

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

        # Park what the regime filter took off the table, rather than letting it
        # sit in cash earning nothing for the length of a bear market.
        freed = sum(base.values()) - sum(scaled.values())
        safe = self._safe_asset(dt, data_store)
        if safe is not None and freed > 1e-9:
            scaled[safe] = scaled.get(safe, 0.0) + freed

        return self._constrain(scaled)

    def _constrain(self, weights: dict[str, float]) -> dict[str, float]:
        limits = self.research.get("risk_limits", {})
        return apply_constraints(
            weights,
            max_weight=float(limits.get("max_position_weight", 0.40)),
            cash_buffer=float(limits.get("min_cash_weight", 0.02)),
        )

    def _safe_asset(self, dt: date, data_store) -> str | None:
        """The configured safe asset, if it is currently worth hiding in.

        A safe asset in its own downtrend is not a refuge — 2022 sank stocks
        and bonds together — so it faces the same absolute-momentum test every
        other holding does. When the test is switched off, so is this check.
        """
        safe = self.params.get("safe_asset")
        if not safe:
            return None
        symbol = str(safe).upper()
        if not self._above_moving_average(dt, symbol, data_store):
            LOGGER.info(
                "theme_multifactor: safe asset %s is below its own moving average at %s; "
                "holding cash instead",
                symbol,
                dt,
            )
            return None
        return symbol

    def _defensive_targets(self, dt: date, data_store) -> dict[str, float]:
        """Hold the safe asset if it is holding up, otherwise hold cash."""
        safe = self._safe_asset(dt, data_store)
        if safe is None:
            return {}
        return self._constrain({safe: 1.0})

    def _apply_absolute_momentum(self, dt: date, scores: pd.Series, data_store) -> pd.Series:
        """NaN out names trading below their own moving average.

        Relative momentum ranks names against each other, so in a broad
        selloff it still buys the least-bad asset. This is the per-asset
        floor: a name has to be in its own uptrend to be eligible at all.

        Exclusion is expressed as NaN so `select_top` drops it the same way
        it drops an unscoreable name. A name with less history than the
        window is excluded too — admitting it would let a new listing bypass
        the filter entirely.
        """
        ma_days = int(self.params["abs_momentum_ma_days"])
        if ma_days <= 0:
            return scores

        filtered = scores.copy()
        for symbol in scores.index:
            if pd.isna(scores.loc[symbol]):
                continue
            if not self._above_moving_average(dt, symbol, data_store):
                filtered.loc[symbol] = float("nan")
        return filtered

    def _above_moving_average(self, dt: date, symbol: str, data_store) -> bool:
        """Is this name in its own uptrend? True when the filter is disabled.

        Shared by the selection filter and the safe-asset check so the two can
        never drift into disagreeing about what an uptrend is.
        """
        ma_days = int(self.params["abs_momentum_ma_days"])
        if ma_days <= 0:
            return True
        try:
            history = data_store.price_history(symbol, dt, ma_days)
        except (FileNotFoundError, KeyError):
            return False
        closes = history["close"].dropna()
        if len(closes) < ma_days:
            return False
        return float(closes.iloc[-1]) > float(closes.mean())

    def _is_price_data_stale(self, dt: date, universe: Sequence[str], data_store) -> bool:
        """True when the newest bar anywhere in the universe is too old to trade on.

        The all-NaN score check cannot catch this: a cache frozen a week ago
        still produces perfectly computable factor scores, so a paper-trading
        deployment whose update job died would keep rebalancing on last week's
        prices. Freshness is judged across the whole universe rather than
        per symbol — one halted ticker is not a dead pipeline.

        `max_staleness_days` counts trading days; a negative value disables
        the check.
        """
        max_stale = int(self.params["max_staleness_days"])
        if max_stale < 0:
            return False

        newest: date | None = None
        for symbol in universe:
            try:
                history = data_store.price_history(symbol, dt, 1)
            except (FileNotFoundError, KeyError):
                continue
            if history.empty:
                continue
            last = history.index[-1].date()
            if newest is None or last > newest:
                newest = last
        if newest is None:
            return False  # nothing at all: the all-NaN score gate reports it

        # trading_days is inclusive of both ends, so a same-day bar gives 1.
        gap = max(0, len(get_calendar(str(self.params["market"])).trading_days(newest, dt)) - 1)
        if gap > max_stale:
            LOGGER.warning(
                "theme_multifactor: newest price bar is %s, %s trading days before %s "
                "(limit %s); skipping rebalance rather than trading on stale data",
                newest,
                gap,
                dt,
                max_stale,
            )
            return True
        return False

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
        if targets is None:
            # Cannot judge — hold whatever is held rather than trade on nothing.
            self.persist_state()
            return
        # `{}` is a judgement: liquidate. plan_rebalance turns it into sells
        # for everything currently held.

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
            try:
                if intent.side == "SELL":
                    ctx.sell(intent.symbol, qty=intent.qty)
                else:
                    ctx.buy(intent.symbol, weight=intent.weight)
            except Exception:
                LOGGER.exception(
                    "theme_multifactor: order submission failed for %s %s; "
                    "continuing with the rest of the plan",
                    intent.side,
                    intent.symbol,
                )
                continue

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
