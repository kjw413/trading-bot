from __future__ import annotations

import math

from tradingbot.research.survivorship import (
    SurvivalRate,
    parse_form_index,
    render_markdown,
    survival_by_year,
)

# Shaped like a real form.idx: header block, then fixed-width rows sorted by
# form type. Company names contain spaces, which is why parsing works from the
# right-hand end.
FORM_IDX = """Description:           Master Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    March 31, 2018

Form Type   Company Name                     CIK        Date Filed  File Name
---------------------------------------------------------------------------
10-K        ACME CORP                        12345      2018-02-01  edgar/data/12345/a.txt
10-K        BETA HOLDINGS INC /DE/           23456      2018-02-15  edgar/data/23456/b.txt
10-Q        ACME CORP                        12345      2018-05-01  edgar/data/12345/c.txt
20-F        GAMMA PLC                        34567      2018-03-20  edgar/data/34567/d.txt
8-K         DELTA CO                         45678      2018-01-09  edgar/data/45678/e.txt
"""


class TestParseFormIndex:
    def test_reads_annual_filers(self):
        assert parse_form_index(FORM_IDX) == {12345, 23456, 34567}

    def test_ignores_other_forms(self):
        # A 10-Q filer is not evidence of an annual report; counting it would
        # inflate the population the survival rate divides by.
        assert 45678 not in parse_form_index(FORM_IDX)

    def test_a_company_filing_twice_counts_once(self):
        text = FORM_IDX + "10-K        ACME CORP    12345      2018-06-01  edgar/data/12345/f.txt\n"
        assert parse_form_index(text) == {12345, 23456, 34567}

    def test_company_names_with_spaces_do_not_break_parsing(self):
        assert 23456 in parse_form_index(FORM_IDX)

    def test_the_form_filter_is_configurable(self):
        assert parse_form_index(FORM_IDX, forms=("10-Q",)) == {12345}

    def test_header_and_rule_lines_are_skipped(self):
        assert parse_form_index("Form Type Company CIK Date File\n---\n") == set()

    def test_empty_text(self):
        assert parse_form_index("") == set()


class TestSurvivalByYear:
    def fetcher(self, calls: list[tuple[int, int]]):
        def fetch(year: int, quarter: int) -> str:
            calls.append((year, quarter))
            return FORM_IDX if quarter == 1 else ""

        return fetch

    def test_counts_filers_against_the_pool(self):
        rates = survival_by_year([2018], {12345, 99999}, fetcher=self.fetcher([]))
        assert rates[0].filers == 3
        assert rates[0].still_listed == 1
        assert rates[0].rate == 1 / 3

    def test_reads_all_four_quarters(self):
        calls: list[tuple[int, int]] = []
        survival_by_year([2018], set(), fetcher=self.fetcher(calls))
        assert calls == [(2018, 1), (2018, 2), (2018, 3), (2018, 4)]

    def test_a_full_pool_is_a_perfect_rate(self):
        rates = survival_by_year([2018], {12345, 23456, 34567}, fetcher=self.fetcher([]))
        assert rates[0].rate == 1.0

    def test_an_unreachable_quarter_does_not_look_like_perfect_survival(self):
        # Counting a failed fetch as zero filers would make the year's rate
        # NaN or 1.0 depending on the others — either way it would read as
        # "nothing missing" when the truth is "we did not look".
        def flaky(year: int, quarter: int) -> str:
            if quarter == 1:
                raise RuntimeError("SEC unreachable")
            return FORM_IDX

        rates = survival_by_year([2018], {12345}, fetcher=flaky)
        assert rates[0].filers == 3

    def test_a_year_with_no_filers_is_unmeasured_not_zero(self):
        rates = survival_by_year([1990], set(), fetcher=lambda y, q: "")
        assert rates[0].filers == 0
        assert math.isnan(rates[0].rate)

    def test_one_entry_per_year_in_order(self):
        rates = survival_by_year([2016, 2017, 2018], set(), fetcher=self.fetcher([]))
        assert [entry.year for entry in rates] == [2016, 2017, 2018]


class TestRenderMarkdown:
    def test_the_table_carries_the_caveat(self):
        text = render_markdown([SurvivalRate(2018, 3, 1)])
        assert "생존자 편향" in text
        assert "할인해서 읽어야" in text
        assert "| 2018 |" in text
        assert "33.3%" in text

    def test_an_unmeasured_year_says_so(self):
        assert "측정 불가" in render_markdown([SurvivalRate(1990, 0, 0)])
