from __future__ import annotations

import pytest

from tradingbot.data.pipeline import COLLECTOR_MARKETS, _default_collectors, run_pipeline


class TestEventsCollectorRegistration:
    def test_events_covers_both_markets(self):
        # One dataset, one schema, a different source per market: DART
        # provisional results for Korea, SEC 8-K Item 2.02 for the US.
        assert COLLECTOR_MARKETS["events"] == ("KR", "US")

    def test_dart_only_collectors_stay_kr_only(self):
        # Without these guards a --market US run would send US tickers to KRX
        # and DART, writing the result under processed/{dataset}/US/.
        assert COLLECTOR_MARKETS["fundamentals"] == ("KR",)
        assert COLLECTOR_MARKETS["flows"] == ("KR",)
        assert COLLECTOR_MARKETS["valuation"] == ("KR",)

    def test_existing_guards_unchanged(self):
        assert COLLECTOR_MARKETS["prices"] == ("KR", "US")

    def test_the_us_events_collector_uses_edgar_not_dart(self, tmp_path, monkeypatch):
        # The two sources must not cross: a US run reaching DART would ask it
        # for tickers it has never heard of and write nothing, silently.
        monkeypatch.setenv("SEC_USER_AGENT", "test test@example.com")
        seen: list[str] = []
        monkeypatch.setattr(
            "tradingbot.data.cik.CikStore.cik_for",
            lambda self, symbols, **kw: {"AAPL": 320193},
        )
        monkeypatch.setattr(
            "tradingbot.data.edgar.update_edgar_events",
            lambda store, **kw: seen.append("edgar") or 3,
        )
        collectors = _default_collectors(
            processed_root=tmp_path / "processed",
            symbols=["AAPL"],
            market="US",
            fundamental_years=3,
            cache_root=tmp_path / "cache",
        )
        assert collectors["events"](market="US", symbols=["AAPL"]) == 3
        assert seen == ["edgar"]

    def test_the_us_events_collector_fails_early_without_a_contact(self, tmp_path, monkeypatch):
        # SEC answers unidentified requests with an opaque 403. Better to say
        # which variable is missing than to collect nothing and report ok.
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)
        collectors = _default_collectors(
            processed_root=tmp_path / "processed",
            symbols=["AAPL"],
            market="US",
            fundamental_years=3,
            cache_root=tmp_path / "cache",
        )
        with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):
            collectors["events"](market="US", symbols=["AAPL"])

    def test_events_is_a_default_collector(self, tmp_path):
        collectors = _default_collectors(
            processed_root=tmp_path / "processed",
            symbols=["005930"],
            market="KR",
            fundamental_years=3,
            cache_root=tmp_path / "cache",
        )
        assert "events" in collectors


class TestOverlayConfigIsolatesTheOverlay:
    """The two configs must differ only by the overlay.

    `research evaluate` measures the overlay by running one strategy against
    both files, so any other divergence — a fee, a factor, an initial cash
    figure — would land in the excess-return number and be read as the
    overlay's doing.
    """

    def load(self, name: str) -> dict:
        from tradingbot.config import load_config

        return load_config(f"config/{name}")

    def test_only_the_overlay_keys_differ(self):
        base = self.load("default.toml")
        overlay = self.load("kr_theme_event_overlay.toml")
        base_strategy = base["strategies"].pop("theme_multifactor")
        overlay_strategy = overlay["strategies"].pop("theme_multifactor")
        assert base == overlay

        added = set(overlay_strategy) - set(base_strategy)
        assert added == {"event_overlay_window_days", "event_overlay_scale"}
        for key in base_strategy:
            assert overlay_strategy[key] == base_strategy[key]

    def test_the_overlay_is_actually_on(self):
        strategy = self.load("kr_theme_event_overlay.toml")["strategies"]["theme_multifactor"]
        assert strategy["event_overlay_window_days"] >= 0
        assert 0.0 <= strategy["event_overlay_scale"] < 1.0

    def test_the_default_config_leaves_it_off(self):
        strategy = self.load("default.toml")["strategies"]["theme_multifactor"]
        assert "event_overlay_window_days" not in strategy


@pytest.fixture
def config(tmp_path):
    return {
        "pipeline": {
            "processed_dir": str(tmp_path / "processed"),
            "log_dir": str(tmp_path / "log"),
            "retry_attempts": 1,
        },
        "data": {"cache_dir": str(tmp_path / "cache")},
    }


class TestEventsInPipelineRun:
    def test_events_rows_are_reported(self, config):
        result = run_pipeline(
            config,
            market="KR",
            symbols=["005930"],
            collectors={"events": lambda **kwargs: 4},
        )
        assert result.ok
        by_name = {r.name: r for r in result.results}
        assert by_name["events"].status == "ok"
        assert by_name["events"].rows == 4

    def test_events_runs_for_us_too(self, config):
        result = run_pipeline(
            config,
            market="US",
            symbols=["AAPL"],
            collectors={"events": lambda **kwargs: 4},
        )
        by_name = {r.name: r for r in result.results}
        assert by_name["events"].status == "ok"
        assert by_name["events"].rows == 4

    def test_the_dart_only_collectors_are_still_skipped_for_us(self, config):
        # Widening the events guard must not widen the others: DART and KRX
        # have never heard of a US ticker, and a run that reached them would
        # write nothing under processed/{dataset}/US/ while reporting ok.
        result = run_pipeline(
            config,
            market="US",
            symbols=["AAPL"],
            collectors={
                "fundamentals": lambda **kwargs: 4,
                "flows": lambda **kwargs: 4,
                "valuation": lambda **kwargs: 4,
            },
        )
        by_name = {r.name: r for r in result.results}
        assert {r.status for r in by_name.values()} == {"skipped"}

    def test_a_failing_events_collector_does_not_stop_the_batch(self, config):
        def boom(**kwargs):
            raise RuntimeError("DART down")

        result = run_pipeline(
            config,
            market="KR",
            symbols=["005930"],
            collectors={"events": boom, "macro": lambda **kwargs: 3},
        )
        by_name = {r.name: r for r in result.results}
        assert by_name["events"].status == "failed"
        assert by_name["macro"].status == "ok"
