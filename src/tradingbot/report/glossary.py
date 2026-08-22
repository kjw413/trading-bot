"""One place that decides how numbers are named for a non-expert reader.

Requirement 3 asks that the plain wording hold not just in this milestone but
in every report and alert that comes later. Wording spread across renderers
drifts apart the moment a second renderer exists, so it lives here and a test
enforces it: anything user-facing goes through these labels, and the jargon
list must not appear in the output at all.

There is deliberately no "allowed if you explain it" escape hatch. An
exception makes the check unenforceable, and the concepts that need saying
are better served by a new plain name than by a glossary footnote.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


def _pct(value: float) -> str:
    return f"{value:+.1%}".replace("+-", "-")


def _pct_unsigned(value: float) -> str:
    """A share of the whole, not a change.

    `_pct` signs everything, which is right for a return and wrong for a
    weight: "현금 비중 +25.5%" reads as a gain of 25.5% rather than a quarter
    of the account sitting in cash.
    """
    return f"{value:.1%}"


def _plain(value: float) -> str:
    return f"{value:,.0f}"


@dataclass(frozen=True)
class Term:
    label: str
    one_line: str
    fmt: Callable[[float], str]


TERMS: dict[str, Term] = {
    "total_return": Term(
        "전체 수익률",
        "처음 넣은 돈 대비 지금까지 얼마나 늘었는지",
        _pct,
    ),
    "period_return": Term(
        "이번 기간 수익률",
        "지난번에 확인한 뒤로 얼마나 달라졌는지",
        _pct,
    ),
    "holding_return": Term(
        "종목 수익률",
        "이 종목을 산 가격 대비 지금 가격이 얼마나 달라졌는지",
        _pct,
    ),
    "price_part": Term(
        "주가가 움직인 몫",
        "수익률 중 주가 자체가 오르내려서 생긴 부분",
        _pct,
    ),
    "fx_part": Term(
        "환율이 움직인 몫",
        "수익률 중 원달러 환율이 달라져서 생긴 부분",
        _pct,
    ),
    "cash_weight": Term(
        "현금 비중",
        "전체 자산 중 아직 투자하지 않고 남겨둔 돈의 비율",
        _pct_unsigned,
    ),
    "total_value": Term(
        "전체 평가금액",
        "지금 계좌에 있는 돈과 주식을 원화로 모두 더한 값",
        _plain,
    ),
    "unmeasured": Term(
        "측정 불가",
        "숫자를 믿을 수 없어서 계산하지 않았다는 뜻",
        _pct,
    ),
}

# Jargon that must never reach the reader as-is. Matched case-insensitively
# against the rendered text.
BANNED: tuple[str, ...] = (
    "sharpe",
    "샤프",
    "mdd",
    "drawdown",
    "드로다운",
    "exposure",
    "익스포저",
    "profit factor",
    "equity curve",
    "cagr",
    "twr",
    "volatility",
    "변동성 역가중",
    "alpha",
    "beta",
    "rebalanc",
)


def label(key: str) -> str:
    """Reader-facing name. Raises on an unknown key, on purpose.

    A typo that renders as an empty string would ship a report with a blank
    where a number's name should be, and nothing would fail.
    """
    return TERMS[key].label


def explain(key: str) -> str:
    return TERMS[key].one_line


def format_value(key: str, value: float | None) -> str:
    """Format a value, or say it could not be measured.

    None is not zero. A missing return rendered as +0.0% is precisely the
    false statement this project refuses to make.
    """
    if value is None:
        return label("unmeasured")
    return TERMS[key].fmt(value)


def find_banned_terms(text: str) -> list[str]:
    """Every jargon term present in `text`, for the enforcement test."""
    lowered = (text or "").lower()
    return [term for term in BANNED if term in lowered]
