"""Assembles the weekly briefing: read the account, store it, render, deliver.

Lives beside `services.py` and for the same reason — the CLI passes arguments
and prints, and nothing else. Everything the run depends on (the account
reader, the notifier, the price cache) is injected, so the whole sequence is
testable without a brokerage, a phone, or a network.

Two orderings here are load-bearing:

* The previous snapshot is read **before** the new one is written. Saving
  first would make the run compare today against itself and report a
  zero-day interval.
* The snapshot is written whether or not delivery succeeds. It is the only
  source the return chain has; dropping it because Telegram was down would
  put a permanent hole in the history.

Delivery failure does not throw away the text. The user is at the keyboard —
if the phone never gets it, the console is the fallback screen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from tradingbot.account.base import (
    AccountReader,
    AccountSnapshot,
    load_latest,
    save_snapshot,
)
from tradingbot.data.cache import ParquetCache
from tradingbot.data.credentials import MissingCredentialsError
from tradingbot.notify.base import Notifier
from tradingbot.report.briefing import render_briefing
from tradingbot.services import update_data
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

ACCOUNT_DIRNAME = "account"
LOG_DIRNAME = "briefing_log"


@dataclass(frozen=True)
class BriefingResult:
    started_at: datetime
    finished_at: datetime
    ok: bool
    text: str
    snapshot_path: Path | None
    sent: bool
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Shaped like `PipelineResult.to_dict` so both logs read the same."""
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "ok": self.ok,
            "sent": self.sent,
            "snapshot_path": str(self.snapshot_path) if self.snapshot_path else None,
            "messages": list(self.messages),
            "text": self.text,
        }


def build_account_reader(state_root: str | Path) -> AccountReader:
    """Build the live read-only account adapter under the configured state root."""
    try:
        from tradingbot.account.toss import build_reader
    except ImportError as exc:
        raise MissingCredentialsError(
            "토스증권 계좌 읽기 모듈을 불러오지 못했습니다. 가상환경 의존성을 "
            "설치한 뒤 다시 실행하세요: .\\.venv\\Scripts\\python.exe -m pip install -e ."
        ) from exc
    return build_reader(state_root)


def _price_history(
    cache: ParquetCache | None,
    snapshot: AccountSnapshot,
    since: datetime | None,
) -> dict[str, pd.Series]:
    """Closing prices per held symbol over the interval, from the local cache.

    A symbol with no cached history is skipped rather than defaulted: an
    invented flat line would read as "this did not move".
    """
    if cache is None:
        return {}

    history: dict[str, pd.Series] = {}
    for held in snapshot.holdings:
        try:
            frame = cache.read(held.market, held.symbol)
        except (FileNotFoundError, ValueError, OSError) as exc:
            LOGGER.info("No cached prices for %s %s: %s", held.market, held.symbol, exc)
            continue
        closes = frame["close"].dropna()
        if since is not None:
            closes = closes[closes.index >= pd.Timestamp(since.date())]
        if len(closes) >= 2:
            history[held.symbol] = closes
    return history


def _refresh_prices(
    config: dict[str, Any],
    snapshot: AccountSnapshot,
    cache: ParquetCache | None,
    messages: list[str],
) -> None:
    """Bring the cache up to date for what is actually held.

    Runs after the account is read, not before, because the account is the
    only authoritative list of held symbols — a symbol bought since the last
    run would otherwise have no prices at all in its first briefing.

    A failure here is recorded and stepped over. Stale trend lines are worth
    less than the rest of the briefing, and losing the whole run over them
    would be the wrong trade.
    """
    by_market: dict[str, list[str]] = {}
    for held in snapshot.holdings:
        by_market.setdefault(held.market.upper(), []).append(held.symbol)

    for market, symbols in by_market.items():
        try:
            update_data(
                config,
                market=market,
                symbols=symbols,
                data_root=cache.root if cache is not None else None,
            )
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            messages.append(f"가격 기록 갱신에 실패했습니다 ({market}): {exc}")
            LOGGER.warning("Price refresh failed for %s: %s", market, exc)


def run_briefing(
    config: dict[str, Any],
    *,
    reader: AccountReader,
    notifier: Notifier | None,
    cache: ParquetCache | None,
    state_root: str | Path,
    skip_update: bool = False,
    notify: bool = True,
) -> BriefingResult:
    """One run of the weekly briefing. `notifier` may be None when notify=False."""
    started = datetime.now(timezone.utc)
    state = Path(state_root)
    messages: list[str] = []

    try:
        curr = reader.snapshot()
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        messages.append(f"계좌를 읽지 못했습니다: {exc}")
        LOGGER.exception("Account read failed")
        return _finish(
            state,
            BriefingResult(
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                ok=False,
                text="",
                snapshot_path=None,
                sent=False,
                messages=messages,
            ),
        )

    prev = load_latest(state / ACCOUNT_DIRNAME)
    snapshot_path = save_snapshot(curr, state / ACCOUNT_DIRNAME)

    if not skip_update:
        _refresh_prices(config, curr, cache, messages)

    text = render_briefing(
        curr,
        prev,
        price_history=_price_history(cache, curr, prev.as_of if prev else None),
    )

    sent = False
    ok = True
    if notify:
        try:
            notifier.send(text)
            sent = True
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            ok = False
            messages.append(f"텔레그램 전송에 실패했습니다: {exc}")
            # Logged as a warning, not an exception: `NotifyError` already
            # names what Telegram objected to, and a traceback above the
            # briefing is noise on a console a non-expert is reading.
            LOGGER.warning("Telegram delivery failed: %s", exc)

    return _finish(
        state,
        BriefingResult(
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            ok=ok,
            text=text,
            snapshot_path=snapshot_path,
            sent=sent,
            messages=messages,
        ),
    )


def _finish(state: Path, result: BriefingResult) -> BriefingResult:
    """Write the run log and hand the result back.

    The log holds the briefing text too. The console scrolls away and the
    phone message can be deleted; this file is what is left to look at when
    the reader wants to know what a past run actually said.
    """
    logs = state / LOG_DIRNAME
    logs.mkdir(parents=True, exist_ok=True)
    # Microseconds, not seconds: two runs started in the same second would
    # otherwise overwrite each other's log, and a run that leaves no record is
    # indistinguishable from a run that never happened.
    path = logs / f"{result.started_at:%Y%m%dT%H%M%S_%f}.json"
    path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
