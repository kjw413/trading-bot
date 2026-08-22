"""Date-aware universes: which symbols are investable on a given date.

Two kinds answer that question, and callers should not have to know which one
they hold.

A **theme** is a hand-maintained list with inclusion and removal dates. It
works at five names, where a person can vouch for each one, and its `from`
dates are what stop a backtest from trading a company before it joined.

A **liquidity universe** (`data/universe_liquidity.py`) is recomputed from
price history at each rebalance. It is what five hundred names require: nobody
maintains that list by hand, and filling it from today's listings would
backdate today's winners into the past — exactly what the theme loader rejects
undated members to prevent.

Both satisfy `Universe`, so a strategy takes either.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from tradingbot.config import PROJECT_ROOT

THEMES_PATH = PROJECT_ROOT / "config" / "themes.toml"


@dataclass(frozen=True)
class ThemeMember:
    symbol: str
    start: date
    end: date | None = None

    def active_on(self, dt: date) -> bool:
        """Inclusive on both ends: a symbol counts on the day it joins and leaves."""
        if dt < self.start:
            return False
        return self.end is None or dt <= self.end


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    market: str
    members: tuple[ThemeMember, ...]


def _parse_member(theme_key: str, raw: dict) -> ThemeMember:
    symbol = str(raw.get("symbol", "")).strip()
    if not symbol:
        raise ValueError(f"Theme {theme_key} has a member without a symbol")
    if "from" not in raw:
        raise ValueError(
            f"Theme {theme_key} member {symbol} has no `from` date. Undated members "
            "backdate today's winners into the past (survivorship bias)."
        )
    end = raw.get("to")
    return ThemeMember(
        symbol=symbol.upper(),
        start=date.fromisoformat(str(raw["from"])),
        end=date.fromisoformat(str(end)) if end else None,
    )


def load_themes(path: str | Path | None = None) -> dict[str, Theme]:
    """Load every theme definition, keyed by theme id."""
    themes_path = Path(path) if path else THEMES_PATH
    if not themes_path.exists():
        raise FileNotFoundError(f"Themes file not found: {themes_path}")
    with themes_path.open("rb") as handle:
        raw = tomllib.load(handle)

    themes: dict[str, Theme] = {}
    for key, body in raw.get("themes", {}).items():
        themes[key] = Theme(
            key=key,
            name=str(body.get("name", key)),
            market=str(body.get("market", "KR")).upper(),
            members=tuple(_parse_member(key, member) for member in body.get("members", [])),
        )
    return themes


def members(theme: Theme, dt: date) -> list[str]:
    """Symbols that belonged to `theme` on `dt`, sorted for reproducibility."""
    return sorted(member.symbol for member in theme.members if member.active_on(dt))


def get_theme(key: str, path: str | Path | None = None) -> Theme:
    themes = load_themes(path)
    try:
        return themes[key]
    except KeyError as exc:
        available = ", ".join(sorted(themes))
        raise ValueError(f"Unknown theme: {key}. Available: {available}") from exc


class Universe(Protocol):
    """What a strategy needs from a universe: who is investable on a date.

    Deliberately narrow. A strategy that could ask "what kind of universe are
    you" would grow a branch per kind, and the point of this interface is that
    a liquidity screen and a hand-written theme are interchangeable.
    """

    market: str

    def members(self, dt: date) -> list[str]:
        """Symbols investable on `dt`, sorted. Only data knowable at `dt`."""
        ...


@dataclass(frozen=True)
class ThemeUniverse:
    """A static theme behind the `Universe` interface."""

    theme: Theme

    @property
    def market(self) -> str:
        return self.theme.market

    def members(self, dt: date) -> list[str]:
        return members(self.theme, dt)
