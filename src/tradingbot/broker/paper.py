from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from tradingbot.broker.backtest import BacktestBroker
from tradingbot.broker.fees import FeeModel
from tradingbot.models import Fill, Order, OrderPhase, OrderSide, OrderStatus, OrderType, Position, TimeInForce


class PaperBroker(BacktestBroker):
    def __init__(
        self,
        name: str,
        state_dir: str | Path,
        initial_cash: float,
        market: str = "KR",
        fee_model: FeeModel | None = None,
        slippage_bps: float = 0.0,
        autosave: bool = True,
    ) -> None:
        self.name = name
        self.state_dir = Path(state_dir)
        self.state_path = self.state_dir / f"{name}.json"
        self.autosave = autosave
        self.metadata: dict[str, Any] = {}
        super().__init__(initial_cash=initial_cash, market=market, fee_model=fee_model, slippage_bps=slippage_bps)
        if self.state_path.exists():
            self.load()
        else:
            self.save()

    def save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "name": self.name,
            "market": self.market,
            "initial_cash": self.portfolio.initial_cash,
            "cash": self.portfolio.cash,
            "realized_pnl": self.portfolio.realized_pnl,
            "positions": {symbol: asdict(pos) for symbol, pos in self.portfolio.positions.items()},
            "open_orders": [_order_to_dict(order) for order in self._open_orders if order.status is OrderStatus.OPEN],
            "fills": [_fill_to_dict(fill) for fill in self.fills],
            "rejected_orders": [_order_to_dict(order) for order in self.rejected_orders],
            "expired_orders": [_order_to_dict(order) for order in self.expired_orders],
            "metadata": self.metadata,
        }
        self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> None:
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.market = data.get("market", self.market).upper()
        self.portfolio.initial_cash = float(data.get("initial_cash", self.portfolio.initial_cash))
        self.portfolio.cash = float(data.get("cash", self.portfolio.initial_cash))
        self.portfolio.realized_pnl = float(data.get("realized_pnl", 0.0))
        self.portfolio.positions = {
            symbol: Position(
                symbol=payload["symbol"],
                qty=int(payload["qty"]),
                avg_price=float(payload["avg_price"]),
                last_price=float(payload.get("last_price", payload["avg_price"])),
            )
            for symbol, payload in data.get("positions", {}).items()
        }
        self._open_orders = [_order_from_dict(item) for item in data.get("open_orders", [])]
        self.fills = [_fill_from_dict(item) for item in data.get("fills", [])]
        self.rejected_orders = [_order_from_dict(item) for item in data.get("rejected_orders", [])]
        self.expired_orders = [_order_from_dict(item) for item in data.get("expired_orders", [])]
        self.metadata = dict(data.get("metadata", {}))

    def submit(self, order: Order) -> Order:
        result = super().submit(order)
        self._save_if_enabled()
        return result

    def cancel(self, order_id: str) -> bool:
        result = super().cancel(order_id)
        if result:
            self._save_if_enabled()
        return result

    def on_session_open(self, dt: date, opens: dict[str, float]) -> list[Fill]:
        fills = super().on_session_open(dt, opens)
        self._save_if_enabled()
        return fills

    def on_intraday_bars(self, dt: date, bars) -> list[Fill]:
        fills = super().on_intraday_bars(dt, bars)
        self._save_if_enabled()
        return fills

    def on_session_close(self, dt: date, bars) -> list[Fill]:
        fills = super().on_session_close(dt, bars)
        self._save_if_enabled()
        return fills

    def expire_day_orders(self, dt: date) -> list[Order]:
        expired = super().expire_day_orders(dt)
        if expired:
            self._save_if_enabled()
        return expired

    def mark_to_market(self, prices: dict[str, float]) -> None:
        super().mark_to_market(prices)
        self._save_if_enabled()

    def set_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value
        self._save_if_enabled()

    def next_order_number(self) -> int:
        ids = [order.id for order in self._open_orders]
        ids.extend(order.id for order in self.rejected_orders)
        ids.extend(order.id for order in self.expired_orders)
        ids.extend(fill.order_id for fill in self.fills)
        max_id = 0
        for order_id in ids:
            if order_id.startswith("O") and order_id[1:].isdigit():
                max_id = max(max_id, int(order_id[1:]))
        return max_id + 1

    def _save_if_enabled(self) -> None:
        if self.autosave:
            self.save()


def _date_to_str(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _date_from_str(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _order_to_dict(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
        "symbol": order.symbol,
        "side": order.side.value,
        "qty": order.qty,
        "order_type": order.order_type.value,
        "tif": order.tif.value,
        "created_at": _date_to_str(order.created_at),
        "created_phase": order.created_phase.value if order.created_phase else None,
        "limit_price": order.limit_price,
        "stop_price": order.stop_price,
        "status": order.status.value,
        "reject_reason": order.reject_reason,
    }


def _order_from_dict(data: dict[str, Any]) -> Order:
    return Order(
        id=data["id"],
        symbol=data["symbol"],
        side=OrderSide(data["side"]),
        qty=int(data["qty"]),
        order_type=OrderType(data.get("order_type", OrderType.MARKET.value)),
        tif=TimeInForce(data.get("tif", TimeInForce.DAY.value)),
        created_at=_date_from_str(data.get("created_at")),
        created_phase=OrderPhase(data["created_phase"]) if data.get("created_phase") else None,
        limit_price=data.get("limit_price"),
        stop_price=data.get("stop_price"),
        status=OrderStatus(data.get("status", OrderStatus.OPEN.value)),
        reject_reason=data.get("reject_reason"),
    )


def _fill_to_dict(fill: Fill) -> dict[str, Any]:
    return {
        "order_id": fill.order_id,
        "symbol": fill.symbol,
        "side": fill.side.value,
        "qty": fill.qty,
        "price": fill.price,
        "fee": fill.fee,
        "dt": _date_to_str(fill.dt),
    }


def _fill_from_dict(data: dict[str, Any]) -> Fill:
    fill_dt = _date_from_str(data.get("dt"))
    if fill_dt is None:
        raise ValueError("Fill dt is required")
    return Fill(
        order_id=data["order_id"],
        symbol=data["symbol"],
        side=OrderSide(data["side"]),
        qty=int(data["qty"]),
        price=float(data["price"]),
        fee=float(data["fee"]),
        dt=fill_dt,
    )

