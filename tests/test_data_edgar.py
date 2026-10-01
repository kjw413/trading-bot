from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradingbot.data.credentials import MissingCredentialsError
from tradingbot.data.edgar import (
    EARNINGS_ITEM,
    EDGAR_TZ,
    Filing,
    classify_filing,
    filings_to_events,
    MissingUserAgentError,
    is_amended,
    parse_filings,
    reaction_date,
    update_edgar_events,
    user_agent_from_env,
)
from tradingbot.data.panel import PanelStore

ET = ZoneInfo("America/New_York")


class TestClassifyFiling:
    def test_an_8k_carrying_item_202_is_the_earnings_release(self):
        # Item 2.02 is "Results of Operations and Financial Condition" — the
        # press release the market trades on, weeks before the 10-Q repeats it.
        assert classify_filing("8-K", "2.02") == "provisional"

    def test_item_202_among_others_still_counts(self):
        # An earnings 8-K almost always carries 9.01 (exhibits) alongside.
        assert classify_filing("8-K", "2.02,9.01") == "provisional"
        assert classify_filing("8-K", "7.01,2.02,9.01") == "provisional"

    def test_whitespace_between_items_is_tolerated(self):
        assert classify_filing("8-K", "2.02, 9.01") == "provisional"

    def test_an_8k_without_item_202_is_not_an_earnings_event(self):
        # 5.02 is officer departures, 1.01 a material agreement. Neither is
        # earnings, and counting them would do to this collector what the
        # amendment tags did to the Korean one.
        assert classify_filing("8-K", "5.02") is None
        assert classify_filing("8-K", "1.01,9.01") is None

    def test_an_8k_with_no_items_is_not_an_event(self):
        assert classify_filing("8-K", "") is None

    def test_a_substring_match_on_the_item_number_is_not_enough(self):
        # "12.02" and "2.021" must not read as 2.02.
        assert classify_filing("8-K", "12.02") is None

    @pytest.mark.parametrize("form", ["10-Q", "10-K"])
    def test_periodic_reports_are_the_fallback(self, form):
        assert classify_filing(form, "") == "periodic"

    @pytest.mark.parametrize("form", ["DEF 14A", "S-1", "4", "SC 13G", "424B2"])
    def test_unrelated_forms_are_not_events(self, form):
        assert classify_filing(form, "") is None

    def test_an_amended_filing_keeps_its_kind(self):
        # Same rule the Korean collector had to learn: being a correction is a
        # property of the filing, not a kind of filing.
        assert classify_filing("8-K/A", "2.02") == "provisional"
        assert classify_filing("10-Q/A", "") == "periodic"

    def test_an_amended_unrelated_form_is_still_not_an_event(self):
        assert classify_filing("DEF 14A/A", "") is None


class TestIsAmended:
    @pytest.mark.parametrize("form", ["8-K/A", "10-Q/A", "10-K/A"])
    def test_slash_a_marks_an_amendment(self, form):
        assert is_amended(form) is True

    @pytest.mark.parametrize("form", ["8-K", "10-Q", "10-K"])
    def test_originals_are_not_amendments(self, form):
        assert is_amended(form) is False

    def test_case_and_spacing_do_not_matter(self):
        assert is_amended(" 8-k/a ") is True


def filing(
    day: str,
    *,
    form: str = "8-K",
    items: str = "2.02",
    acceptance: str | None = None,
    accession: str = "0000320193-24-000001",
) -> Filing:
    return Filing(
        form=form,
        items=items,
        filing_date=date.fromisoformat(day),
        acceptance=datetime.fromisoformat(acceptance).replace(tzinfo=ET) if acceptance else None,
        accession=accession,
    )


class TestReactionDate:
    def test_a_filing_accepted_after_the_close_reacts_the_next_session(self):
        # 2024-01-09 was a Tuesday. Accepted at 16:05 ET, after the bell, so
        # the first session that can price it is Wednesday.
        assert reaction_date(filing("2024-01-09", acceptance="2024-01-09T16:05:12"), "US") == date(
            2024, 1, 10
        )

    def test_exactly_at_the_close_counts_as_after(self):
        assert reaction_date(filing("2024-01-09", acceptance="2024-01-09T16:00:00"), "US") == date(
            2024, 1, 10
        )

    def test_an_intraday_filing_reacts_the_same_session(self):
        assert reaction_date(filing("2024-01-09", acceptance="2024-01-09T11:30:00"), "US") == date(
            2024, 1, 9
        )

    def test_a_premarket_filing_reacts_the_same_session(self):
        # Most large caps report before the open; the session that day is the
        # reaction.
        assert reaction_date(filing("2024-01-09", acceptance="2024-01-09T07:00:00"), "US") == date(
            2024, 1, 9
        )

    def test_a_friday_evening_filing_reacts_on_monday(self):
        # 2024-01-05 was a Friday.
        assert reaction_date(filing("2024-01-05", acceptance="2024-01-05T18:00:00"), "US") == date(
            2024, 1, 8
        )

    def test_a_filing_before_a_holiday_skips_it(self):
        # 2024-07-03 close, and 2024-07-04 is a market holiday.
        assert reaction_date(filing("2024-07-03", acceptance="2024-07-03T16:30:00"), "US") == date(
            2024, 7, 5
        )

    def test_a_missing_timestamp_falls_back_to_the_filing_date(self):
        # Older submissions carry no acceptance time. Guessing "after close"
        # would shift every one of them by a day.
        assert reaction_date(filing("2024-01-09", acceptance=None), "US") == date(2024, 1, 9)


class TestParseFilings:
    SUBMISSIONS = {
        "filings": {
            "recent": {
                "form": ["8-K", "10-Q", "4"],
                "items": ["2.02,9.01", "", ""],
                "filingDate": ["2024-02-01", "2024-02-15", "2024-02-20"],
                "acceptanceDateTime": [
                    "2024-02-01T16:31:20.000Z",
                    "2024-02-15T06:02:11.000Z",
                    "2024-02-20T18:00:00.000Z",
                ],
                "accessionNumber": ["a-1", "a-2", "a-3"],
            }
        }
    }

    def test_reads_every_filing_in_the_recent_block(self):
        filings = parse_filings(self.SUBMISSIONS)
        assert [f.form for f in filings] == ["8-K", "10-Q", "4"]
        assert filings[0].items == "2.02,9.01"
        assert filings[0].filing_date == date(2024, 2, 1)

    def test_acceptance_is_read_as_eastern_time(self):
        # The API stamps a trailing Z but publishes Eastern Time. Reading it as
        # UTC would move a 16:31 filing to 11:31 ET and turn an after-close
        # release into an intraday one.
        filings = parse_filings(self.SUBMISSIONS)
        assert filings[0].acceptance == datetime(2024, 2, 1, 16, 31, 20, tzinfo=EDGAR_TZ)

    def test_a_missing_acceptance_time_is_none_not_midnight(self):
        submissions = {
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "items": ["2.02"],
                    "filingDate": ["2024-02-01"],
                    "acceptanceDateTime": [""],
                    "accessionNumber": ["a-1"],
                }
            }
        }
        assert parse_filings(submissions)[0].acceptance is None

    def test_an_absent_items_column_is_tolerated(self):
        # Only 8-K submissions carry `items`; some pages omit the key.
        submissions = {
            "filings": {
                "recent": {
                    "form": ["10-Q"],
                    "filingDate": ["2024-02-15"],
                    "accessionNumber": ["a-2"],
                }
            }
        }
        assert parse_filings(submissions)[0].items == ""

    def test_no_filings_gives_an_empty_list(self):
        assert parse_filings({"filings": {"recent": {}}}) == []
        assert parse_filings({}) == []


class TestFilingsToEvents:
    def test_keeps_only_earnings_filings(self):
        frame = filings_to_events(
            [
                filing("2024-02-01", items="2.02,9.01", acceptance="2024-02-01T16:31:20"),
                filing("2024-02-05", form="4", items=""),
                filing("2024-02-15", form="10-Q", items="", acceptance="2024-02-15T06:02:11"),
            ],
            "AAPL",
        )
        assert list(frame["event_kind"]) == ["provisional", "periodic"]

    def test_records_the_reaction_date_alongside_the_filing_date(self):
        frame = filings_to_events(
            [filing("2024-02-01", acceptance="2024-02-01T16:31:20")], "AAPL"
        )
        assert frame["date"].iloc[0] == pd.Timestamp("2024-02-01")
        assert frame["reaction_date"].iloc[0] == pd.Timestamp("2024-02-02")

    def test_marks_amendments(self):
        frame = filings_to_events(
            [filing("2024-02-08", form="8-K/A", items="2.02")], "AAPL"
        )
        assert bool(frame["amended"].iloc[0]) is True

    def test_consolidated_is_uniformly_true(self):
        # US filings carry no separate/consolidated split, so the flag the
        # Korean collector needs is a no-op here — set true so the shared
        # schedule_dates preference step passes straight through.
        frame = filings_to_events([filing("2024-02-01")], "AAPL")
        assert bool(frame["consolidated"].iloc[0]) is True

    def test_the_original_wins_when_two_filings_share_a_date(self):
        frame = filings_to_events(
            [
                filing("2024-02-01", form="8-K/A", items="2.02", accession="b"),
                filing("2024-02-01", form="8-K", items="2.02", accession="a"),
            ],
            "AAPL",
        )
        assert len(frame) == 1
        assert bool(frame["amended"].iloc[0]) is False

    def test_the_earnings_release_wins_over_a_periodic_report(self):
        frame = filings_to_events(
            [
                filing("2024-02-01", form="10-K", items="", accession="b"),
                filing("2024-02-01", form="8-K", items="2.02", accession="a"),
            ],
            "AAPL",
        )
        assert len(frame) == 1
        assert frame["event_kind"].iloc[0] == "provisional"

    def test_symbol_is_upper_cased(self):
        frame = filings_to_events([filing("2024-02-01")], "aapl")
        assert frame["symbol"].iloc[0] == "AAPL"

    def test_sorted_by_date(self):
        frame = filings_to_events(
            [filing("2024-05-02", accession="b"), filing("2024-02-01", accession="a")],
            "AAPL",
        )
        assert list(frame["date"]) == [pd.Timestamp("2024-02-01"), pd.Timestamp("2024-05-02")]

    def test_no_earnings_filings_gives_an_empty_frame(self):
        frame = filings_to_events([filing("2024-02-05", form="4", items="")], "AAPL")
        assert frame.empty


class TestAppleFiscal2024:
    """A year of AAPL's real reporting rhythm.

    Apple files its earnings 8-K after the close, then the 10-Q or 10-K a few
    days later. The schedule has to come out quarterly off the 8-Ks alone.
    """

    RELEASES = ["2024-02-01", "2024-05-02", "2024-08-01", "2024-10-31"]
    PERIODIC = [
        ("2024-02-02", "10-Q"),
        ("2024-05-03", "10-Q"),
        ("2024-08-02", "10-Q"),
        ("2024-11-01", "10-K"),
    ]

    def frame(self) -> pd.DataFrame:
        filings = [
            filing(day, items="2.02,9.01", acceptance=f"{day}T16:30:00", accession=f"r{i}")
            for i, day in enumerate(self.RELEASES)
        ]
        filings += [
            filing(day, form=form, items="", acceptance=f"{day}T06:05:00", accession=f"p{i}")
            for i, (day, form) in enumerate(self.PERIODIC)
        ]
        return filings_to_events(filings, "AAPL")

    def test_the_schedule_uses_the_releases_not_the_reports(self):
        from tradingbot.data.events import schedule_dates

        assert schedule_dates(self.frame()) == [date.fromisoformat(d) for d in self.RELEASES]

    def test_the_cadence_is_quarterly(self):
        from tradingbot.data.events import schedule_dates

        dates = schedule_dates(self.frame())
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        assert all(85 <= gap <= 95 for gap in gaps), gaps

    def test_every_release_reacts_the_next_session(self):
        frame = self.frame()
        releases = frame[frame["event_kind"] == "provisional"]
        for filing_day, reaction in zip(releases["date"], releases["reaction_date"]):
            assert reaction > filing_day


class TestMicrosoftFiscal2024:
    """MSFT files its 10-Q the same day as the earnings release.

    A live collection returned 47 provisional rows for MSFT and only 9
    periodic ones, against 47 and 47 for AAPL. That gap is the same-day
    collapse doing its job, not a collection failure — but it is surprising
    enough that someone counting periodic rows later would reasonably
    conclude the collector was broken, so it is pinned here.
    """

    RELEASES = ["2024-01-30", "2024-04-25", "2024-07-30", "2024-10-30"]

    def frame(self) -> pd.DataFrame:
        filings = []
        for index, day in enumerate(self.RELEASES):
            filings.append(
                filing(
                    day,
                    items="2.02,9.01",
                    acceptance=f"{day}T16:07:00",
                    accession=f"r{index}",
                )
            )
            # Same day, a few hours earlier — still the periodic report.
            filings.append(
                filing(
                    day,
                    form="10-Q",
                    items="",
                    acceptance=f"{day}T16:05:00",
                    accession=f"p{index}",
                )
            )
        return filings_to_events(filings, "MSFT")

    def test_one_row_per_day_and_the_release_wins(self):
        frame = self.frame()
        assert len(frame) == len(self.RELEASES)
        assert set(frame["event_kind"]) == {"provisional"}

    def test_the_periodic_report_is_absorbed_not_lost_to_chance(self):
        # The panel keys on (date, symbol), so one of the two had to go. Which
        # one is decided by _KIND_PRIORITY rather than by write order.
        assert "periodic" not in set(self.frame()["event_kind"])

    def test_the_schedule_is_still_quarterly(self):
        from tradingbot.data.events import schedule_dates

        dates = schedule_dates(self.frame())
        assert dates == [date.fromisoformat(d) for d in self.RELEASES]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        assert all(85 <= gap <= 100 for gap in gaps), gaps

    def test_every_release_reacts_the_next_session(self):
        frame = self.frame()
        assert all(frame["reaction_date"] > frame["date"])


class TestUserAgentFromEnv:
    def test_reads_the_configured_contact(self):
        assert user_agent_from_env({"SEC_USER_AGENT": "me me@example.com"}) == "me me@example.com"

    def test_missing_is_an_error_with_a_pointer(self):
        # SEC blocks requests without a contact in the User-Agent, and the
        # failure it returns is an opaque 403.
        with pytest.raises(MissingUserAgentError, match="SEC_USER_AGENT"):
            user_agent_from_env({})

    def test_a_blank_value_counts_as_missing(self):
        with pytest.raises(MissingUserAgentError):
            user_agent_from_env({"SEC_USER_AGENT": "   "})

    def test_it_is_a_credential_error_so_the_pipeline_skips_rather_than_retries(self):
        # No amount of backoff conjures an environment variable, and a missing
        # contact is a configuration state rather than a collection failure —
        # the same treatment the absent DART key gets.
        assert issubclass(MissingUserAgentError, MissingCredentialsError)


class TestUpdateEdgarEvents:
    def test_writes_rows_with_availability_on_the_next_trading_day(self, tmp_path):
        store = PanelStore(tmp_path, "events", "US")

        def fetcher(cik, start, end, symbol):
            return filings_to_events(
                [filing("2024-02-01", acceptance="2024-02-01T16:31:20")], symbol
            )

        written = update_edgar_events(
            store,
            symbols=["AAPL"],
            ciks={"AAPL": 320193},
            start=date(2024, 1, 1),
            end=date(2024, 3, 1),
            fetcher=fetcher,
        )
        assert written == 1
        panel = store.read()
        assert panel["available_at"].iloc[0] == pd.Timestamp("2024-02-02")
        assert panel["source"].iloc[0] == "edgar"

    def test_recollecting_unchanged_data_writes_nothing(self, tmp_path):
        store = PanelStore(tmp_path, "events", "US")

        def fetcher(cik, start, end, symbol):
            return filings_to_events(
                [filing("2024-02-01", acceptance="2024-02-01T16:31:20")], symbol
            )

        kwargs = dict(
            symbols=["AAPL"],
            ciks={"AAPL": 320193},
            start=date(2024, 1, 1),
            end=date(2024, 3, 1),
            fetcher=fetcher,
        )
        assert update_edgar_events(store, **kwargs) == 1
        assert update_edgar_events(store, **kwargs) == 0

    def test_a_symbol_without_a_cik_is_skipped_not_fatal(self, tmp_path):
        store = PanelStore(tmp_path, "events", "US")

        def fetcher(cik, start, end, symbol):
            return filings_to_events([filing("2024-02-01")], symbol)

        written = update_edgar_events(
            store,
            symbols=["AAPL", "NOTATICKER"],
            ciks={"AAPL": 320193},
            start=date(2024, 1, 1),
            end=date(2024, 3, 1),
            fetcher=fetcher,
        )
        assert written == 1

    def test_one_failing_symbol_does_not_abort_the_batch(self, tmp_path):
        store = PanelStore(tmp_path, "events", "US")

        def fetcher(cik, start, end, symbol):
            if symbol == "MSFT":
                raise RuntimeError("SEC rate limit")
            return filings_to_events([filing("2024-02-01")], symbol)

        written = update_edgar_events(
            store,
            symbols=["MSFT", "AAPL"],
            ciks={"MSFT": 789019, "AAPL": 320193},
            start=date(2024, 1, 1),
            end=date(2024, 3, 1),
            fetcher=fetcher,
        )
        assert written == 1


class TestEarningsItemConstant:
    def test_the_item_number_is_stated_once(self):
        # Referenced by the docs and the tests; a literal scattered around is
        # how "2.02" silently becomes "2.2" somewhere.
        assert EARNINGS_ITEM == "2.02"
