from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from tradingbot.account.base import AccountSnapshot, Holding
from tradingbot.data.news import NewsItem, NewsResult, find_causal_terms
from tradingbot.proposal import (
    NoProposalReason,
    PassedNoChange,
    Proposal,
    Refusal,
)
from tradingbot.report import glossary
from tradingbot.report.briefing import render_briefing, split_for_telegram
from tradingbot.research.promotion_ledger import (
    CriterionResult,
    PromotionRecord,
    Verdict,
)

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

KNOWN_LEVERAGE_SENTENCE = (
    "- SOXL은 하루 단위로 3배 움직임을 목표로 하는 상품입니다. "
    "여러 날을 합치면 기준 가격 움직임의 정확히 3배가 아니며, "
    "오래 들고 있을수록 차이가 커집니다."
)
UNKNOWN_LEVERAGE_SENTENCE = (
    "- SPCX는 등록된 상품이지만 목표 배수를 확인하지 못했습니다. "
    "1배 상품으로 가정하지 않습니다."
)
UNREGISTERED_LEVERAGE_SENTENCE = "- 005930은 상품 배수가 등록되지 않았습니다."


def news_item(
    *,
    symbol="005930",
    source="DART",
    title="주요사항보고서 제출",
    url="https://example.com/news/1",
    via="",
):
    return NewsItem(
        symbol=symbol,
        source=source,
        published_at=date(2026, 8, 14),
        title=title,
        url=url,
        via=via,
    )


def news_result(*items, failures=None, dropped=None, skipped=None):
    return NewsResult(
        items=tuple(items),
        failures=failures or {},
        dropped=dropped or {},
        skipped=skipped or {},
    )


def promotion_basis(*, passed: bool, symbol: str = "005930") -> PromotionRecord:
    return PromotionRecord(
        strategy="briefing_test",
        market="KR",
        universe=(symbol,),
        verdict=Verdict.PASS if passed else Verdict.FAIL,
        criteria=(
            CriterionResult(
                "excess_return" if passed else "max_drawdown",
                0.10 if passed else 0.25,
                0.12 if passed else 0.41,
                passed,
            ),
        ),
        cadence="weekly",
        rejected_orders=0,
        total_orders=10,
        evaluated_at=NOW,
        commit="current",
        report_path="reports/evaluation.md",
    )


def refusal(reason: NoProposalReason, *, symbol: str = "005930") -> Refusal:
    basis = (
        promotion_basis(passed=False, symbol=symbol)
        if reason is NoProposalReason.DID_NOT_PASS
        else None
    )
    return Refusal(symbol, reason, "internal detail must not be rendered", basis)


def proposal_block(text: str) -> list[str]:
    block = next(
        block for block in text.split("\n\n") if block.startswith("[이번 주 판단]")
    )
    return block.splitlines()[1:]


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
        assert KNOWN_LEVERAGE_SENTENCE in text
        assert UNKNOWN_LEVERAGE_SENTENCE not in text

    def test_a_two_times_product_gets_its_warning(self):
        ggll = h(symbol="GGLL", currency="USD", market="US", avg=20.0, last=30.0)
        text = render_briefing(snap(15, [ggll]), snap(1, [ggll]), now=NOW)
        assert "2배" in text

    def test_an_unknown_multiple_gets_its_own_warning(self):
        spcx = h(symbol="SPCX", currency="USD", market="US", avg=20.0, last=30.0)
        text = render_briefing(snap(15, [spcx]), snap(1, [spcx]), now=NOW)
        assert UNKNOWN_LEVERAGE_SENTENCE in text
        assert KNOWN_LEVERAGE_SENTENCE not in text
        assert "3배" not in text

    def test_an_unregistered_unlevered_holding_gets_its_own_sentence(self):
        text = render_briefing(snap(15), snap(1), now=NOW)
        assert KNOWN_LEVERAGE_SENTENCE not in text
        assert UNKNOWN_LEVERAGE_SENTENCE not in text
        assert UNREGISTERED_LEVERAGE_SENTENCE in text

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


class TestNewsSection:
    def test_the_news_section_is_absent_when_no_news_is_given(self):
        # Existing M1 callers omit the keyword, so their output must not gain a block.
        omitted = render_briefing(snap(15), snap(1), now=NOW)
        explicit_none = render_briefing(snap(15), snap(1), news=None, now=NOW)
        assert explicit_none == omitted
        assert "[새 소식]" not in omitted

    def test_each_item_shows_its_date_title_and_source(self):
        title = "매출액 또는 손익구조 30% 이상 변경"
        item = news_item(title=title)
        text = render_briefing(snap(15), snap(1), news=news_result(item), now=NOW)
        assert item.published_at.isoformat() in text
        assert f"  {title}" in text
        assert item.source in text
        assert item.url in text

    def test_a_mapped_item_says_it_is_not_the_etfs_own_news(self):
        item = news_item(
            symbol="SOXL",
            source="Yahoo",
            title="NVIDIA announces quarterly results",
            via="NVDA",
        )
        text = render_briefing(snap(15), snap(1), news=news_result(item), now=NOW)
        assert "SOXL 자체 소식이 아닙니다." in text
        assert "NVDA에서 가져온 소식입니다." in text

    def test_no_news_and_a_failed_fetch_read_differently(self):
        # An outage must not be presented as a quiet week with no publications.
        empty = render_briefing(snap(15), snap(1), news=news_result(), now=NOW)
        failed = render_briefing(
            snap(15),
            snap(1),
            news=news_result(failures={"Yahoo": "연결 시간 초과"}),
            now=NOW,
        )
        assert "이 기간에 새로 올라온 소식이 없습니다." in empty
        assert "소식을 가져오지 못했습니다 (Yahoo: 연결 시간 초과). 계좌 숫자는 영향받지 않습니다." in failed
        assert "이 기간에 새로 올라온 소식이 없습니다." not in failed

    def test_what_the_cap_dropped_is_stated(self):
        # A capped list must not look like the complete set of publications.
        text = render_briefing(
            snap(15),
            snap(1),
            news=news_result(news_item(), dropped={"005930": 4, "AAPL": 2}),
            now=NOW,
        )
        assert "이 밖에 005930 4건, AAPL 2건이 더 있습니다." in text

    def test_the_briefing_never_claims_a_cause(self):
        rendered = render_briefing(
            snap(15), snap(1), news=news_result(news_item()), now=NOW
        )
        assert find_causal_terms(rendered) == []

    def test_the_news_section_passes_the_jargon_check(self):
        rendered = render_briefing(
            snap(15),
            snap(1),
            news=news_result(
                news_item(),
                failures={"Yahoo": "연결 시간 초과"},
                dropped={"005930": 2},
                skipped={"dart": "missing key"},
            ),
            now=NOW,
        )
        assert glossary.find_banned_terms(rendered) == []

    def test_a_long_news_list_still_splits_at_section_boundaries(self):
        # A near-limit news block should move whole instead of splitting a headline.
        items = tuple(
            news_item(
                symbol=f"NEWS{number}",
                title=f"{number} " + "가" * 270,
                url=f"https://example.com/news/{number}",
            )
            for number in range(12)
        )
        text = render_briefing(snap(15), snap(1), news=news_result(*items), now=NOW)
        parts = split_for_telegram(text)
        assert len(parts) > 1
        assert parts[-1].startswith("[새 소식]")
        assert all(len(part) <= 4096 for part in parts)


class TestProposalSection:
    REFUSAL_SENTENCES = {
        NoProposalReason.NEVER_EVALUATED: (
            "- 005930: 아직 아무도 이 보유 종목의 성과를 재보지 않았고, "
            "다음 단계는 평가 실행입니다."
        ),
        NoProposalReason.DID_NOT_PASS: (
            "- 005930: 성과를 재봤지만 가장 크게 줄어든 폭 기준에 미치지 "
            "못했으며, 전략을 고친 뒤 다시 평가해야 합니다."
        ),
        NoProposalReason.UNMEASURABLE: (
            "- 005930: 평가를 시도했지만 자료가 판단을 뒷받침하지 못했으며, "
            "충분한 자료를 갖춘 뒤 다시 평가해야 합니다."
        ),
        NoProposalReason.STALE_RECORD: (
            "- 005930: 통과한 기록은 있지만 그 뒤 코드가 바뀌었으며, 지금 "
            "코드로 평가를 다시 실행해야 합니다."
        ),
        NoProposalReason.CADENCE_MISMATCH: (
            "- 005930: 통과한 기록의 점검 주기가 이번 브리핑과 다르며, 이번 "
            "브리핑과 같은 주기로 다시 평가해야 합니다."
        ),
        NoProposalReason.DISCRETIONARY_HOLDING: (
            "- 005930: 이 보유 종목은 구조상 앞으로도 봇이 측정할 수 없는 "
            "재량 보유이며, 기다리지 말고 사람이 계속 판단해야 합니다."
        ),
    }
    HOLD_SENTENCE = (
        "- 005930: 평가를 통과해 정상적으로 작동하고 있으며, 이번 주에는 "
        "보유한 그대로 유지하세요."
    )

    def test_the_section_is_absent_when_no_proposal_is_given(self):
        omitted = render_briefing(snap(15), snap(1), now=NOW)
        explicit_none = render_briefing(
            snap(15), snap(1), proposal=None, now=NOW
        )
        assert explicit_none == omitted
        assert "[이번 주 판단]" not in omitted

    def test_each_refusal_has_its_own_actionable_sentence(self):
        rendered = {}
        for reason, expected in self.REFUSAL_SENTENCES.items():
            text = render_briefing(
                snap(15),
                snap(1),
                proposal=Proposal((refusal(reason),)),
                now=NOW,
            )
            rendered[reason] = proposal_block(text)[0]
            assert rendered[reason] == expected

        assert len(rendered) == len(NoProposalReason) == 6
        assert len(set(rendered.values())) == 6

    def test_a_successful_hold_is_distinct_from_every_refusal(self):
        decision = PassedNoChange(
            "005930", promotion_basis(passed=True), "internal detail"
        )
        text = render_briefing(
            snap(15), snap(1), proposal=Proposal((decision,)), now=NOW
        )
        rendered = proposal_block(text)[0]
        assert rendered == self.HOLD_SENTENCE
        assert rendered not in self.REFUSAL_SENTENCES.values()
        assert "보유한 그대로 유지하세요" in rendered

    def test_a_discretionary_holding_reads_as_permanent(self):
        text = render_briefing(
            snap(15),
            snap(1),
            proposal=Proposal(
                (refusal(NoProposalReason.DISCRETIONARY_HOLDING),)
            ),
            now=NOW,
        )
        rendered = proposal_block(text)[0]
        assert "구조상 앞으로도" in rendered
        assert "기다리지 말고" in rendered

    def test_measurable_and_discretionary_holdings_both_appear(self):
        soxl = h(
            symbol="SOXL", currency="USD", market="US", avg=20.0, last=30.0
        )
        fngu = h(
            symbol="FNGU", currency="USD", market="US", avg=100.0, last=120.0
        )
        proposal = Proposal(
            (
                PassedNoChange(
                    "SOXL",
                    promotion_basis(passed=True, symbol="SOXL"),
                    "internal detail",
                ),
                refusal(
                    NoProposalReason.DISCRETIONARY_HOLDING, symbol="FNGU"
                ),
            )
        )
        lines = proposal_block(
            render_briefing(
                snap(15, [soxl, fngu]),
                snap(1, [soxl, fngu]),
                proposal=proposal,
                now=NOW,
            )
        )
        assert lines == [
            self.HOLD_SENTENCE.replace("005930", "SOXL"),
            self.REFUSAL_SENTENCES[
                NoProposalReason.DISCRETIONARY_HOLDING
            ].replace("005930", "FNGU"),
        ]

    def test_the_section_passes_the_language_rules(self):
        decisions = tuple(refusal(reason) for reason in NoProposalReason) + (
            PassedNoChange(
                "005930", promotion_basis(passed=True), "internal detail"
            ),
        )
        rendered = render_briefing(
            snap(15), snap(1), proposal=Proposal(decisions), now=NOW
        )
        assert glossary.find_banned_terms(rendered) == []
        assert find_causal_terms(rendered) == []
        block = "\n".join(proposal_block(rendered)).casefold()
        assert all(word not in block for word in ("매수", "매도", "buy", "sell"))


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
