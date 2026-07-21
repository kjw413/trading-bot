from __future__ import annotations

import json

import pytest

from tradingbot.cli import build_parser, cmd_data_pipeline
from tradingbot.data.pipeline import run_pipeline, with_retry


@pytest.fixture
def config(tmp_path):
    return {
        "pipeline": {
            "processed_dir": str(tmp_path / "processed"),
            "log_dir": str(tmp_path / "log"),
            "symbols": ["005930"],
            "fundamental_years": 1,
            "retry_attempts": 2,
        }
    }


def ok_collector(name: str, rows: int = 5):
    def collect(**kwargs):
        return rows

    collect.__name__ = name
    return collect


class TestWithRetry:
    def test_returns_first_success_without_sleeping(self):
        slept: list[float] = []
        assert with_retry(lambda: 42, attempts=3, sleep=slept.append) == 42
        assert slept == []

    def test_retries_then_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")
            return "ok"

        assert with_retry(flaky, attempts=3, base_delay=0.01, sleep=lambda _: None) == "ok"
        assert calls["n"] == 3

    def test_reraises_after_exhausting_attempts(self):
        with pytest.raises(RuntimeError, match="always"):
            with_retry(
                lambda: (_ for _ in ()).throw(RuntimeError("always")),
                attempts=2,
                base_delay=0.01,
                sleep=lambda _: None,
            )

    def test_backoff_grows(self):
        slept: list[float] = []

        def always_fails():
            raise RuntimeError("no")

        with pytest.raises(RuntimeError):
            with_retry(always_fails, attempts=3, base_delay=1.0, sleep=slept.append)
        assert slept == [1.0, 2.0]

    def test_no_retry_types_fail_immediately(self):
        slept: list[float] = []
        calls = {"n": 0}

        def missing_config():
            calls["n"] += 1
            raise KeyError("no api key")

        # A missing key is not transient — retrying only wastes the batch's time.
        with pytest.raises(KeyError):
            with_retry(
                missing_config, attempts=3, base_delay=1.0, no_retry=(KeyError,), sleep=slept.append
            )
        assert calls["n"] == 1
        assert slept == []


class TestRunPipeline:
    def test_all_sources_ok(self, config):
        result = run_pipeline(
            config,
            market="KR",
            symbols=["005930"],
            collectors={"macro": ok_collector("macro"), "flows": ok_collector("flows", 7)},
        )
        assert result.ok
        assert {r.name for r in result.results} == {"macro", "flows"}
        assert sum(r.rows for r in result.results) == 12

    def test_one_source_failure_does_not_stop_others(self, config):
        def boom(**kwargs):
            raise RuntimeError("source down")

        result = run_pipeline(
            config,
            market="KR",
            symbols=["005930"],
            collectors={"macro": boom, "flows": ok_collector("flows", 7)},
        )
        assert not result.ok
        by_name = {r.name: r for r in result.results}
        assert by_name["macro"].status == "failed"
        assert "source down" in by_name["macro"].message
        assert by_name["flows"].status == "ok"
        assert by_name["flows"].rows == 7

    def test_writes_run_log_json(self, config, tmp_path):
        run_pipeline(
            config, market="KR", symbols=["005930"], collectors={"macro": ok_collector("macro")}
        )
        logs = list((tmp_path / "log").glob("*.json"))
        assert len(logs) == 1
        record = json.loads(logs[0].read_text(encoding="utf-8"))
        assert record["market"] == "KR"
        assert record["results"][0]["name"] == "macro"
        assert record["results"][0]["status"] == "ok"

    def test_missing_credentials_skips_source_without_failing(self, config, monkeypatch):
        from tradingbot.data.credentials import MissingCredentialsError

        def needs_key(**kwargs):
            raise MissingCredentialsError("no key")

        result = run_pipeline(
            config,
            market="KR",
            symbols=["005930"],
            collectors={"fundamentals": needs_key, "macro": ok_collector("macro")},
        )
        by_name = {r.name: r for r in result.results}
        assert by_name["fundamentals"].status == "skipped"
        # A missing optional key is not a pipeline failure.
        assert result.ok

    def test_result_is_serializable(self, config):
        result = run_pipeline(
            config, market="KR", symbols=["005930"], collectors={"macro": ok_collector("macro")}
        )
        payload = json.dumps(result.to_dict())
        assert "macro" in payload


class TestCli:
    def test_parser_wires_data_pipeline(self):
        parser = build_parser()
        args = parser.parse_args(["data", "pipeline", "--market", "KR"])
        assert args.handler is cmd_data_pipeline
        assert args.market == "KR"

    def test_symbols_are_optional(self):
        parser = build_parser()
        args = parser.parse_args(["data", "pipeline", "--market", "KR"])
        assert args.symbols is None
