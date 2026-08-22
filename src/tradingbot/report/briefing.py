"""Turns two account snapshots into something a non-expert can read.

Built as an ordered list of sections rather than one format string, because
M2 (news) and M3 (trade suggestions) attach by adding a name to `SECTIONS`
and a function beside the ones here — not by rewriting a template.

Every named number goes through `glossary.format_value`, so a rate is never
hand-formatted into the text; the one exception is a per-holding price, which
carries its own currency symbol and has no glossary term. Quantities print
`qty_display` verbatim: the figure on screen has to match what the Toss app
shows, and reformatting a fractional share is how that agreement breaks.

The period length appears in the first line and again in the totals. Runs are
manual and irregular, so "+1.2%" over eight days and over twenty-three days
are different statements, and the reader cannot tell them apart otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

import pandas as pd

from tradingbot.account.base import AccountSnapshot
from tradingbot.account.returns import IntervalReturn, holding_return, interval_return
from tradingbot.report import glossary

SECTIONS: tuple[str, ...] = ("summary", "totals", "holdings", "trend", "notes")

# How far the broker's own timestamp may lag our clock before the reader is
# told the numbers may not be current.
STALE_AFTER = timedelta(hours=6)

# Daily-reset leveraged ETFs. Held over more than a day, the multiple does
# not hold — which is exactly the misunderstanding plain wording has to
# prevent, and it starts the moment one of these is in the account.
_LEVERAGED = {"SOXL", "SOXS", "TECL", "TECS", "TQQQ", "SQQQ", "FNGU", "LABU", "SPXL"}

_MARKET_NAMES = {"KR": "한국", "US": "미국"}
_CURRENCY_NAMES = {"KRW": "원", "USD": "달러"}


@dataclass(frozen=True)
class _Context:
    curr: AccountSnapshot
    prev: AccountSnapshot | None
    interval: IntervalReturn | None
    price_history: dict[str, Any] | None
    now: datetime
    long_gap_days: int


def _money(value: float, currency: str) -> str:
    """A price in its own currency. Not a glossary term — it has no label."""
    if currency == "USD":
        return f"${value:,.2f}"
    if currency == "KRW":
        return f"{value:,.0f}원"
    return f"{value:,.2f} {currency}"


def _market_name(market: str) -> str:
    return _MARKET_NAMES.get(market.upper(), market)


def _day(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d")


def _minute(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


def _holds_foreign_currency(snapshot: AccountSnapshot | None) -> bool:
    if snapshot is None:
        return False
    return any(held.currency != "KRW" for held in snapshot.holdings)


def _render_summary(ctx: _Context) -> list[str]:
    if ctx.interval is None:
        return [
            "이번이 처음 기록입니다. 지금 계좌 상태를 남겨 두었고, 다음에 다시 켜면 "
            "그 사이에 무엇이 달라졌는지 함께 알려드립니다."
        ]

    days = ctx.interval.days
    if not ctx.interval.measured:
        return [
            f"지난 {days}일 동안 전체 자산이 얼마나 달라졌는지는 "
            f"{glossary.label('unmeasured')}입니다."
        ]

    change = ctx.interval.return_pct
    shown = glossary.format_value("period_return", change)
    if change > 0:
        return [f"지난 {days}일 동안 전체 자산은 {shown} 늘었습니다."]
    if change < 0:
        return [f"지난 {days}일 동안 전체 자산은 {shown} 줄었습니다."]
    return [f"지난 {days}일 동안 전체 자산은 {shown}로 거의 그대로입니다."]


def _render_totals(ctx: _Context) -> list[str]:
    total = ctx.curr.value_krw()
    lines = ["[전체]"]

    if ctx.interval is not None and ctx.prev is not None:
        lines.append(
            f"- 기간: {_day(ctx.prev.as_of)} ~ {_day(ctx.curr.as_of)} "
            f"({ctx.interval.days}일)"
        )
    else:
        lines.append(
            f"- 이 기록의 날짜: {_day(ctx.curr.as_of)} (비교할 지난 기록이 없습니다)"
        )

    lines.append(
        f"- {glossary.label('total_value')}: "
        f"{glossary.format_value('total_value', total)}원"
    )

    if ctx.interval is not None:
        lines.append(
            "- 직전 기록 시점 금액: "
            f"{glossary.format_value('total_value', ctx.interval.start_value_krw)}원"
        )
        lines.append(
            f"- {glossary.label('period_return')}: "
            f"{glossary.format_value('period_return', ctx.interval.return_pct)}"
        )
        if ctx.interval.reason:
            lines.append(f"  {ctx.interval.reason}")
        if ctx.interval.measured and _holds_foreign_currency(ctx.prev):
            lines.append(
                f"- {glossary.label('price_part')}: "
                f"{glossary.format_value('price_part', ctx.interval.price_part_pct)}"
            )
            lines.append(
                f"- {glossary.label('fx_part')}: "
                f"{glossary.format_value('fx_part', ctx.interval.fx_part_pct)}"
            )
            # The two are compounded, not added. Saying "these sum to the
            # total" would be off by the cross term — 0.4 percentage points on
            # a 10% price move with the won weakening 3.7% — so it is not said.
            lines.append("  (두 몫은 곱해져 전체가 되므로, 그냥 더한 값과는 조금 다릅니다.)")

    if total > 0:
        lines.append(
            f"- {glossary.label('cash_weight')}: "
            f"{glossary.format_value('cash_weight', ctx.curr.cash_krw() / total)}"
        )
    else:
        lines.append(
            f"- {glossary.label('cash_weight')}: {glossary.label('unmeasured')} "
            "(전체 평가금액이 0원입니다)"
        )
    return lines


def _render_holdings(ctx: _Context) -> list[str]:
    if not ctx.curr.holdings:
        return ["[종목별]", "- 지금 들고 있는 주식이 없습니다."]

    lines = ["[종목별]"]
    for held in ctx.curr.holdings:
        basis = ""
        if held.currency != "KRW":
            basis = f" ({_CURRENCY_NAMES.get(held.currency, held.currency)} 기준)"
        lines.append(
            f"- {held.symbol} ({_market_name(held.market)}) {held.qty_display}주"
            f" · 산 가격 {_money(held.avg_price, held.currency)}"
            f" · 지금 {_money(held.last_price, held.currency)}"
            f" · {glossary.label('holding_return')} "
            f"{glossary.format_value('holding_return', holding_return(held))}{basis}"
        )
    return lines


def _series_change(series: Any) -> float | None:
    """Change from the first to the last point, or nothing if it cannot be had."""
    if series is None:
        return None
    values = pd.Series(series).dropna()
    if len(values) < 2:
        return None
    first = float(values.iloc[0])
    if first <= 0:
        return None
    return float(values.iloc[-1]) / first - 1


def _render_trend(ctx: _Context) -> list[str]:
    if not ctx.price_history:
        return []

    lines = []
    for held in ctx.curr.holdings:
        change = _series_change(ctx.price_history.get(held.symbol))
        if change is None:
            continue
        lines.append(f"- {held.symbol}: {glossary.format_value('period_return', change)}")
    if not lines:
        return []
    return ["[이 기간 주가 움직임]", *lines]


def _render_notes(ctx: _Context) -> list[str]:
    notes: list[str] = []

    if _holds_foreign_currency(ctx.curr) or _holds_foreign_currency(ctx.prev):
        notes.append(
            "- 달러로 사는 주식은 환율이 달라지면 원화로 본 금액도 달라집니다. "
            "주가가 그대로여도 숫자가 움직일 수 있습니다."
        )

    if {held.symbol.upper() for held in ctx.curr.holdings} & _LEVERAGED:
        notes.append(
            "- 3배 ETF는 하루 단위로 3배라서, 여러 날을 합치면 기초지수의 정확히 "
            "3배가 아닙니다. 오래 들고 있을수록 차이가 커집니다."
        )

    if ctx.curr.fx_source != "broker":
        notes.append(
            "- 환율은 증권사가 알려준 값이 아니라 시장에서 가져온 값으로 계산했습니다."
        )

    lag = ctx.now - ctx.curr.as_of
    if lag >= STALE_AFTER:
        hours = int(lag.total_seconds() // 3600)
        notes.append(
            f"- 증권사가 알려준 기준 시각은 {_minute(ctx.curr.as_of)}입니다. "
            f"지금보다 {hours}시간 이르므로, 위 숫자는 가장 최근 값이 아닐 수 있습니다."
        )

    if ctx.interval is not None and ctx.interval.days > ctx.long_gap_days:
        notes.append(
            f"- 지난 기록과 이번 기록 사이가 {ctx.interval.days}일입니다. "
            "그 사이에 오르내린 움직임은 이 브리핑에 담지 못한 부분이 있습니다."
        )

    if not notes:
        return []
    return ["[알아둘 점]", *notes]


_RENDERERS: dict[str, Callable[[_Context], list[str]]] = {
    "summary": _render_summary,
    "totals": _render_totals,
    "holdings": _render_holdings,
    "trend": _render_trend,
    "notes": _render_notes,
}


def render_briefing(
    curr: AccountSnapshot,
    prev: AccountSnapshot | None = None,
    *,
    price_history: dict[str, Any] | None = None,
    now: datetime | None = None,
    long_gap_days: int = 14,
) -> str:
    """The whole briefing as one string, sections separated by a blank line.

    `price_history` is injected rather than read from the cache here, so this
    stays testable without disk or network. `now` is injected for the same
    reason — the staleness note otherwise depends on the wall clock.
    """
    context = _Context(
        curr=curr,
        prev=prev,
        interval=interval_return(prev, curr) if prev is not None else None,
        price_history=price_history,
        now=now if now is not None else datetime.now(curr.as_of.tzinfo),
        long_gap_days=long_gap_days,
    )
    blocks = []
    for name in SECTIONS:
        lines = _RENDERERS[name](context)
        if lines:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def split_for_telegram(text: str, limit: int = 4096) -> list[str]:
    """Break the briefing into messages Telegram will accept.

    Splits at blank lines so a section stays whole where it can. A single
    section longer than the limit is cut mid-way rather than dropped: a
    truncated section is bad, a missing one is worse.
    """
    parts: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        pieces = (
            [block]
            if len(block) <= limit
            else [block[i : i + limit] for i in range(0, len(block), limit)]
        )
        for piece in pieces:
            if not current:
                current = piece
            elif len(current) + 2 + len(piece) <= limit:
                current = f"{current}\n\n{piece}"
            else:
                parts.append(current)
                current = piece
    if current:
        parts.append(current)
    return parts or [text]
