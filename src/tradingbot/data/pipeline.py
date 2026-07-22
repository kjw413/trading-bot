from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from tradingbot.config import resolve_project_path
from tradingbot.data.credentials import MissingCredentialsError
from tradingbot.data.fundamentals_panel import update_fundamentals
from tradingbot.data.flows import update_flows
from tradingbot.data.macro import update_macro
from tradingbot.data.panel import PanelStore
from tradingbot.data.valuation import update_valuation
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


@dataclass(frozen=True)
class SourceResult:
    name: str
    status: str
    rows: int
    message: str


@dataclass(frozen=True)
class PipelineResult:
    started_at: datetime
    finished_at: datetime
    market: str
    results: list[SourceResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Skipped optional sources are not failures; failed ones are."""
        return all(result.status != STATUS_FAILED for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "market": self.market,
            "ok": self.ok,
            "results": [asdict(result) for result in self.results],
        }


def with_retry(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_delay: float = 2.0,
    no_retry: tuple[type[BaseException], ...] = (),
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Run `fn`, retrying transient failures with exponential backoff.

    `no_retry` names failures that retrying cannot fix — a missing API key is
    the same on the third attempt as the first, and retrying it only delays
    the rest of the batch."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except no_retry:
            raise
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised
            last = exc
            if attempt == attempts - 1:
                break
            delay = base_delay * (2**attempt)
            LOGGER.warning("Attempt %s/%s failed (%s); retrying in %.1fs", attempt + 1, attempts, exc, delay)
            sleep(delay)
    assert last is not None
    raise last


def _default_collectors(
    processed_root: Path,
    symbols: Sequence[str],
    market: str,
    fundamental_years: int,
    cache_root: Path,
) -> dict[str, Callable[..., int]]:
    def prices(**_: Any) -> int:
        """Refresh the OHLCV cache the factor layer reads."""
        from tradingbot.data.cache import ParquetCache
        from tradingbot.data.quality import FAIL, check_ohlcv

        cache = ParquetCache(cache_root)
        rows = 0
        for symbol in symbols:
            try:
                frame = cache.update(market, symbol)
            except Exception:
                LOGGER.exception("Price update failed for %s; skipping", symbol)
                continue
            report = check_ohlcv(frame, dataset=f"prices/{symbol}", market=market)
            if report.severity == FAIL:
                LOGGER.error("Price quality check failed for %s: %s", symbol, report.issues)
            rows += len(frame)
        return rows

    def macro(**_: Any) -> int:
        return update_macro(PanelStore(processed_root, "macro", market))

    def flows(**_: Any) -> int:
        return update_flows(PanelStore(processed_root, "flows", market), symbols=symbols)

    def valuation(**_: Any) -> int:
        return update_valuation(PanelStore(processed_root, "valuation", market), symbols=symbols)

    def fundamentals(**_: Any) -> int:
        from tradingbot.data.corp_codes import CorpCodeStore
        from tradingbot.data.fundamentals_panel import dart_api_key

        dart_api_key()  # raises MissingApiKeyError -> reported as skipped
        this_year = date.today().year
        years = list(range(this_year - fundamental_years + 1, this_year + 1))
        corp_codes = CorpCodeStore(cache_root).corp_code_for(symbols)
        if not corp_codes:
            LOGGER.warning("No DART corp_code resolved for any of %s symbols", len(symbols))
        return update_fundamentals(
            PanelStore(processed_root, "fundamentals", market),
            symbols=symbols,
            corp_codes=corp_codes,
            years=years,
        )

    return {
        "prices": prices,
        "macro": macro,
        "flows": flows,
        "valuation": valuation,
        "fundamentals": fundamentals,
    }


def run_pipeline(
    config: dict[str, Any],
    *,
    market: str,
    symbols: Sequence[str] | None = None,
    processed_root: str | Path | None = None,
    log_root: str | Path | None = None,
    collectors: dict[str, Callable[..., int]] | None = None,
) -> PipelineResult:
    """Run every collector once, isolating failures.

    A source that raises is recorded as failed and the batch continues; the
    next run's incremental fetch picks up whatever it missed."""
    settings = config.get("pipeline", {})
    processed = Path(processed_root or settings.get("processed_dir", "data/processed"))
    if not processed.is_absolute():
        processed = resolve_project_path(processed)
    logs = Path(log_root or settings.get("log_dir", "state/pipeline_log"))
    if not logs.is_absolute():
        logs = resolve_project_path(logs)
    cache = Path(config.get("data", {}).get("cache_dir", "data/cache"))
    if not cache.is_absolute():
        cache = resolve_project_path(cache)

    active_symbols = list(symbols) if symbols else list(settings.get("symbols", []))
    attempts = int(settings.get("retry_attempts", 3))
    active = collectors or _default_collectors(
        processed,
        active_symbols,
        market.upper(),
        int(settings.get("fundamental_years", 3)),
        cache,
    )

    started = datetime.now(timezone.utc)
    results: list[SourceResult] = []
    for name, collector in active.items():
        try:
            rows = with_retry(
                lambda c=collector: c(market=market, symbols=active_symbols),
                attempts=attempts,
                no_retry=(MissingCredentialsError,),
            )
            message = _panel_quality_message(processed, name, market.upper())
            results.append(SourceResult(name, STATUS_OK, int(rows), message))
            LOGGER.info("Pipeline source %s collected %s rows%s", name, rows, f" ({message})" if message else "")
        except MissingCredentialsError as exc:
            results.append(SourceResult(name, STATUS_SKIPPED, 0, str(exc)))
            LOGGER.warning("Pipeline source %s skipped: %s", name, exc)
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            results.append(SourceResult(name, STATUS_FAILED, 0, str(exc)))
            LOGGER.exception("Pipeline source %s failed", name)

    result = PipelineResult(started, datetime.now(timezone.utc), market.upper(), results)
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{started:%Y%m%dT%H%M%S}_{market.upper()}.json"
    log_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _panel_quality_message(processed_root: Path, dataset: str, market: str) -> str:
    """Quality summary for a freshly collected panel, or '' when not applicable.

    Only FAIL-severity issues (duplicate keys, availability inversions) are
    surfaced here, matching the FAIL-only logging already used for the
    `prices` collector's OHLCV check. WARN-level issues (e.g. an "empty"
    panel before a dataset has ever collected anything, or a missing
    trading day) are expected transient states, not operator-actionable
    batch problems, and would otherwise make every early run of a fresh
    dataset look broken."""
    from tradingbot.data.panel import PanelStore
    from tradingbot.data.quality import check_panel

    if dataset == "prices":
        return ""
    try:
        panel = PanelStore(processed_root, dataset, market).read()
    except Exception:
        LOGGER.exception("Quality check could not read panel %s", dataset)
        return "quality check unavailable"
    report = check_panel(panel, dataset=dataset)
    if report.ok:
        return ""
    return f"quality={report.severity}: " + "; ".join(
        f"{issue.check}({issue.count})" for issue in report.issues
    )
