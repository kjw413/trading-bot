"""Event study: what happened around announcements, in a table.

This is the program's go/no-go point. If a two-dimensional bucket table over
several thousand events shows no effect, a gradient-boosted model over forty
features will find noise and dress it as signal. So the table comes first, and
it is descriptive on purpose — no fitting, nothing to overfit.

Everything is measured relative to the **reaction date**: the first session
that can price the filing, which `data/edgar.py` computes at collection time
because an 8-K accepted after the bell is tomorrow's news. Day 0 is that
session. The pre-event window ends the day before it, and the post-event
window starts the day after, so no return is ever counted twice.

Returns are market-adjusted — the stock's return minus the benchmark's over
the same days. A quarter where everything rose is not evidence about an
announcement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class EventWindow:
    """Trading-day offsets from the reaction date (day 0).

    The defaults follow the design review: sixty days of run-up is enough to
    see a name that has already been bid up, and twenty days after is the
    horizon the post-announcement drift literature works in.
    """

    pre_start: int = -60
    pre_end: int = -1
    post_start: int = 1
    post_end: int = 20

    def __post_init__(self) -> None:
        if self.pre_end >= 0:
            raise ValueError("pre_end must be before the reaction day")
        if self.post_start <= 0:
            raise ValueError("post_start must be after the reaction day")
        if self.pre_start > self.pre_end or self.post_start > self.post_end:
            raise ValueError("window bounds are inverted")


def abnormal_returns(closes: pd.Series, benchmark: pd.Series) -> pd.Series:
    """Daily return minus the benchmark's, aligned on shared dates.

    Market-adjusted rather than beta-adjusted. Estimating a beta per event
    needs an estimation window that itself has to avoid the event, and at this
    stage the extra machinery would buy precision the sample cannot support.
    """
    joined = pd.concat(
        [closes.rename("stock"), benchmark.rename("benchmark")], axis=1, join="inner"
    ).dropna()
    if len(joined) < 2:
        return pd.Series(dtype=float)
    returns = joined.pct_change().dropna()
    return (returns["stock"] - returns["benchmark"]).rename("ar")


def _window_sum(ar: pd.Series, day_zero: date, start: int, end: int) -> float:
    """Cumulative abnormal return over an offset window, or NaN if truncated.

    A window that runs off either end of the data is NaN rather than a partial
    sum. A five-day "twenty-day" return is not a smaller version of the same
    measurement, and averaging the two would quietly bias whichever bucket
    holds the most recent events.
    """
    if ar.empty:
        return float("nan")
    positions = ar.index.searchsorted(pd.Timestamp(day_zero))
    zero = int(positions)
    if zero >= len(ar):
        return float("nan")
    first, last = zero + start, zero + end
    if first < 0 or last >= len(ar):
        return float("nan")
    return float(ar.iloc[first : last + 1].sum())


def event_row(
    symbol: str,
    reaction_date: date,
    ar: pd.Series,
    window: EventWindow,
) -> dict[str, object]:
    """One event's abnormal returns before, on, and after the reaction day."""
    return {
        "symbol": symbol,
        "reaction_date": pd.Timestamp(reaction_date),
        "pre_car": _window_sum(ar, reaction_date, window.pre_start, window.pre_end),
        "reaction_ar": _window_sum(ar, reaction_date, 0, 0),
        "post_car": _window_sum(ar, reaction_date, window.post_start, window.post_end),
    }


def build_event_panel(
    events: pd.DataFrame,
    close_series: dict[str, pd.Series],
    benchmark: pd.Series,
    window: EventWindow | None = None,
) -> pd.DataFrame:
    """One row per event, with its windows measured.

    `events` needs `symbol` and `reaction_date`. Rows whose windows cannot be
    fully measured are kept with NaN so the caller can count what was dropped
    rather than wonder.
    """
    active = window or EventWindow()
    rows = []
    for symbol, group in events.groupby("symbol"):
        closes = close_series.get(str(symbol).upper())
        if closes is None or closes.empty:
            LOGGER.debug("No prices for %s; its events cannot be measured", symbol)
            continue
        ar = abnormal_returns(closes, benchmark)
        for reaction in pd.to_datetime(group["reaction_date"]):
            rows.append(event_row(str(symbol).upper(), reaction.date(), ar, active))
    if not rows:
        return pd.DataFrame(columns=["symbol", "reaction_date", "pre_car", "reaction_ar", "post_car"])
    return pd.DataFrame(rows).sort_values(["reaction_date", "symbol"]).reset_index(drop=True)


def assign_quantiles(values: pd.Series, n: int, by: pd.Series | None = None) -> pd.Series:
    """Rank into `n` buckets, 1 = lowest.

    `by` groups the ranking — passing the event year keeps a single strong
    period from filling a whole bucket, which is the difference between "high
    run-up names did badly" and "2022 happened".

    Groups too small to split return NaN rather than being forced into
    buckets; a three-event year cut into quintiles says nothing.
    """
    def rank(series: pd.Series) -> pd.Series:
        clean = series.dropna()
        if clean.nunique() < n:
            return pd.Series(np.nan, index=series.index)
        return pd.qcut(series, n, labels=range(1, n + 1), duplicates="drop").astype("float")

    if by is None:
        return rank(values)
    return values.groupby(by, group_keys=False).apply(rank)


def quantile_table(
    panel: pd.DataFrame,
    *,
    rows: str = "pre_car",
    cols: str = "reaction_ar",
    value: str = "post_car",
    n_quantiles: int = 5,
    group_by_year: bool = True,
) -> pd.DataFrame:
    """The table the whole program hinges on: mean outcome per bucket pair.

    Reported with counts alongside, because a cell mean over eleven events is
    not a finding and the table should make that obvious rather than leave it
    to be discovered.
    """
    frame = panel.dropna(subset=[rows, cols, value]).copy()
    if frame.empty:
        return pd.DataFrame()

    grouper = frame["reaction_date"].dt.year if group_by_year else None
    frame["_row_q"] = assign_quantiles(frame[rows], n_quantiles, grouper)
    frame["_col_q"] = assign_quantiles(frame[cols], n_quantiles, grouper)
    frame = frame.dropna(subset=["_row_q", "_col_q"])
    if frame.empty:
        return pd.DataFrame()

    stats = frame.groupby(["_row_q", "_col_q"])[value].agg(
        mean="mean", median="median", count="count", hit=lambda s: float((s > 0).mean())
    )
    return stats.reset_index().rename(columns={"_row_q": rows, "_col_q": cols})


def render_markdown(
    table: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    rows: str = "pre_car",
    cols: str = "reaction_ar",
    value: str = "post_car",
) -> str:
    """The table plus what it is allowed to conclude."""
    lines = [
        "# 이벤트 스터디",
        "",
        "발표 전 초과수익(세로)과 발표 당일 반응(가로)으로 나눈 뒤,",
        "이후 초과수익의 평균을 본다. 전부 시장 대비 초과수익이며, 반응일은",
        "그 공시를 처음 가격에 반영할 수 있는 세션이다.",
        "",
    ]
    if table.empty:
        lines += [
            "**측정할 수 있는 이벤트가 없습니다.**",
            "",
            f"패널 {len(panel)}행 중 창을 온전히 잴 수 있는 행이 없었습니다.",
            "가격 이력이 이벤트 앞뒤로 충분한지 확인하세요.",
            "",
        ]
        return "\n".join(lines)

    total = len(panel)
    measured = int(table["count"].sum())
    lines += [
        f"- 이벤트 {total:,}건 중 {measured:,}건이 측정됐습니다",
        f"- 칸당 최소 표본: {int(table['count'].min()):,}건",
        "",
        f"## {value} 평균 (%)",
        "",
    ]

    pivot = table.pivot(index=rows, columns=cols, values="mean") * 100
    counts = table.pivot(index=rows, columns=cols, values="count")
    header = " | ".join(f"{cols} {int(c)}" for c in pivot.columns)
    lines.append(f"| {rows} \\ {cols} | {header} |")
    lines.append("|---" * (len(pivot.columns) + 1) + "|")
    for index, row in pivot.iterrows():
        cells = " | ".join(
            f"{row[c]:+.2f} (n={int(counts.loc[index, c])})" for c in pivot.columns
        )
        lines.append(f"| {int(index)} | {cells} |")

    lines += [
        "",
        "## 읽는 법",
        "",
        f"세로 {rows}가 클수록 발표 전에 이미 오른 종목이고, 가로 {cols}가",
        "작을수록 발표에 시장이 약하게 반응한 종목이다. 오른쪽 위(많이 올랐고",
        "반응도 강함)와 왼쪽 아래(안 올랐고 반응도 약함)의 차이가 없다면,",
        "이 표에는 상호작용이 없는 것이다.",
        "",
        "**칸의 표본이 수백 건 미만이면 그 칸의 평균은 결론이 될 수 없다.**",
        "그리고 이 표는 기술통계일 뿐 검정이 아니다 — 방향이 보이더라도",
        "OOS와 비용을 통과해야 전략 후보가 된다.",
        "",
    ]
    return "\n".join(lines)
