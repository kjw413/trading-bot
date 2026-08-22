from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tradingbot.data.fundamentals import Disclosure
from tradingbot.data.news import (
    CAUSAL,
    DART_VIEWER_URL,
    UNDERLYING,
    NewsItem,
    UnderlyingOutcome,
    cap,
    dart_items,
    find_causal_terms,
    resolve_underlying,
    within,
    yahoo_items,
)


def news_item(symbol: str, published_at: date, title: str) -> NewsItem:
    return NewsItem(
        symbol=symbol,
        source="dart",
        published_at=published_at,
        title=title,
        url=f"https://example.test/{symbol}/{title}",
    )


def yahoo_payload(filename: str) -> list[dict]:
    path = Path(__file__).parent / "data" / filename
    return json.loads(path.read_text(encoding="utf-8"))


class TestDartItems:
    DISCLOSURES = (
        Disclosure(
            rcept_no="20260821000123",
            report_name="[정정] 반기보고서 (2026.06)",
            rcept_dt=date(2026, 8, 21),
        ),
        Disclosure(
            rcept_no="20260818000456",
            report_name="주요사항보고서(자기주식취득결정)",
            rcept_dt=date(2026, 8, 18),
        ),
    )

    def test_every_disclosure_becomes_one_item(self):
        items = dart_items(self.DISCLOSURES, "005930")

        assert len(items) == len(self.DISCLOSURES)
        assert all(item.symbol == "005930" for item in items)
        assert all(item.source == "dart" for item in items)

    def test_the_title_is_the_filing_name_unchanged(self):
        items = dart_items(self.DISCLOSURES, "005930")

        assert [item.title for item in items] == [
            disclosure.report_name for disclosure in self.DISCLOSURES
        ]

    def test_the_url_is_built_from_the_receipt_number(self):
        items = dart_items(self.DISCLOSURES, "005930")

        assert [item.url for item in items] == [
            DART_VIEWER_URL.format(rcept_no=disclosure.rcept_no)
            for disclosure in self.DISCLOSURES
        ]

    def test_the_date_is_the_receipt_date(self):
        items = dart_items(self.DISCLOSURES, "005930")

        assert [item.published_at for item in items] == [
            disclosure.rcept_dt for disclosure in self.DISCLOSURES
        ]

    def test_an_empty_filing_list_is_an_empty_result_not_a_failure(self):
        assert dart_items([], "005930") == ()


class TestYahooItems:
    def test_every_article_in_the_sample_becomes_one_item(self):
        payload = yahoo_payload("yahoo_news_sample.json")

        items = yahoo_items(payload, "AAPL")

        assert len(items) == len(payload)
        assert all(item.symbol == "AAPL" for item in items)
        assert all(item.source == "yahoo" for item in items)

    def test_the_title_date_and_url_come_from_the_response(self):
        payload = yahoo_payload("yahoo_news_sample.json")

        items = yahoo_items(payload, "AAPL", via="Apple")

        assert [item.title for item in items] == [
            entry["content"]["title"] for entry in payload
        ]
        assert [item.published_at.isoformat() for item in items] == [
            entry["content"]["pubDate"][:10] for entry in payload
        ]
        assert [item.url for item in items] == [
            entry["content"]["canonicalUrl"]["url"] for entry in payload
        ]
        assert all(item.via == "Apple" for item in items)

    def test_a_video_with_an_empty_display_time_still_maps_from_pub_date(self):
        payload = yahoo_payload("yahoo_news_video_sample.json")
        video_index = next(
            index
            for index, entry in enumerate(payload)
            if entry["content"]["contentType"] == "VIDEO"
            and entry["content"]["displayTime"] == ""
        )

        items = yahoo_items(payload, "NVDA")

        # The recorded Yahoo video has an empty displayTime but a usable pubDate.
        assert items[video_index].published_at == date(2026, 8, 21)
        assert items[video_index].title == payload[video_index]["content"]["title"]

    def test_an_unexpected_shape_raises_instead_of_returning_nothing(self):
        content = yahoo_payload("yahoo_news_sample.json")[0]["content"]
        malformed_payloads = (
            {"content": content},
            [
                {
                    "id": "missing-title",
                    "content": {
                        key: value
                        for key, value in content.items()
                        if key != "title"
                    },
                }
            ],
            [{"id": "missing-url", "content": {**content, "canonicalUrl": {}}}],
        )

        for payload in malformed_payloads:
            with pytest.raises((KeyError, TypeError, ValueError)):
                yahoo_items(payload, "AAPL")


class TestWindowAndCap:
    def test_items_before_the_window_are_excluded(self):
        items = (
            news_item("AAPL", date(2026, 7, 31), "before"),
            news_item("AAPL", date(2026, 8, 1), "first day"),
            news_item("AAPL", date(2026, 8, 7), "last day"),
            news_item("AAPL", date(2026, 8, 8), "after"),
        )

        result = within(items, since=date(2026, 8, 1), until=date(2026, 8, 7))

        assert [item.title for item in result] == ["first day", "last day"]

    def test_the_newest_items_survive_the_cap(self):
        items = (
            news_item("AAPL", date(2026, 8, 18), "oldest"),
            news_item("AAPL", date(2026, 8, 21), "newest"),
            news_item("AAPL", date(2026, 8, 19), "older"),
            news_item("AAPL", date(2026, 8, 20), "newer"),
        )

        kept, _ = cap(items, per_symbol=2, total=12)

        assert [item.title for item in kept] == ["newest", "newer"]

    def test_what_the_cap_dropped_is_counted_per_symbol(self):
        items = tuple(
            news_item("AAPL", date(2026, 8, day), f"AAPL {day}")
            for day in range(18, 22)
        ) + tuple(
            news_item("MSFT", date(2026, 8, day), f"MSFT {day}")
            for day in range(19, 22)
        )

        _, dropped = cap(items, per_symbol=2, total=12)

        assert dropped == {"AAPL": 2, "MSFT": 1}

    def test_nothing_is_dropped_silently(self):
        items = tuple(
            news_item(symbol, date(2026, 8, day), f"{symbol} {day}")
            for symbol in ("AAPL", "MSFT", "NVDA")
            for day in (20, 21)
        )

        kept, dropped = cap(items, per_symbol=2, total=4)

        # A Telegram-size cap must not make a partial list look complete.
        assert len(kept) + sum(dropped.values()) == len(items)
        assert sum(dropped.values()) == 2


class TestUnderlyingMapping:
    def test_a_leveraged_etf_is_mapped_to_its_index_constituents(self):
        assert UNDERLYING == {
            "SOXL": ("반도체 지수", ("NVDA", "AVGO", "AMD")),
            "SOXS": ("반도체 지수", ("NVDA", "AVGO", "AMD")),
            "TQQQ": ("나스닥 100 지수", ("AAPL", "MSFT", "NVDA")),
            "SQQQ": ("나스닥 100 지수", ("AAPL", "MSFT", "NVDA")),
        }

        resolution = resolve_underlying("SOXL")

        assert resolution.outcome is UnderlyingOutcome.MAPPED
        assert resolution.symbols == ("NVDA", "AVGO", "AMD")

    def test_a_mapped_item_records_where_it_came_from(self):
        resolution = resolve_underlying("SOXL")
        payload = yahoo_payload("yahoo_news_sample.json")[:1]

        items = yahoo_items(payload, "SOXL", via=resolution.via)

        assert items[0].via == "반도체 지수"

    def test_an_unmapped_etf_is_not_guessed_at(self):
        direct = resolve_underlying("AAPL")
        unmapped = resolve_underlying("TECL")

        # TECL is leveraged but has no row in the hand-written constituent table.
        assert direct.outcome is UnderlyingOutcome.DIRECT
        assert direct.symbols == ("AAPL",)
        assert unmapped.outcome is UnderlyingOutcome.UNMAPPED_LEVERAGED
        assert unmapped.symbols == ()
        assert unmapped.reason


class TestCausalTerms:
    def test_text_without_a_causal_connective_has_no_matches(self):
        assert find_causal_terms("이번 주 주가는 2% 올랐습니다.") == []

    def test_one_causal_connective_is_found(self):
        assert find_causal_terms("뉴스의 여파로 주가가 움직였습니다.") == ["여파로"]

    def test_every_causal_connective_in_the_text_is_found_in_policy_order(self):
        text = "실적 탓에 하락했고, 수급 때문에 흔들렸으며, 환율 영향으로 반등했습니다."

        assert find_causal_terms(text) == ["때문에", "영향으로", "탓에"]
        assert all(term in CAUSAL for term in find_causal_terms(text))

    def test_a_repeated_causal_connective_is_reported_once(self):
        text = "첫 소식 때문에 올랐고, 둘째 소식 때문에 다시 올랐습니다."

        # The full briefing can repeat the same connective across several news items.
        assert find_causal_terms(text) == ["때문에"]
