from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from tradingbot.account.base import AccountSnapshot, Holding
from tradingbot.report import glossary
from tradingbot.report.briefing import render_briefing, split_for_telegram

KST = timezone(timedelta(hours=9))


def h(symbol="005930", currency="KRW", qty=10.0, avg=70000.0, last=77000.0, market="KR"):
    return Holding(
        symbol=symbol, market=market, qty=qty, qty_display=str(qty),
        avg_price=avg, last_price=last, currency=currency,
    )


def snap(day, holdings=None, cash=300_000.0, usd=1350.0, hour=9):
    return AccountSnapshot(
        as_of=datetime(2026, 8, day, hour, 0, tzinfo=KST),
        holdings=tuple(holdings if holdings is not None else [h()]),
        cash={"KRW": cash},
        fx_to_krw={"KRW": 1.0, "USD": usd},
        fx_source="broker",
    )


NOW = datetime(2026, 8, 15, 10, 0, tzinfo=KST)


class TestPlainLanguage:
    def test_the_rendered_briefing_contains_no_jargon(self):
        # This is the enforcement point for requirement 3.
        text = render_briefing(snap(15), snap(1), now=NOW)
        assert glossary.find_banned_terms(text) == []

    def test_a_first_run_with_no_history_also_contains_no_jargon(self):
        assert glossary.find_banned_terms(render_briefing(snap(15), None, now=NOW)) == []

    def test_an_unmeasured_interval_also_contains_no_jargon(self):
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(15, [h(last=70000.0)], cash=700_000.0)
        assert glossary.find_banned_terms(render_briefing(curr, prev, now=NOW)) == []


class TestContent:
    def test_the_first_line_says_how_many_days(self):
        text = render_briefing(snap(15), snap(1), now=NOW)
        assert "14일" in text.splitlines()[0] or "14일" in text[:200]

    def test_a_first_run_says_so_instead_of_inventing_a_period(self):
        text = render_briefing(snap(15), None, now=NOW)
        assert "처음" in text

    def test_every_holding_appears(self):
        text = render_briefing(snap(15, [h(), h(symbol="000660")]), snap(1), now=NOW)
        assert "005930" in text and "000660" in text

    def test_the_display_quantity_is_what_gets_printed(self):
        odd = Holding(
            symbol="SOXL", market="US", qty=1.2345678, qty_display="1.2345678",
            avg_price=20.0, last_price=30.0, currency="USD",
        )
        text = render_briefing(snap(15, [odd]), snap(1, [odd]), now=NOW)
        assert "1.2345678" in text

    def test_an_unmeasured_return_shows_the_reason_not_a_zero(self):
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(15, [h(last=70000.0)], cash=700_000.0)
        text = render_briefing(curr, prev, now=NOW)
        assert glossary.label("unmeasured") in text
        assert "입출금" in text

    def test_a_leveraged_etf_gets_its_warning(self):
        soxl = h(symbol="SOXL", currency="USD", market="US", avg=20.0, last=30.0)
        text = render_briefing(snap(15, [soxl]), snap(1, [soxl]), now=NOW)
        assert "3배" in text

    def test_no_leveraged_warning_when_none_is_held(self):
        text = render_briefing(snap(15), snap(1), now=NOW)
        assert "3배" not in text

    def test_a_long_gap_is_called_out(self):
        text = render_briefing(snap(30), snap(1), now=datetime(2026, 8, 30, 10, 0, tzinfo=KST))
        assert "29일" in text
        assert "담지 못한" in text or "놓친" in text

    def test_a_short_gap_gets_no_gap_warning(self):
        text = render_briefing(snap(8), snap(1), now=datetime(2026, 8, 8, 10, 0, tzinfo=KST))
        assert "담지 못한" not in text

    def test_a_stale_broker_timestamp_is_flagged(self):
        # as_of is the broker's clock; if it lags our clock badly the numbers
        # are not current and the reader must be told.
        text = render_briefing(snap(15, hour=1), snap(1), now=datetime(2026, 8, 15, 20, 0, tzinfo=KST))
        assert "기준" in text

    def test_price_history_drives_the_trend_section(self):
        history = {"005930": pd.Series([70000.0, 72000.0, 77000.0])}
        text = render_briefing(snap(15), snap(1), price_history=history, now=NOW)
        assert "005930" in text

    def test_missing_price_history_does_not_break_rendering(self):
        text = render_briefing(snap(15), snap(1), price_history={}, now=NOW)
        assert text.strip()

    def test_an_empty_account_renders_without_crashing(self):
        text = render_briefing(snap(15, [], cash=0.0), None, now=NOW)
        assert text.strip()


class TestSplitForTelegram:
    def test_short_text_stays_in_one_message(self):
        assert len(split_for_telegram("짧은 브리핑")) == 1

    def test_long_text_is_split_under_the_limit(self):
        parts = split_for_telegram("\n\n".join(["가" * 1000] * 10), limit=4096)
        assert len(parts) > 1
        assert all(len(part) <= 4096 for part in parts)

    def test_nothing_is_lost_in_the_split(self):
        original = "\n\n".join([f"섹션{i}\n" + "나" * 900 for i in range(8)])
        assert "".join(split_for_telegram(original, limit=4096)).replace("\n", "") == original.replace("\n", "")

    def test_a_single_oversized_section_is_still_delivered(self):
        parts = split_for_telegram("다" * 9000, limit=4096)
        assert all(len(part) <= 4096 for part in parts)
        assert sum(len(part) for part in parts) >= 9000
