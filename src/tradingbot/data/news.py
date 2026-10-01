"""Pure news models and transformations for the weekly briefing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from tradingbot.account.base import Holding
from tradingbot.data.credentials import MissingCredentialsError, require_env
from tradingbot.data.fundamentals import DartClient, Disclosure, requests_transport

DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
PER_SYMBOL = 3
TOTAL_CAP = 12
NEWS_SCHEMA_VERSION = 1
NEWS_DIRNAME = "news"
NEWS_FILENAME = "latest.json"
CAUSAL = ("때문에", "덕분에", "영향으로", "로 인해", "여파로", "탓에", "때문인지")

# These products do not have company news of their own. Fetch representative
# constituent news only for the products whose mapping is explicitly listed.
UNDERLYING: dict[str, tuple[str, tuple[str, ...]]] = {
    "SOXL": ("반도체 지수", ("NVDA", "AVGO", "AMD")),
    "SOXS": ("반도체 지수", ("NVDA", "AVGO", "AMD")),
    "TQQQ": ("나스닥 100 지수", ("AAPL", "MSFT", "NVDA")),
    "SQQQ": ("나스닥 100 지수", ("AAPL", "MSFT", "NVDA")),
    "GGLL": ("Alphabet", ("GOOGL",)),
}

# A ticker alone cannot reliably distinguish a basket from a company. Keep an
# explicit list of products known to need a constituent mapping, and never guess
# at their constituents.
_UNMAPPED_BASKETS = frozenset({"SPCX", "TECL", "TECS", "FNGU", "LABU", "SPXL"})


class DartFetcher(Protocol):
    def __call__(
        self, corp_code: str, start: date, end: date
    ) -> Sequence[Disclosure]: ...


class YahooFetcher(Protocol):
    def __call__(self, ticker: str) -> list[dict]: ...


class UnderlyingOutcome(Enum):
    DIRECT = "direct"
    MAPPED = "mapped"
    UNMAPPED_LEVERAGED = "unmapped_leveraged"


@dataclass(frozen=True)
class UnderlyingResolution:
    outcome: UnderlyingOutcome
    symbols: tuple[str, ...]
    via: str = ""
    reason: str = ""


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


def resolve_underlying(symbol: str) -> UnderlyingResolution:
    mapped = UNDERLYING.get(symbol)
    if mapped is not None:
        via, symbols = mapped
        return UnderlyingResolution(
            outcome=UnderlyingOutcome.MAPPED,
            symbols=symbols,
            via=via,
        )

    if symbol in _UNMAPPED_BASKETS:
        return UnderlyingResolution(
            outcome=UnderlyingOutcome.UNMAPPED_LEVERAGED,
            symbols=(),
            reason=(
                "이 종목은 개별 회사가 아니라 여러 종목을 묶은 상품이고, "
                "등록된 구성 종목 매핑이 없습니다."
            ),
        )

    return UnderlyingResolution(
        outcome=UnderlyingOutcome.DIRECT,
        symbols=(symbol,),
    )


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


def _record_reason(reasons: dict[str, str], source: str, reason: str) -> None:
    previous = reasons.get(source)
    if previous is None:
        reasons[source] = reason
    elif reason not in previous.split(" | "):
        reasons[source] = f"{previous} | {reason}"


def _failure_reason(symbol: str, exc: Exception) -> str:
    detail = str(exc).strip() or type(exc).__name__
    return f"{symbol}: {detail}"


def fetch_news(
    holdings: Sequence[Holding],
    *,
    since: date,
    until: date,
    dart: DartFetcher | None = None,
    yahoo: YahooFetcher | None = None,
    corp_codes: Mapping[str, str] | None = None,
) -> NewsResult:
    """Collect held-symbol news without letting an optional source stop the run."""
    raw_items: list[NewsItem] = []
    failures: dict[str, str] = {}
    skipped: dict[str, str] = {}
    codes = {} if corp_codes is None else corp_codes

    resolved = tuple(
        (holding, resolve_underlying(holding.symbol)) for holding in holdings
    )
    for holding, resolution in resolved:
        market = holding.market.upper()

        if resolution.outcome is UnderlyingOutcome.UNMAPPED_LEVERAGED:
            _record_reason(
                skipped,
                "yahoo",
                f"{holding.symbol}: {resolution.reason}",
            )
            continue

        if market == "KR":
            if dart is None:
                _record_reason(skipped, "dart", "DART fetcher is not configured.")
                continue

            for source_symbol in resolution.symbols:
                corp_code = codes.get(source_symbol)
                if not corp_code:
                    _record_reason(
                        skipped,
                        "dart",
                        f"{source_symbol}: no DART corp code is configured.",
                    )
                    continue
                try:
                    disclosures = dart(corp_code, since, until)
                    raw_items.extend(dart_items(disclosures, holding.symbol))
                except Exception as exc:  # noqa: BLE001 - source failures are data
                    _record_reason(
                        failures, "dart", _failure_reason(source_symbol, exc)
                    )
            continue

        if market == "US":
            if yahoo is None:
                _record_reason(skipped, "yahoo", "Yahoo fetcher is not configured.")
                continue

            for source_symbol in resolution.symbols:
                try:
                    payload = yahoo(source_symbol)
                    raw_items.extend(
                        yahoo_items(
                            payload,
                            holding.symbol,
                            via=(
                                source_symbol
                                if resolution.outcome is UnderlyingOutcome.MAPPED
                                else ""
                            ),
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - source failures are data
                    _record_reason(
                        failures, "yahoo", _failure_reason(source_symbol, exc)
                    )
            continue

        _record_reason(
            skipped,
            "market",
            f"{holding.symbol}: unsupported market {holding.market!r}.",
        )

    windowed = within(tuple(raw_items), since=since, until=until)
    items, dropped = cap(windowed)
    return NewsResult(
        items=items,
        failures=failures,
        dropped=dropped,
        skipped=skipped,
    )


def build_fetchers() -> tuple[
    DartFetcher | None, YahooFetcher | None, dict[str, str]
]:
    """Build optional live sources without making a network request."""
    try:
        dart_api_key = require_env(
            "DART_API_KEY",
            hint=(
                "Get a free key at https://opendart.fss.or.kr and set it as an "
                "environment variable; never commit it to the repository."
            ),
        )
    except MissingCredentialsError:
        dart: DartFetcher | None = None
    else:
        dart = DartClient(dart_api_key, requests_transport()).disclosure_list

    try:
        import yfinance
    except ImportError:
        yahoo: YahooFetcher | None = None
    else:

        def yahoo(ticker: str) -> list[dict]:
            return yfinance.Ticker(ticker).get_news(count=10)

    return dart, yahoo, {}


def _news_path(root: str | Path) -> Path:
    return Path(root) / NEWS_DIRNAME / NEWS_FILENAME


def save_news(result: NewsResult, root: str | Path) -> Path:
    path = _news_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": NEWS_SCHEMA_VERSION,
        "items": [
            {
                "symbol": item.symbol,
                "source": item.source,
                "published_at": item.published_at.isoformat(),
                "title": item.title,
                "url": item.url,
                "via": item.via,
            }
            for item in result.items
        ],
        "failures": result.failures,
        "dropped": result.dropped,
        "skipped": result.skipped,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_news(root: str | Path) -> NewsResult | None:
    path = _news_path(root)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["schema_version"] != NEWS_SCHEMA_VERSION:
            raise ValueError("unknown news schema")
        return NewsResult(
            items=tuple(
                NewsItem(
                    symbol=row["symbol"],
                    source=row["source"],
                    published_at=date.fromisoformat(row["published_at"]),
                    title=row["title"],
                    url=row["url"],
                    via=row.get("via", ""),
                )
                for row in payload["items"]
            ),
            failures=dict(payload["failures"]),
            dropped={key: int(value) for key, value in payload["dropped"].items()},
            skipped=dict(payload["skipped"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Corrupt news result: {path}") from exc


def find_causal_terms(text: str) -> list[str]:
    """Every banned causal connective present in `text`, for enforcement tests."""
    return [term for term in CAUSAL if term in text]
