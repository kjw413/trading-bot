from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def current_git_commit(cwd: Path | None = None) -> str:
    """Current HEAD hash, or 'unknown' outside a repo / without git."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def record_experiment(
    root: Path,
    *,
    kind: str,
    params: dict[str, Any],
    metrics: dict[str, Any],
    created_at: datetime | None = None,
) -> Path:
    """Write one experiment record as JSON under `root`; returns the path."""
    root.mkdir(parents=True, exist_ok=True)
    created = created_at or datetime.now(timezone.utc)
    experiment_id = f"{created:%Y%m%dT%H%M%S}_{kind}_{uuid4().hex[:8]}"
    record = {
        "experiment_id": experiment_id,
        "kind": kind,
        "git_commit": current_git_commit(),
        "created_at": created.isoformat(),
        "params": params,
        "metrics": metrics,
    }
    path = root / f"{experiment_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
