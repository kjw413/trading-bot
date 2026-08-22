from __future__ import annotations

from datetime import date

from tradingbot.data.fundamentals import Disclosure
from tradingbot.data.news import DART_VIEWER_URL, NewsItem, cap, dart_items, within


def news_item(symbol: str, published_at: date, title: str) -> NewsItem:
    return NewsItem(
        symbol=symbol,
        source="dart",
        published_at=published_at,
        title=title,
        url=f"https://example.test/{symbol}/{title}",
    )


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
