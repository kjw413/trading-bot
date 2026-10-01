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
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
from tradingbot.data.news import (
    DartFetcher,
    NewsResult,
    YahooFetcher,
    fetch_news,
    save_news,
)
from tradingbot.instruments import LeverageState, leverage_of
from tradingbot.notify.base import Notifier
from tradingbot.proposal import Proposal, propose_rebalance
from tradingbot.proxy import ProxyMeasurement, measure_proxy_pair
from tradingbot.reconciliation import ReconciliationResult, reconcile_holdings
from tradingbot.report.briefing import render_briefing
from tradingbot.services import update_data
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

ACCOUNT_DIRNAME = "account"
LOG_DIRNAME = "briefing_log"

NewsFetchers = tuple[DartFetcher | None, YahooFetcher | None, dict[str, str]]

# These are proxy candidates to measure, not asserted benchmark relationships.
# A pair reaches the expected-return panel only when measure_proxy_pair qualifies
# it. Leverage stays owned by the instrument registry rather than duplicated here.
_PROXY_CANDIDATES: dict[str, tuple[str, float | None]] = {
    "FNGU": ("QQQ", None),
    "GGLL": ("QQQ", None),
    "SOXL": ("SOXX", None),
    # The registry deliberately keeps SPCX leverage unknown. Three is only the
    # proxy hypothesis being tested here; it is never a fallback registry fact.
    "SPCX": ("SPY", 3.0),
    "TECL": ("XLK", None),
}


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


def _cached_reconciliation_inputs(
    cache: ParquetCache | None,
    snapshot: AccountSnapshot,
) -> tuple[
    dict[str, pd.Series],
    dict[str, pd.Series],
    dict[str, ProxyMeasurement],
]:
    """Read and measure the held proxy candidates from the CLI's price cache."""
    if cache is None:
        return {}, {}, {}

    holding_prices: dict[str, pd.Series] = {}
    proxy_prices: dict[str, pd.Series] = {}
    measurements: dict[str, ProxyMeasurement] = {}
    for held in snapshot.holdings:
        symbol = held.symbol.strip().upper()
        candidate = _PROXY_CANDIDATES.get(symbol)
        if candidate is None:
            continue
        proxy_symbol, candidate_multiple = candidate

        registered_multiple = leverage_of(symbol)
        if candidate_multiple is None:
            if registered_multiple is LeverageState.UNKNOWN:
                raise ValueError(f"proxy candidate multiple is unknown for {symbol}")
            measured_multiple = registered_multiple
        else:
            measured_multiple = candidate_multiple

        traded = cache.read(held.market, symbol)["close"].dropna()
        proxy = proxy_prices.get(proxy_symbol)
        if proxy is None:
            proxy = cache.read(held.market, proxy_symbol)["close"].dropna()
            proxy_prices[proxy_symbol] = proxy
        holding_prices[symbol] = traded
        measurements[symbol] = measure_proxy_pair(
            traded_symbol=symbol,
            proxy_symbol=proxy_symbol,
            traded_prices=traded,
            proxy_prices=proxy,
            leverage=measured_multiple,
        )

    return holding_prices, proxy_prices, measurements


def run_briefing(
    config: dict[str, Any],
    *,
    reader: AccountReader,
    notifier: Notifier | None,
    cache: ParquetCache | None,
    state_root: str | Path,
    skip_update: bool = False,
    notify: bool = True,
    news: bool = True,
    news_fetchers: NewsFetchers | None = None,
    proposal: bool = True,
    ledger_root: str | Path | None = None,
    current_commit: str | None = None,
    reconciliation: bool = True,
    reconciliation_holding_prices: Mapping[str, pd.Series] | None = None,
    reconciliation_proxy_prices: Mapping[str, pd.Series] | None = None,
    reconciliation_measurements: Mapping[str, ProxyMeasurement] | None = None,
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

    until = curr.as_of.date()
    since = prev.as_of.date() if prev is not None else until - timedelta(days=7)

    if not skip_update:
        _refresh_prices(config, curr, cache, messages)

    news_result: NewsResult | None = None
    if news:
        try:
            dart, yahoo, corp_codes = news_fetchers or (None, None, {})
            news_result = fetch_news(
                curr.holdings,
                since=since,
                until=until,
                dart=dart,
                yahoo=yahoo,
                corp_codes=corp_codes,
            )
        except Exception as exc:  # noqa: BLE001
            # Optional context must not stop the account briefing.
            detail = str(exc).strip() or type(exc).__name__
            news_result = NewsResult(
                items=(),
                failures={"collection": detail},
                dropped={},
                skipped={},
            )
            LOGGER.warning("News collection failed: %s", exc)

        for source, reason in news_result.failures.items():
            messages.append(f"뉴스 수집에 실패했습니다 ({source}): {reason}")

        try:
            save_news(news_result, state)
        except Exception as exc:  # noqa: BLE001
            # A missing archive must not make the rendered briefing undeliverable.
            detail = str(exc).strip() or type(exc).__name__
            messages.append(f"뉴스 기록을 저장하지 못했습니다: {detail}")
            LOGGER.warning("News store failed: %s", exc)
            news_result = NewsResult(
                items=news_result.items,
                failures={
                    **news_result.failures,
                    "store": f"뉴스 기록을 저장하지 못했습니다: {detail}",
                },
                dropped=news_result.dropped,
                skipped=news_result.skipped,
            )

    proposal_result: Proposal | None = None
    proposal_failure: str | None = None
    if proposal and ledger_root is not None and current_commit is not None:
        try:
            proposal_result = propose_rebalance(
                curr,
                ledger_root=ledger_root,
                current_commit=current_commit,
            )
        except Exception as exc:  # noqa: BLE001
            # A proposal is optional context. Account numbers and delivery are
            # still useful when its ledger is unreadable or its engine fails.
            detail = str(exc).strip() or type(exc).__name__
            messages.append(f"주간 판단 생성에 실패했습니다: {detail}")
            proposal_failure = (
                "[이번 주 판단]\n"
                f"- 이번 주 판단을 만들지 못했습니다 ({detail}). "
                "계좌 숫자는 영향받지 않습니다."
            )
            LOGGER.warning("Proposal generation failed: %s", exc)

    reconciliation_result: ReconciliationResult | None = None
    reconciliation_failure: str | None = None
    if reconciliation:
        try:
            supplied = (
                reconciliation_holding_prices,
                reconciliation_proxy_prices,
                reconciliation_measurements,
            )
            if any(value is not None for value in supplied):
                if any(value is None for value in supplied):
                    raise ValueError(
                        "reconciliation prices and measurements must be supplied together"
                    )
                holding_prices = reconciliation_holding_prices
                proxy_prices = reconciliation_proxy_prices
                measurements = reconciliation_measurements
            else:
                holding_prices, proxy_prices, measurements = (
                    _cached_reconciliation_inputs(cache, curr)
                )

            if holding_prices:
                reconciliation_result = reconcile_holdings(
                    period_start=since,
                    period_end=until,
                    holding_prices=holding_prices,
                    proxy_prices=proxy_prices,
                    proxy_measurements=measurements,
                    state_root=state,
                )
        except Exception as exc:  # noqa: BLE001
            # Reconciliation is optional context. Account numbers and delivery
            # remain useful when a cache series, measurement, or history fails.
            detail = str(exc).strip() or type(exc).__name__
            messages.append(f"실현 수익과 예상 비교 생성에 실패했습니다: {detail}")
            reconciliation_failure = (
                "[실현 수익과 예상 비교]\n"
                f"- 실현 수익과 예상 비교를 만들지 못했습니다 ({detail}). "
                "계좌 숫자는 영향받지 않습니다."
            )
            LOGGER.warning("Reconciliation failed: %s", exc)

    text = render_briefing(
        curr,
        prev,
        price_history=_price_history(cache, curr, prev.as_of if prev else None),
        news=news_result,
        proposal=proposal_result,
        reconciliation=reconciliation_result,
    )
    if proposal_failure is not None:
        text = "\n\n".join(part for part in (text, proposal_failure) if part)
    if reconciliation_failure is not None:
        text = "\n\n".join(part for part in (text, reconciliation_failure) if part)

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
