from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tradingbot.account.base import AccountSnapshot, Holding
from tradingbot.briefing_service import run_briefing
from tradingbot.notify.telegram import NotifyError

KST = timezone(timedelta(hours=9))


def snapshot(day=15):
    return AccountSnapshot(
        as_of=datetime(2026, 8, day, 9, 0, tzinfo=KST),
        holdings=(Holding("005930", "KR", 10.0, "10", 70000.0, 77000.0, "KRW"),),
        cash={"KRW": 300_000.0},
        fx_to_krw={"KRW": 1.0},
        fx_source="broker",
    )


class FakeReader:
    def __init__(self, snap=None, exc=None):
        self._snap = snap or snapshot()
        self._exc = exc

    def snapshot(self):
        if self._exc:
            raise self._exc
        return self._snap


class FakeNotifier:
    def __init__(self, exc=None):
        self.sent: list[str] = []
        self._exc = exc

    def send(self, text):
        if self._exc:
            raise self._exc
        self.sent.append(text)


def run(tmp_path, reader=None, notifier=None, **kwargs):
    return run_briefing(
        {},
        reader=reader or FakeReader(),
        notifier=notifier or FakeNotifier(),
        cache=None,
        state_root=tmp_path,
        skip_update=True,
        **kwargs,
    )


class TestHappyPath:
    def test_sends_the_briefing(self, tmp_path):
        notifier = FakeNotifier()
        result = run(tmp_path, notifier=notifier)
        assert result.ok and result.sent
        assert len(notifier.sent) == 1

    def test_stores_the_snapshot(self, tmp_path):
        run(tmp_path)
        assert len(list((tmp_path / "account").glob("*.json"))) == 1

    def test_a_second_run_compares_against_the_first(self, tmp_path):
        run(tmp_path, reader=FakeReader(snapshot(1)))
        result = run(tmp_path, reader=FakeReader(snapshot(15)))
        assert "14일" in result.text

    def test_writes_a_run_log(self, tmp_path):
        run(tmp_path)
        logs = list((tmp_path / "briefing_log").glob("*.json"))
        assert len(logs) == 1
        assert json.loads(logs[0].read_text(encoding="utf-8"))["ok"] is True


class TestNoNotify:
    def test_dry_run_renders_but_does_not_send(self, tmp_path):
        notifier = FakeNotifier()
        result = run(tmp_path, notifier=notifier, notify=False)
        assert result.text.strip()
        assert notifier.sent == []
        assert result.sent is False

    def test_dry_run_still_records_the_snapshot(self, tmp_path):
        # The snapshot is the return chain's only source; skipping it would
        # leave a hole that no later run can fill.
        run(tmp_path, notify=False)
        assert len(list((tmp_path / "account").glob("*.json"))) == 1


class TestFailures:
    def test_a_reader_failure_is_reported_not_swallowed(self, tmp_path):
        result = run(tmp_path, reader=FakeReader(exc=RuntimeError("403")))
        assert not result.ok
        assert "403" in " ".join(result.messages)

    def test_a_reader_failure_writes_no_snapshot(self, tmp_path):
        run(tmp_path, reader=FakeReader(exc=RuntimeError("boom")))
        assert list((tmp_path / "account").glob("*.json")) == []

    def test_a_send_failure_still_returns_the_text(self, tmp_path):
        # The user is at the keyboard; the console is the fallback screen.
        result = run(tmp_path, notifier=FakeNotifier(exc=NotifyError("no network")))
        assert not result.ok
        assert result.text.strip()

    def test_a_send_failure_keeps_the_snapshot(self, tmp_path):
        run(tmp_path, notifier=FakeNotifier(exc=NotifyError("no network")))
        assert len(list((tmp_path / "account").glob("*.json"))) == 1
