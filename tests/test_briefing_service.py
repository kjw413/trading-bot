from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from tradingbot.account.base import AccountSnapshot, Holding
from tradingbot.briefing_service import build_account_reader, run_briefing
from tradingbot.notify.telegram import NotifyError
from tradingbot.proxy import ProxyMeasurement, ProxyStatus
from tradingbot.reconciliation import load_reconciliation

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


def proposal_snapshot():
    return AccountSnapshot(
        as_of=datetime(2026, 8, 15, 9, 0, tzinfo=KST),
        holdings=(
            Holding("SOXL", "US", 1.0, "1", 20.0, 25.0, "USD"),
            Holding("TECL", "US", 2.0, "2", 30.0, 35.0, "USD"),
        ),
        cash={"KRW": 300_000.0},
        fx_to_krw={"KRW": 1.0, "USD": 1_350.0},
        fx_source="broker",
    )


def test_an_absent_proposal_ledger_renders_never_evaluated_per_holding(tmp_path):
    result = run(
        tmp_path,
        reader=FakeReader(proposal_snapshot()),
        ledger_root=tmp_path / "missing-ledger-root",
        current_commit="running-commit",
    )

    assert result.ok and result.sent
    assert "[이번 주 판단]" in result.text
    assert "SOXL: 아직 아무도 이 보유 종목의 성과를 재보지 않았고" in result.text
    assert "TECL: 아직 아무도 이 보유 종목의 성과를 재보지 않았고" in result.text


def test_a_proposal_failure_is_visible_and_does_not_fail_delivery(
    monkeypatch, tmp_path
):
    def failing_proposal(*_args, **_kwargs):
        raise ValueError("corrupt ledger")

    monkeypatch.setattr(
        "tradingbot.briefing_service.propose_rebalance", failing_proposal
    )
    notifier = FakeNotifier()
    result = run(
        tmp_path,
        reader=FakeReader(proposal_snapshot()),
        notifier=notifier,
        ledger_root=tmp_path,
        current_commit="running-commit",
    )

    assert result.ok and result.sent
    assert notifier.sent == [result.text]
    assert "[전체]" in result.text
    assert "[이번 주 판단]" in result.text
    assert "corrupt ledger" in result.text
    assert "계좌 숫자는 영향받지 않습니다." in result.text
    assert "corrupt ledger" in " ".join(result.messages)


def test_cli_injects_the_proposal_ledger_root_and_running_commit(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from tradingbot.cli import build_parser

    calls = {}

    def resolve(path):
        calls.setdefault("resolved", []).append(path)
        return tmp_path / path

    def fake_run(_config, **kwargs):
        calls["run"] = kwargs
        return SimpleNamespace(
            text="",
            messages=[],
            snapshot_path=None,
            sent=False,
            ok=True,
        )

    monkeypatch.setattr("tradingbot.cli.load_config", lambda _path: {})
    monkeypatch.setattr("tradingbot.cli.resolve_project_path", resolve)
    monkeypatch.setattr(
        "tradingbot.briefing_service.build_account_reader", lambda _root: FakeReader()
    )
    monkeypatch.setattr("tradingbot.briefing_service.run_briefing", fake_run)
    monkeypatch.setattr("tradingbot.services.build_cache", lambda _config: None)

    def fake_commit(*, cwd):
        calls["commit_cwd"] = cwd
        return "running-commit"

    monkeypatch.setattr(
        "tradingbot.research.experiment.current_git_commit", fake_commit
    )

    args = build_parser().parse_args(
        ["briefing", "weekly", "--dry-run", "--skip-update", "--no-news"]
    )

    assert args.handler(args) == 0
    assert calls["run"]["proposal"] is True
    assert calls["run"]["ledger_root"] == tmp_path / "reports"
    assert calls["run"]["current_commit"] == "running-commit"
    assert calls["commit_cwd"] == tmp_path


def test_no_proposal_flag_skips_the_ledger_read(monkeypatch, tmp_path, capsys):
    from tradingbot.cli import build_parser

    def unexpected_read(_root):
        raise AssertionError("--no-proposal must not read the promotion ledger")

    def unexpected_commit(*, cwd):
        raise AssertionError("--no-proposal must not resolve the running commit")

    monkeypatch.setattr("tradingbot.cli.load_config", lambda _path: {})
    monkeypatch.setattr("tradingbot.cli.resolve_project_path", lambda _path: tmp_path)
    monkeypatch.setattr(
        "tradingbot.briefing_service.build_account_reader",
        lambda _root: FakeReader(proposal_snapshot()),
    )
    monkeypatch.setattr("tradingbot.services.build_cache", lambda _config: None)
    monkeypatch.setattr("tradingbot.proposal._load_records", unexpected_read)
    monkeypatch.setattr(
        "tradingbot.research.experiment.current_git_commit", unexpected_commit
    )

    args = build_parser().parse_args(
        [
            "briefing",
            "weekly",
            "--dry-run",
            "--skip-update",
            "--no-news",
            "--no-proposal",
        ]
    )

    assert args.handler(args) == 0
    assert "[이번 주 판단]" not in capsys.readouterr().out
def reconciliation_snapshot(day=15):
    return AccountSnapshot(
        as_of=datetime(2026, 8, day, 9, 0, tzinfo=KST),
        holdings=(Holding("SOXL", "US", 1.0, "1", 20.0, 25.0, "USD"),),
        cash={"KRW": 300_000.0},
        fx_to_krw={"KRW": 1.0, "USD": 1_350.0},
        fx_source="broker",
    )


def weekly_prices(start, end, first, last):
    return pd.Series(
        [first, last],
        index=pd.to_datetime([start, end]),
        dtype=float,
    )


def qualified_soxl_measurement():
    return ProxyMeasurement(
        traded_symbol="SOXL",
        proxy_symbol="SOXX",
        leverage=3.0,
        beta=3.0,
        r_squared=0.99,
        observations=252,
        qualifies=True,
    )


def test_the_first_reconciliation_run_writes_and_renders_its_first_entry(tmp_path):
    result = run(
        tmp_path,
        reader=FakeReader(reconciliation_snapshot()),
        news=False,
        proposal=False,
        reconciliation_holding_prices={
            "SOXL": weekly_prices("2026-08-08", "2026-08-15", 100, 108)
        },
        reconciliation_proxy_prices={
            "SOXX": weekly_prices("2026-08-08", "2026-08-15", 100, 102)
        },
        reconciliation_measurements={"SOXL": qualified_soxl_measurement()},
    )

    stored = load_reconciliation(tmp_path)
    assert stored is not None
    assert len(stored.entries) == 1
    assert stored.entries[0].cumulative_gap_percentage_points == pytest.approx(2.0)
    assert result.ok and result.sent
    assert "[실현 수익과 예상 비교]" in result.text
    assert "추적 시작 뒤 누적 차이는 +2.0%포인트" in result.text


def test_reconciliation_uses_the_account_comparison_period(tmp_path):
    run(
        tmp_path,
        reader=FakeReader(reconciliation_snapshot(1)),
        notify=False,
        news=False,
        proposal=False,
        reconciliation=False,
    )

    run(
        tmp_path,
        reader=FakeReader(reconciliation_snapshot(15)),
        notify=False,
        news=False,
        proposal=False,
        reconciliation_holding_prices={
            "SOXL": weekly_prices("2026-08-01", "2026-08-15", 100, 108)
        },
        reconciliation_proxy_prices={
            "SOXX": weekly_prices("2026-08-01", "2026-08-15", 100, 102)
        },
        reconciliation_measurements={"SOXL": qualified_soxl_measurement()},
    )

    stored = load_reconciliation(tmp_path)
    assert stored is not None
    assert stored.entries[0].period_start.isoformat() == "2026-08-01"
    assert stored.entries[0].period_end.isoformat() == "2026-08-15"


def test_a_reconciliation_failure_is_visible_and_does_not_fail_delivery(
    monkeypatch, tmp_path
):
    def failing_reconciliation(**_kwargs):
        raise ValueError("corrupt reconciliation history")

    monkeypatch.setattr(
        "tradingbot.briefing_service.reconcile_holdings", failing_reconciliation
    )
    notifier = FakeNotifier()
    result = run(
        tmp_path,
        reader=FakeReader(reconciliation_snapshot()),
        notifier=notifier,
        news=False,
        proposal=False,
        reconciliation_holding_prices={
            "SOXL": weekly_prices("2026-08-08", "2026-08-15", 100, 108)
        },
        reconciliation_proxy_prices={
            "SOXX": weekly_prices("2026-08-08", "2026-08-15", 100, 102)
        },
        reconciliation_measurements={"SOXL": qualified_soxl_measurement()},
    )

    assert result.ok and result.sent
    assert notifier.sent == [result.text]
    assert "[전체]" in result.text
    assert "[실현 수익과 예상 비교]" in result.text
    assert "corrupt reconciliation history" in result.text
    assert "계좌 숫자는 영향받지 않습니다." in result.text
    assert "corrupt reconciliation history" in " ".join(result.messages)


def cache_price_frames():
    returns = [0.01 if index % 2 == 0 else -0.01 for index in range(252)]
    proxy_values = [100.0]
    holding_values = [100.0]
    for period_return in returns:
        proxy_values.append(proxy_values[-1] * (1.0 + period_return))
        holding_values.append(holding_values[-1] * (1.0 + 3.0 * period_return))
    index = pd.bdate_range(end="2026-08-14", periods=253)
    return {
        "SOXL": pd.DataFrame({"close": holding_values}, index=index),
        "SOXX": pd.DataFrame({"close": proxy_values}, index=index),
    }


def test_cli_supplies_reconciliation_prices_and_measurements_from_the_cache(
    monkeypatch, tmp_path, capsys
):
    from tradingbot.cli import build_parser

    class FakeCache:
        def __init__(self):
            self.frames = cache_price_frames()
            self.calls = []

        def read(self, market, symbol):
            self.calls.append((market, symbol))
            return self.frames[symbol]

    cache = FakeCache()
    monkeypatch.setattr("tradingbot.cli.load_config", lambda _path: {})
    monkeypatch.setattr("tradingbot.cli.resolve_project_path", lambda _path: tmp_path)
    monkeypatch.setattr(
        "tradingbot.briefing_service.build_account_reader",
        lambda _root: FakeReader(reconciliation_snapshot()),
    )
    monkeypatch.setattr("tradingbot.services.build_cache", lambda _config: cache)

    args = build_parser().parse_args(
        [
            "briefing",
            "weekly",
            "--dry-run",
            "--skip-update",
            "--no-news",
            "--no-proposal",
        ]
    )

    assert args.handler(args) == 0
    assert ("US", "SOXL") in cache.calls
    assert ("US", "SOXX") in cache.calls
    assert load_reconciliation(tmp_path) is not None
    assert "[실현 수익과 예상 비교]" in capsys.readouterr().out


def test_no_reconciliation_flag_skips_proxy_cache_reads(
    monkeypatch, tmp_path, capsys
):
    from tradingbot.cli import build_parser

    class HoldingOnlyCache:
        def read(self, market, symbol):
            if symbol != "SOXL":
                raise AssertionError(
                    "--no-reconciliation must not read a proxy price series"
                )
            return cache_price_frames()[symbol]

    monkeypatch.setattr("tradingbot.cli.load_config", lambda _path: {})
    monkeypatch.setattr("tradingbot.cli.resolve_project_path", lambda _path: tmp_path)
    monkeypatch.setattr(
        "tradingbot.briefing_service.build_account_reader",
        lambda _root: FakeReader(reconciliation_snapshot()),
    )
    monkeypatch.setattr(
        "tradingbot.services.build_cache", lambda _config: HoldingOnlyCache()
    )

    args = build_parser().parse_args(
        [
            "briefing",
            "weekly",
            "--dry-run",
            "--skip-update",
            "--no-news",
            "--no-proposal",
            "--no-reconciliation",
        ]
    )

    assert args.handler(args) == 0
    assert "[실현 수익과 예상 비교]" not in capsys.readouterr().out
    assert not (tmp_path / "reconciliation").exists()


def test_an_unknown_leverage_holding_is_recorded_as_unmeasurable(tmp_path):
    index = pd.bdate_range(end="2026-08-14", periods=52)
    frame = pd.DataFrame({"close": range(100, 152)}, index=index)

    class FakeCache:
        def read(self, market, symbol):
            assert market == "US"
            assert symbol in {"SPCX", "SPY"}
            return frame

    current = AccountSnapshot(
        as_of=datetime(2026, 8, 15, 9, 0, tzinfo=KST),
        holdings=(Holding("SPCX", "US", 1.0, "1", 20.0, 25.0, "USD"),),
        cash={"KRW": 300_000.0},
        fx_to_krw={"KRW": 1.0, "USD": 1_350.0},
        fx_source="broker",
    )

    result = run_briefing(
        {},
        reader=FakeReader(current),
        notifier=FakeNotifier(),
        cache=FakeCache(),
        state_root=tmp_path,
        skip_update=True,
        news=False,
        proposal=False,
    )

    stored = load_reconciliation(tmp_path)
    assert stored is not None
    assert stored.entries[0].symbol == "SPCX"
    assert stored.entries[0].status is ProxyStatus.UNMEASURABLE
    assert stored.entries[0].expected_return is None
    assert "잴 자료가 부족해" in result.text
