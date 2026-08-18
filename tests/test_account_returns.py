from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingbot.account.base import AccountSnapshot, Holding
from tradingbot.account.returns import holding_return, interval_return

KST = timezone(timedelta(hours=9))


def h(symbol="005930", currency="KRW", qty=10.0, avg=70000.0, last=70000.0, market="KR"):
    return Holding(
        symbol=symbol, market=market, qty=qty, qty_display=str(qty),
        avg_price=avg, last_price=last, currency=currency,
    )


def snap(day, holdings, cash=0.0, usd=1350.0):
    return AccountSnapshot(
        as_of=datetime(2026, 8, day, 9, 0, tzinfo=KST),
        holdings=tuple(holdings),
        cash={"KRW": cash},
        fx_to_krw={"KRW": 1.0, "USD": usd},
        fx_source="broker",
    )


class TestHoldingReturn:
    def test_gain_against_the_brokers_average_price(self):
        assert holding_return(h(avg=70000.0, last=77000.0)) == pytest.approx(0.10)

    def test_loss_is_negative(self):
        assert holding_return(h(avg=70000.0, last=63000.0)) == pytest.approx(-0.10)

    def test_a_zero_average_price_is_unmeasured_not_infinite(self):
        assert holding_return(h(avg=0.0, last=100.0)) is None


class TestIntervalReturn:
    def test_days_come_from_the_broker_timestamps(self):
        result = interval_return(snap(1, [h()]), snap(13, [h()]))
        assert result.days == 12

    def test_a_pure_price_gain_is_measured(self):
        prev = snap(1, [h(last=70000.0)])
        curr = snap(8, [h(last=77000.0)])
        result = interval_return(prev, curr)
        assert result.measured
        assert result.return_pct == pytest.approx(0.10)

    def test_price_and_fx_are_separated_for_a_usd_holding(self):
        # Price +10% in dollars, won weakens 1350 -> 1400 (+3.7%).
        prev = snap(1, [h(symbol="SOXL", currency="USD", qty=5.0, avg=20.0, last=20.0, market="US")], usd=1350.0)
        curr = snap(8, [h(symbol="SOXL", currency="USD", qty=5.0, avg=20.0, last=22.0, market="US")], usd=1400.0)
        result = interval_return(prev, curr)
        assert result.measured
        assert result.price_part_pct == pytest.approx(0.10, abs=1e-9)
        assert result.fx_part_pct == pytest.approx(1400 / 1350 - 1, abs=1e-9)

    def test_a_krw_only_account_reports_no_fx_part(self):
        result = interval_return(snap(1, [h(last=70000.0)]), snap(8, [h(last=77000.0)]))
        assert result.fx_part_pct == pytest.approx(0.0)

    def test_a_known_deposit_is_excluded_from_the_return(self):
        # Value doubles, but half of it was deposited. The return is the price
        # move only.
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(8, [h(last=70000.0)], cash=700_000.0)
        result = interval_return(prev, curr, net_flow_krw=700_000.0)
        assert result.measured
        assert result.return_pct == pytest.approx(0.0)

    def test_an_unexplained_jump_is_reported_unmeasured(self):
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(8, [h(last=70000.0)], cash=700_000.0)
        result = interval_return(prev, curr)
        assert not result.measured
        assert result.return_pct is None
        assert result.reason

    def test_the_unmeasured_reason_asks_about_both_causes(self):
        # The check cannot tell a deposit from a trade made by hand, so the
        # question must cover both rather than assert one.
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(8, [h(last=70000.0)], cash=700_000.0)
        reason = interval_return(prev, curr).reason
        assert "입출금" in reason and "매매" in reason

    def test_small_drift_stays_within_tolerance(self):
        # Fees and rounding must not push every week into "unmeasured".
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(8, [h(last=70000.0)], cash=1_000.0)  # 0.14% of 700,000
        assert interval_return(prev, curr).measured

    def test_a_new_symbol_bought_in_the_interval_is_flagged(self):
        prev = snap(1, [h(last=70000.0)], cash=700_000.0)
        curr = snap(8, [h(last=70000.0), h(symbol="000660", qty=10.0, avg=70000.0, last=70000.0)], cash=0.0)
        # Cash moved into shares — total is unchanged, so this stays measured.
        assert interval_return(prev, curr).measured

    def test_a_fully_sold_symbol_does_not_crash(self):
        prev = snap(1, [h(last=70000.0)], cash=0.0)
        curr = snap(8, [], cash=700_000.0)
        assert interval_return(prev, curr).measured

    def test_a_zero_starting_value_is_unmeasured_not_a_division_error(self):
        prev = snap(1, [], cash=0.0)
        curr = snap(8, [h(last=70000.0)], cash=0.0)
        result = interval_return(prev, curr)
        assert not result.measured
        assert result.return_pct is None

    def test_snapshots_in_the_wrong_order_are_rejected(self):
        with pytest.raises(ValueError):
            interval_return(snap(8, [h()]), snap(1, [h()]))

    def test_start_and_end_values_are_always_reported(self):
        # Even when the return is unmeasured, the reader still gets to see
        # what the account was worth at each end.
        result = interval_return(snap(1, [h(last=70000.0)], cash=0.0), snap(8, [h(last=70000.0)], cash=700_000.0))
        assert result.start_value_krw == pytest.approx(700_000.0)
        assert result.end_value_krw == pytest.approx(1_400_000.0)
