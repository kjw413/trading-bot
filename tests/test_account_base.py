from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingbot.account.base import (
    AccountSnapshot,
    Holding,
    load_latest,
    load_snapshots,
    save_snapshot,
)

KST = timezone(timedelta(hours=9))


def holding(symbol="005930", currency="KRW", qty=10.0, avg=70000.0, last=77000.0, market="KR"):
    return Holding(
        symbol=symbol,
        market=market,
        qty=qty,
        qty_display=str(qty),
        avg_price=avg,
        last_price=last,
        currency=currency,
    )


def snapshot(when="2026-08-15T18:00:00+09:00", holdings=None, cash=None, fx=None):
    return AccountSnapshot(
        as_of=datetime.fromisoformat(when),
        holdings=tuple(holdings if holdings is not None else [holding()]),
        cash=cash if cash is not None else {"KRW": 300_000.0},
        fx_to_krw=fx if fx is not None else {"KRW": 1.0, "USD": 1350.0},
        fx_source="broker",
    )


class TestValuation:
    def test_krw_holding_is_qty_times_price(self):
        snap = snapshot()
        assert snap.holding_value_krw(snap.holdings[0]) == pytest.approx(770_000.0)

    def test_usd_holding_is_converted_at_the_snapshot_rate(self):
        soxl = holding(symbol="SOXL", currency="USD", qty=5.0, avg=20.0, last=30.0, market="US")
        snap = snapshot(holdings=[soxl])
        assert snap.holding_value_krw(soxl) == pytest.approx(5 * 30.0 * 1350.0)

    def test_total_value_adds_cash_in_every_currency(self):
        soxl = holding(symbol="SOXL", currency="USD", qty=5.0, avg=20.0, last=30.0, market="US")
        snap = snapshot(holdings=[holding(), soxl], cash={"KRW": 300_000.0, "USD": 100.0})
        expected = 770_000.0 + 5 * 30.0 * 1350.0 + 300_000.0 + 100.0 * 1350.0
        assert snap.value_krw() == pytest.approx(expected)

    def test_a_currency_without_a_rate_fails_loudly(self):
        # Silently treating an unknown currency as 1:1 would understate a US
        # position by a factor of ~1350 and look like a crash.
        odd = holding(symbol="7203", currency="JPY", market="US")
        snap = snapshot(holdings=[odd])
        with pytest.raises(KeyError):
            snap.value_krw()

    def test_weights_sum_with_cash_to_one(self):
        soxl = holding(symbol="SOXL", currency="USD", qty=5.0, avg=20.0, last=30.0, market="US")
        snap = snapshot(holdings=[holding(), soxl])
        weights = snap.weights_krw()
        cash_weight = snap.cash_krw() / snap.value_krw()
        assert sum(weights.values()) + cash_weight == pytest.approx(1.0)

    def test_an_empty_account_has_no_weights_and_does_not_divide_by_zero(self):
        snap = snapshot(holdings=[], cash={"KRW": 0.0})
        assert snap.value_krw() == pytest.approx(0.0)
        assert snap.weights_krw() == {}


class TestPersistence:
    def test_round_trips_through_json(self, tmp_path):
        original = snapshot()
        save_snapshot(original, tmp_path)
        assert load_latest(tmp_path) == original

    def test_the_timezone_survives(self, tmp_path):
        # A snapshot that comes back naive would silently shift every interval
        # length by nine hours.
        original = snapshot()
        save_snapshot(original, tmp_path)
        assert load_latest(tmp_path).as_of.utcoffset() == original.as_of.utcoffset()

    def test_two_runs_on_one_day_both_survive(self, tmp_path):
        save_snapshot(snapshot("2026-08-15T09:00:00+09:00"), tmp_path)
        save_snapshot(snapshot("2026-08-15T18:00:00+09:00"), tmp_path)
        assert len(load_snapshots(tmp_path)) == 2

    def test_snapshots_come_back_oldest_first(self, tmp_path):
        save_snapshot(snapshot("2026-08-15T18:00:00+09:00"), tmp_path)
        save_snapshot(snapshot("2026-08-01T18:00:00+09:00"), tmp_path)
        loaded = load_snapshots(tmp_path)
        assert [s.as_of.isoformat() for s in loaded] == [
            "2026-08-01T18:00:00+09:00",
            "2026-08-15T18:00:00+09:00",
        ]

    def test_latest_is_none_on_a_fresh_install(self, tmp_path):
        assert load_latest(tmp_path) is None

    def test_a_missing_directory_is_not_an_error(self, tmp_path):
        assert load_snapshots(tmp_path / "nope") == []

    def test_display_quantity_is_preserved_verbatim(self, tmp_path):
        # The whole point: the report must show what the Toss app shows.
        odd = Holding(
            symbol="SOXL", market="US", qty=1.2345678, qty_display="1.2345678",
            avg_price=20.0, last_price=30.0, currency="USD",
        )
        save_snapshot(snapshot(holdings=[odd]), tmp_path)
        assert load_latest(tmp_path).holdings[0].qty_display == "1.2345678"

    def test_a_corrupt_snapshot_raises_rather_than_being_skipped(self, tmp_path):
        save_snapshot(snapshot(), tmp_path)
        broken = tmp_path / "20260101T000000.json"
        broken.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError):
            load_snapshots(tmp_path)

    def test_an_unknown_schema_version_raises(self, tmp_path):
        import json

        path = save_snapshot(snapshot(), tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = 999
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError):
            load_snapshots(tmp_path)
