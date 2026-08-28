from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tradingbot.account.base import AccountSnapshot, Holding
from tradingbot.briefing_service import build_account_reader, run_briefing
from tradingbot.notify.telegram import NotifyError

KST = timezone(timedelta(hours=9))


def test_build_account_reader_forwards_the_state_root(monkeypatch, tmp_path):
    marker = object()
    seen = []
    monkeypatch.setattr("tradingbot.account.toss.build_reader", lambda root: seen.append(root) or marker)

    assert build_account_reader(tmp_path) is marker
    assert seen == [tmp_path]


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


def test_a_news_failure_is_reported_and_the_briefing_still_renders(tmp_path):
    calls = []

    def failing_dart(corp_code, since, until):
        calls.append((corp_code, since, until))
        raise TimeoutError("DART timed out")

    notifier = FakeNotifier()
    result = run(
        tmp_path,
        notifier=notifier,
        news_fetchers=(failing_dart, None, {"005930": "00126380"}),
    )

    until = snapshot(15).as_of.date()
    assert calls == [("00126380", until - timedelta(days=7), until)]
    assert result.ok and result.sent
    assert notifier.sent == [result.text]
    assert "[새 소식]" in result.text
    assert "DART timed out" in result.text
    assert "계좌 숫자는 영향받지 않습니다." in result.text
    assert "DART timed out" in " ".join(result.messages)
    assert (tmp_path / "news" / "latest.json").is_file()


def test_no_news_flag_skips_the_fetch_entirely(monkeypatch, tmp_path, capsys):
    from tradingbot.cli import build_parser

    def unexpected_build():
        raise AssertionError("--no-news must not build live fetchers")

    monkeypatch.setattr("tradingbot.cli.load_config", lambda _path: {})
    monkeypatch.setattr("tradingbot.cli.resolve_project_path", lambda _path: tmp_path)
    monkeypatch.setattr(
        "tradingbot.briefing_service.build_account_reader", lambda _root: FakeReader()
    )
    monkeypatch.setattr("tradingbot.data.news.build_fetchers", unexpected_build)
    monkeypatch.setattr("tradingbot.services.build_cache", lambda _config: None)

    args = build_parser().parse_args(
        ["briefing", "weekly", "--dry-run", "--skip-update", "--no-news"]
    )

    assert args.handler(args) == 0
    assert "[새 소식]" not in capsys.readouterr().out
    assert not (tmp_path / "news").exists()
