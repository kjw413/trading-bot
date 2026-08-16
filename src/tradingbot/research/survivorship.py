"""Measure how much of the past the candidate pool is missing.

The pool comes from today's listing directory, so a company that delisted in
2018 is not in it, and no amount of point-in-time discipline downstream can
put it back. Every result computed on this universe is therefore biased toward
companies that survived, and the honest thing is not to claim otherwise but to
say how large the effect is, year by year.

SEC's full-index is the yardstick. It lists every filing accepted in a
quarter, by CIK, whether or not the filer still exists. Counting the companies
that filed an annual report in a year gives the population that was actually
reporting then; intersecting with the pool gives the share still visible now.

A low ratio does not invalidate a result. It says how much to discount it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

FULL_INDEX_URL = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/form.idx"

# The annual report, and the foreign-issuer equivalent. A company filing
# either was reporting that year.
ANNUAL_FORMS = ("10-K", "20-F")


@dataclass(frozen=True)
class SurvivalRate:
    """One year's comparison between who filed and who is still listed."""

    year: int
    filers: int
    still_listed: int

    @property
    def rate(self) -> float:
        """Share of that year's filers still in the pool. NaN when none filed."""
        if self.filers == 0:
            return float("nan")
        return self.still_listed / self.filers


def parse_form_index(text: str, forms: Sequence[str] = ANNUAL_FORMS) -> set[int]:
    """CIKs that filed one of `forms`, from a full-index `form.idx`.

    The file is fixed-width with a header block, sorted by form type:

        Form Type   Company Name    CIK   Date Filed  File Name
        10-K        ACME CORP       12345 2024-02-01  edgar/data/...

    Parsed by splitting on runs of whitespace from the right, because company
    names contain single spaces and the columns are not reliably aligned across
    years. The CIK is the third field from the end.
    """
    wanted = {form.upper() for form in forms}
    ciks: set[int] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("-"):
            continue
        parts = stripped.split()
        if len(parts) < 4:
            continue
        if parts[0].upper() not in wanted:
            continue
        # ... Company Name ... CIK  Date  FileName
        try:
            cik = int(parts[-3])
        except ValueError:
            continue
        ciks.add(cik)
    return ciks


def survival_by_year(
    years: Iterable[int],
    pool_ciks: set[int],
    *,
    fetcher: Callable[[int, int], str],
    forms: Sequence[str] = ANNUAL_FORMS,
) -> list[SurvivalRate]:
    """Compare each year's annual filers against the candidate pool.

    A quarter that cannot be fetched is logged and skipped rather than counted
    as zero filers — an unreachable index would otherwise look like a year in
    which the survival rate was perfect.
    """
    results: list[SurvivalRate] = []
    for year in years:
        filers: set[int] = set()
        for quarter in (1, 2, 3, 4):
            try:
                filers |= parse_form_index(fetcher(year, quarter), forms)
            except Exception:
                LOGGER.exception(
                    "full-index %s QTR%s unavailable; that quarter is not counted",
                    year,
                    quarter,
                )
        results.append(
            SurvivalRate(year=year, filers=len(filers), still_listed=len(filers & pool_ciks))
        )
    return results


def render_markdown(rates: Sequence[SurvivalRate]) -> str:
    """The table that goes in the judgement, with the caveat attached."""
    lines = [
        "## 생존자 편향 크기",
        "",
        "그 해 연차보고서를 제출한 기업 중 지금도 후보 풀에 남아 있는 비율이다.",
        "낮은 구간의 결과는 그만큼 할인해서 읽어야 한다 — 사라진 기업이 빠진 채로",
        "계산됐기 때문이다. 이 표는 편향을 없애지 않는다. 크기를 밝힐 뿐이다.",
        "",
        "| 연도 | 그때 보고한 기업 | 지금도 있는 기업 | 생존율 |",
        "|---|---:|---:|---:|",
    ]
    for entry in rates:
        rate = "측정 불가" if entry.filers == 0 else f"{entry.rate:.1%}"
        lines.append(f"| {entry.year} | {entry.filers:,} | {entry.still_listed:,} | {rate} |")
    lines.append("")
    return "\n".join(lines)


def requests_fetcher(user_agent: str | None = None, timeout: float = 60.0):
    """Real network fetch of one quarter's form index."""
    from tradingbot.data.edgar import user_agent_from_env

    agent = user_agent or user_agent_from_env()

    def fetch(year: int, quarter: int) -> str:
        import requests

        response = requests.get(
            FULL_INDEX_URL.format(year=year, quarter=quarter),
            headers={"User-Agent": agent},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.text

    return fetch
