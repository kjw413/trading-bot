"""Returns on a real account, where money comes and goes.

`report/metrics.py` divides by the starting cash, which is right for a
backtest and wrong here: a deposit would show up as a gain. So each interval
is measured between two snapshots with external cash flows taken out.

When the flow is unknown the interval is not guessed at. The check compares
what prices explain against what actually happened, and anything left over
means something happened that prices do not account for — a transfer, or a
trade made by hand in the app. It cannot tell those two apart, so it says so
and reports no number, the same way an under-sampled walk-forward win rate is
reported unmeasured instead of passed.
"""

from __future__ import annotations

from dataclasses import dataclass

from tradingbot.account.base import AccountSnapshot, Holding

DEFAULT_TOLERANCE = 0.01

_UNEXPLAINED = (
    "이 기간에 설명되지 않는 금액 변화가 있어서 수익률을 계산하지 않았습니다. "
    "입출금을 하셨거나, 앱에서 직접 매매하신 적이 있나요?"
)
_NO_START_VALUE = "직전 기록 시점의 평가금액이 0이라 수익률을 낼 수 없습니다."


@dataclass(frozen=True)
class IntervalReturn:
    days: int
    start_value_krw: float
    end_value_krw: float
    measured: bool
    reason: str | None
    return_pct: float | None
    price_part_pct: float | None
    fx_part_pct: float | None


def holding_return(holding: Holding) -> float | None:
    """Gain against the broker's average price, in the holding's own currency.

    Not converted to won: the purchase-time exchange rate is not in the
    balance response, so a won-denominated figure since purchase cannot be
    computed honestly. The interval figures below do carry the fx split,
    because there both rates are known.
    """
    if holding.avg_price <= 0:
        return None
    return holding.last_price / holding.avg_price - 1


def explained_change_krw(prev: AccountSnapshot, curr: AccountSnapshot) -> float:
    """Won change the two snapshots' prices and rates account for.

    Uses the earlier quantities on purpose — this is the buy-and-hold part of
    the move. Shares acquired during the interval are not explained by it,
    which is what makes the leftover a usable signal.
    """
    latest = {h.symbol: h for h in curr.holdings}
    total = 0.0
    for held in prev.holdings:
        now = latest.get(held.symbol)
        end_price_krw = (
            now.last_price * curr.rate(now.currency)
            if now is not None
            else held.last_price * curr.rate(held.currency)
        )
        start_price_krw = held.last_price * prev.rate(held.currency)
        total += held.qty * (end_price_krw - start_price_krw)
    return total


def _fx_part(prev: AccountSnapshot, curr: AccountSnapshot) -> float:
    """Weighted rate move across the currencies actually held at the start."""
    weights: dict[str, float] = {}
    for held in prev.holdings:
        weights[held.currency] = weights.get(held.currency, 0.0) + prev.holding_value_krw(held)
    total = sum(weights.values())
    if total <= 0:
        return 0.0
    return sum(
        (weight / total) * (curr.rate(cur) / prev.rate(cur) - 1)
        for cur, weight in weights.items()
    )


def interval_return(
    prev: AccountSnapshot,
    curr: AccountSnapshot,
    *,
    net_flow_krw: float | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> IntervalReturn:
    """Return between two snapshots, or a stated reason for having none."""
    if curr.as_of < prev.as_of:
        raise ValueError("interval_return: curr.as_of is earlier than prev.as_of")

    days = (curr.as_of - prev.as_of).days
    start = prev.value_krw()
    end = curr.value_krw()
    blank = dict(
        days=days, start_value_krw=start, end_value_krw=end,
        return_pct=None, price_part_pct=None, fx_part_pct=None,
    )

    if start <= 0:
        return IntervalReturn(measured=False, reason=_NO_START_VALUE, **blank)

    if net_flow_krw is None:
        leftover = (end - start) - explained_change_krw(prev, curr)
        if abs(leftover) / start > tolerance:
            return IntervalReturn(measured=False, reason=_UNEXPLAINED, **blank)
        flow = 0.0
    else:
        flow = float(net_flow_krw)

    # Flows are treated as arriving at the start of the interval. With one
    # snapshot at each end there is nothing finer to go on, and pretending
    # otherwise would dress an assumption up as precision.
    total = (end - flow) / start - 1

    # The split is multiplicative, not additive: a won return is the dollar
    # price move compounded with the rate move, so subtracting the rate move
    # from the total leaves the cross term stuck to the price part. On a +10%
    # price move with the won weakening 3.7% that error is 0.37 percentage
    # points, which is enough to make the two halves disagree with the app.
    # The consequence is that the two parts do not add up to the total, so the
    # briefing states them side by side and never claims a sum.
    fx = _fx_part(prev, curr)
    return IntervalReturn(
        days=days,
        start_value_krw=start,
        end_value_krw=end,
        measured=True,
        reason=None,
        return_pct=total,
        price_part_pct=(1 + total) / (1 + fx) - 1,
        fx_part_pct=fx,
    )
