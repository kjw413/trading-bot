"""Read credentials from a local `.env` file into the process environment.

Everything in this project reads credentials straight from `os.environ`
(`data/credentials.py`, `account/toss.py`, `notify/telegram.py`). That works
when a command is typed into a shell that already holds them, and it silently
fails for the way this project is actually meant to be run: a double-clicked
`.bat`. A launcher starts a fresh process, so a `$env:TOSS_CLIENT_ID` set in
some PowerShell window is invisible to it, and the run dies reporting the
credential as "not set" while the person who set it is looking right at it.

Persisting the names into the Windows user environment is the other way out,
but it puts secrets in the registry and needs a terminal restart to take. A
file next to the launcher is the mechanism people already reach for -- and
did reach for here, which is what prompted this module.

Deliberately no `python-dotenv`: the format below is a dozen lines and adding
a dependency to parse `KEY=value` is not a trade worth making.

The environment always wins. A name already set is never overwritten, so a
one-off value typed into a shell still beats a file set up weeks ago.
"""

from __future__ import annotations

import logging
import os
from collections.abc import MutableMapping
from pathlib import Path

LOGGER = logging.getLogger(__name__)

ENV_FILENAME = ".env"

# The repo root when installed editable (src layout: src/tradingbot/env_file.py).
# Used only as a fallback for commands typed outside the project directory;
# the launchers `cd /d "%~dp0"` first, so they hit the working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env_file(
    path: Path | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> list[str]:
    """Copy `KEY=value` lines into `environ`, returning the names it set.

    Names already present are left alone. A missing or unreadable file is not
    an error: the credentials may well be in the environment already, and a
    startup helper has no business stopping a run that would otherwise work.
    """
    target = os.environ if environ is None else environ
    candidate = _resolve(path)
    if candidate is None:
        return []

    try:
        text = candidate.read_text(encoding="utf-8-sig")
    except OSError as exc:
        LOGGER.warning("%s 파일을 읽지 못해 건너뜁니다: %s", candidate, exc)
        return []

    loaded: list[str] = []
    for line in text.splitlines():
        parsed = _parse(line)
        if parsed is None:
            continue
        name, value = parsed
        if name in target:
            continue
        target[name] = value
        loaded.append(name)

    if loaded:
        # Names only -- never the values. This line reaches log files.
        LOGGER.info("%s 에서 환경변수를 읽었습니다: %s", candidate, ", ".join(loaded))
    return loaded


def _resolve(path: Path | None) -> Path | None:
    if path is not None:
        return path
    for directory in (Path.cwd(), PROJECT_ROOT):
        candidate = directory / ENV_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _parse(line: str) -> tuple[str, str] | None:
    """One line to a (name, value) pair, or None if it carries no setting.

    A `#` only starts a comment at the beginning of a line. Treating it as
    one mid-value would quietly truncate a secret that happens to contain it,
    which is a far worse failure than an unsupported trailing comment.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :]
    name, separator, value = stripped.partition("=")
    if not separator:
        return None
    name = name.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if not name or not value:
        # `.env.template` ships every name with a blank value. Copying it
        # must not plant empty names that read as "set" but satisfy nothing.
        return None
    return name, value
