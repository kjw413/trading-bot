from __future__ import annotations

import pytest

from tradingbot.report import glossary


class TestTerms:
    def test_every_term_has_a_korean_label_and_explanation(self):
        for key, term in glossary.TERMS.items():
            assert term.label.strip(), f"{key} has no label"
            assert term.one_line.strip(), f"{key} has no explanation"

    def test_no_label_or_explanation_contains_a_banned_word(self):
        # The dictionary is the thing that keeps jargon out; it must not be
        # the thing that smuggles jargon in.
        for key, term in glossary.TERMS.items():
            assert glossary.find_banned_terms(term.label) == [], key
            assert glossary.find_banned_terms(term.one_line) == [], key

    def test_the_terms_the_briefing_needs_exist(self):
        required = {
            "total_return",
            "period_return",
            "holding_return",
            "price_part",
            "fx_part",
            "cash_weight",
            "unmeasured",
        }
        assert required <= set(glossary.TERMS)

    def test_label_and_explain_read_from_the_dictionary(self):
        assert glossary.label("total_return") == glossary.TERMS["total_return"].label
        assert glossary.explain("total_return") == glossary.TERMS["total_return"].one_line

    def test_an_unknown_key_fails_loudly(self):
        # A typo must not silently render as an empty string in a report the
        # user is meant to trust.
        with pytest.raises(KeyError):
            glossary.label("no_such_term")


class TestFormatValue:
    def test_percent_terms_get_a_sign_and_one_decimal(self):
        assert glossary.format_value("period_return", 0.0123) == "+1.2%"
        assert glossary.format_value("period_return", -0.0456) == "-4.6%"

    def test_zero_is_not_signed_as_negative(self):
        assert glossary.format_value("period_return", 0.0) == "+0.0%"

    def test_none_renders_as_the_unmeasured_label(self):
        # "측정 불가" must survive formatting; a None that becomes 0.0% is the
        # exact lie this project refuses to tell.
        assert glossary.format_value("period_return", None) == glossary.label("unmeasured")


class TestFindBannedTerms:
    @pytest.mark.parametrize(
        "text",
        [
            "Sharpe 비율은 1.5입니다",
            "샤프 지수가 높습니다",
            "MDD는 30%입니다",
            "max drawdown was large",
            "exposure 94%",
            "profit factor 3.5",
        ],
    )
    def test_jargon_is_reported(self, text):
        assert glossary.find_banned_terms(text) != []

    def test_matching_ignores_case(self):
        assert glossary.find_banned_terms("sharpe ratio") != []

    def test_plain_korean_passes(self):
        text = "지난 12일 동안 전체 자산은 1.2% 늘었습니다. 현금 비중은 8%입니다."
        assert glossary.find_banned_terms(text) == []

    def test_every_hit_is_reported_not_just_the_first(self):
        hits = glossary.find_banned_terms("Sharpe와 MDD를 함께 봅니다")
        assert len(hits) >= 2

    def test_empty_text_has_no_hits(self):
        assert glossary.find_banned_terms("") == []
