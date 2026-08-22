"""Pure news models and transformations for the weekly briefing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Sequence

from tradingbot.data.fundamentals import Disclosure

DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
PER_SYMBOL = 3
TOTAL_CAP = 12
CAUSAL = ("때문에", "덕분에", "영향으로", "로 인해", "여파로", "탓에", "때문인지")


@dataclass(frozen=True)
class NewsItem:
    symbol: str
    source: str
    published_at: date
    title: str
    url: str
    via: str = ""


@dataclass(frozen=True)
class NewsResult:
    items: tuple[NewsItem, ...]
    failures: dict[str, str]
    dropped: dict[str, int]
    skipped: dict[str, str]

    def for_symbol(self, symbol: str) -> tuple[NewsItem, ...]:
        return tuple(item for item in self.items if item.symbol == symbol)


def dart_items(disclosures: Sequence[Disclosure], symbol: str) -> tuple[NewsItem, ...]:
    return tuple(
        NewsItem(
            symbol=symbol,
            source="dart",
            published_at=disclosure.rcept_dt,
            title=disclosure.report_name,
            url=DART_VIEWER_URL.format(rcept_no=disclosure.rcept_no),
        )
        for disclosure in disclosures
    )


def yahoo_items(
    payload: list[dict], symbol: str, *, via: str = ""
) -> tuple[NewsItem, ...]:
    if not isinstance(payload, list):
        raise TypeError("Yahoo news payload must be a list")

    items: list[NewsItem] = []
    for index, entry in enumerate(payload):
        try:
            content = entry["content"]
            title = content["title"]
            published = content["pubDate"]
            url = content["canonicalUrl"]["url"]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"unexpected Yahoo news item shape at index {index}") from exc

        if not isinstance(title, str) or not title:
            raise ValueError(f"Yahoo news item {index} has no title")
        if not isinstance(published, str):
            raise ValueError(f"Yahoo news item {index} has no publication date")
        if not isinstance(url, str) or not url:
            raise ValueError(f"Yahoo news item {index} has no URL")

        try:
            published_at = datetime.fromisoformat(
                published.replace("Z", "+00:00")
            ).date()
        except ValueError as exc:
            raise ValueError(
                f"Yahoo news item {index} has an invalid publication date"
            ) from exc

        items.append(
            NewsItem(
                symbol=symbol,
                source="yahoo",
                published_at=published_at,
                title=title,
                url=url,
                via=via,
            )
        )

    return tuple(items)


def within(items, *, since: date, until: date) -> tuple[NewsItem, ...]:
    return tuple(item for item in items if since <= item.published_at <= until)


def cap(
    items, *, per_symbol=PER_SYMBOL, total=TOTAL_CAP
) -> tuple[tuple[NewsItem, ...], dict[str, int]]:
    kept: list[NewsItem] = []
    kept_per_symbol: dict[str, int] = {}
    dropped: dict[str, int] = {}

    for item in sorted(items, key=lambda candidate: candidate.published_at, reverse=True):
        symbol_count = kept_per_symbol.get(item.symbol, 0)
        if symbol_count >= per_symbol or len(kept) >= total:
            dropped[item.symbol] = dropped.get(item.symbol, 0) + 1
            continue
        kept.append(item)
        kept_per_symbol[item.symbol] = symbol_count + 1

    return tuple(kept), dropped


def find_causal_terms(text: str) -> list[str]:
    """Every banned causal connective present in `text`, for enforcement tests."""
    return [term for term in CAUSAL if term in text]
