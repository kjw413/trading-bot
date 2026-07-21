from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from tradingbot.engine.calendar import get_calendar

PANEL_KEY_COLUMNS = ["date", "symbol"]
PANEL_META_COLUMNS = ["source", "available_at", "ingested_at", "data_version"]


def _values_equal(left: Any, right: Any) -> bool:
    """NaN-tolerant equality: two missing values are the same value."""
    if pd.isna(left) and pd.isna(right):
        return True
    return bool(left == right)


def next_trading_day_availability(dates: pd.Series, market: str) -> pd.Series:
    """First date on which data observed on `dates` may be used.

    Daily data for trading day T is only known after T's close, so the
    earliest a backtest may act on it is the next trading day."""
    if dates.empty:
        return pd.Series([], dtype="datetime64[ns]")
    calendar = get_calendar(market)
    unique = pd.to_datetime(dates).dt.normalize().drop_duplicates()
    mapping = {value: pd.Timestamp(calendar.next_trading_day(value.date())) for value in unique}
    return pd.to_datetime(dates).dt.normalize().map(mapping)


def attach_metadata(
    frame: pd.DataFrame,
    *,
    source: str,
    available_at: pd.Series | str | date,
    data_version: str,
    ingested_at: datetime | None = None,
) -> pd.DataFrame:
    """Add the point-in-time metadata columns every panel record must carry."""
    missing = [column for column in PANEL_KEY_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Panel frame is missing key columns: {missing}")

    tagged = frame.copy()
    tagged["date"] = pd.to_datetime(tagged["date"]).dt.normalize()
    tagged["symbol"] = tagged["symbol"].astype(str).str.upper()
    tagged["source"] = source
    if isinstance(available_at, pd.Series):
        tagged["available_at"] = pd.to_datetime(available_at.to_numpy()).normalize()
    else:
        tagged["available_at"] = pd.Timestamp(available_at).normalize()
    tagged["ingested_at"] = pd.Timestamp(ingested_at or datetime.now(timezone.utc))
    tagged["data_version"] = str(data_version)
    return tagged


class PanelStore:
    """Year-partitioned Parquet panel with a point-in-time read barrier.

    Layout: `root/dataset/MARKET/{year}.parquet`, one row per (date, symbol).
    Year partitioning keeps cross-sectional reads to one file per year, which
    is how the research layer consumes these datasets."""

    def __init__(self, root: str | Path, dataset: str, market: str) -> None:
        self.root = Path(root)
        self.dataset = dataset
        self.market = market.upper()

    @property
    def directory(self) -> Path:
        return self.root / self.dataset / self.market

    def path(self, year: int) -> Path:
        return self.directory / f"{year}.parquet"

    def years(self) -> list[int]:
        if not self.directory.exists():
            return []
        return sorted(int(p.stem) for p in self.directory.glob("*.parquet") if p.stem.isdigit())

    def append(self, frame: pd.DataFrame) -> int:
        """Merge rows into their year partitions; (date, symbol) keeps the newest.

        Returns the number of rows actually added or changed, not the size of
        the incoming frame: a re-run over already-collected, unchanged data
        must report 0, not the row count it happened to re-fetch, or an
        operator watching the log would think collection is still making
        progress when it is not. A row whose (date, symbol) key already
        exists but whose value changed (e.g. a DART restatement correcting a
        prior figure) counts as changed, not as 0 -- silently reporting 0
        there would hide real data being overwritten. The comparison ignores
        `ingested_at`, since that column is a fresh timestamp on every run
        and would otherwise make every re-collected row look changed."""
        if frame.empty:
            return 0
        missing = [c for c in PANEL_KEY_COLUMNS + PANEL_META_COLUMNS if c not in frame.columns]
        if missing:
            raise ValueError(f"Panel frame is missing required columns: {missing}")

        incoming = frame.copy()
        incoming["date"] = pd.to_datetime(incoming["date"]).dt.normalize()
        incoming["symbol"] = incoming["symbol"].astype(str).str.upper()

        added = 0
        for year, chunk in incoming.groupby(incoming["date"].dt.year):
            path = self.path(int(year))
            # Two rows in the same call sharing a key collapse to one stored
            # row (keep last); count and concat against that deduplicated
            # frame so an intra-chunk duplicate isn't counted twice.
            chunk = chunk.drop_duplicates(subset=PANEL_KEY_COLUMNS, keep="last")
            comparable = [c for c in chunk.columns if c != "ingested_at"]
            if path.exists():
                existing = pd.read_parquet(path)
                combined = pd.concat([existing, chunk], ignore_index=True)
                before_keys = existing.set_index(PANEL_KEY_COLUMNS)
                unchanged = 0
                for _, row in chunk.iterrows():
                    key = (row["date"], row["symbol"])
                    if key not in before_keys.index:
                        continue
                    prior = before_keys.loc[key]
                    if isinstance(prior, pd.DataFrame):  # duplicate keys in a corrupt file
                        prior = prior.iloc[-1]
                    if all(
                        _values_equal(prior.get(c), row[c])
                        for c in comparable
                        if c in before_keys.columns
                    ):
                        unchanged += 1
                added += len(chunk) - unchanged
            else:
                combined = chunk
                added += len(chunk)
            combined = combined.drop_duplicates(subset=PANEL_KEY_COLUMNS, keep="last")
            combined = combined.sort_values(PANEL_KEY_COLUMNS).reset_index(drop=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            combined.to_parquet(path)
        return added

    def read(
        self,
        *,
        as_of: date | None = None,
        start: date | None = None,
        end: date | None = None,
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """Rows visible as of `as_of` — the guard against look-ahead bias.

        Without `as_of` the full panel is returned; callers on the research or
        strategy path must always pass it."""
        years = self.years()
        if not years:
            return pd.DataFrame()
        if start is not None:
            years = [y for y in years if y >= start.year]
        if end is not None:
            years = [y for y in years if y <= end.year]
        frames = [pd.read_parquet(self.path(year)) for year in years]
        if not frames:
            return pd.DataFrame()

        panel = pd.concat(frames, ignore_index=True)
        if as_of is not None:
            panel = panel[panel["available_at"] <= pd.Timestamp(as_of)]
        if start is not None:
            panel = panel[panel["date"] >= pd.Timestamp(start)]
        if end is not None:
            panel = panel[panel["date"] <= pd.Timestamp(end)]
        if symbols is not None:
            wanted = {str(symbol).upper() for symbol in symbols}
            panel = panel[panel["symbol"].isin(wanted)]
        return panel.sort_values(PANEL_KEY_COLUMNS).reset_index(drop=True)

    def last_date(self, symbol: str | None = None) -> date | None:
        """Newest observation date, for incremental collection."""
        panel = self.read(symbols=[symbol] if symbol else None)
        if panel.empty:
            return None
        return panel["date"].max().date()
