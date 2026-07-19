from __future__ import annotations

import json
from datetime import datetime, timezone

from tradingbot.research.experiment import current_git_commit, record_experiment


def test_record_experiment_writes_json(tmp_path):
    path = record_experiment(
        tmp_path / "experiments", kind="factor_report", params={"market": "US"}, metrics={"ic": 0.03}
    )
    assert path.exists()
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["kind"] == "factor_report"
    assert record["params"] == {"market": "US"}
    assert record["metrics"] == {"ic": 0.03}
    assert record["experiment_id"] == path.stem
    assert record["git_commit"]  # hash or "unknown", never empty


def test_record_experiment_ids_are_unique_even_at_same_timestamp(tmp_path):
    root = tmp_path / "experiments"
    created = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    first = record_experiment(root, kind="x", params={}, metrics={}, created_at=created)
    second = record_experiment(root, kind="x", params={}, metrics={}, created_at=created)
    assert first != second


def test_current_git_commit_outside_repo_is_unknown(tmp_path):
    assert current_git_commit(cwd=tmp_path) == "unknown"


def test_record_experiment_git_hash_resolved_from_root(tmp_path):
    path = record_experiment(tmp_path / "experiments", kind="x", params={}, metrics={})
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["git_commit"] == "unknown"  # tmp root lives outside any repo
