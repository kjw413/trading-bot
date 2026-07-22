from __future__ import annotations

from datetime import date

import pytest

from tradingbot.data.universe import load_themes, members

THEMES_TOML = """
[themes.demo]
name = "데모"
market = "KR"
members = [
    { symbol = "005930", from = "2023-01-01" },
    { symbol = "000660", from = "2023-06-01", to = "2024-03-01" },
]
"""


@pytest.fixture
def themes(tmp_path):
    path = tmp_path / "themes.toml"
    path.write_text(THEMES_TOML, encoding="utf-8")
    return load_themes(path)


class TestLoadThemes:
    def test_parses_theme_metadata(self, themes):
        theme = themes["demo"]
        assert theme.key == "demo"
        assert theme.name == "데모"
        assert theme.market == "KR"
        assert len(theme.members) == 2

    def test_member_dates_are_parsed(self, themes):
        first, second = themes["demo"].members
        assert first.start == date(2023, 1, 1)
        assert first.end is None
        assert second.end == date(2024, 3, 1)

    def test_member_without_from_is_rejected(self, tmp_path):
        path = tmp_path / "bad.toml"
        path.write_text(
            '[themes.x]\nname="x"\nmarket="KR"\nmembers=[{symbol="005930"}]\n',
            encoding="utf-8",
        )
        # Undated members would silently backdate today's winners into the past.
        with pytest.raises(ValueError, match="from"):
            load_themes(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_themes(tmp_path / "nope.toml")

    def test_repo_themes_file_loads(self):
        themes = load_themes()
        assert themes
        for theme in themes.values():
            assert theme.members
            assert theme.market in {"KR", "US"}


class TestMembers:
    def test_only_symbols_already_included(self, themes):
        assert members(themes["demo"], date(2023, 3, 1)) == ["005930"]

    def test_includes_symbol_on_its_start_date(self, themes):
        assert set(members(themes["demo"], date(2023, 6, 1))) == {"005930", "000660"}

    def test_excludes_symbol_after_removal(self, themes):
        # Removed 2024-03-01; a backtest on 2024-06-01 must not see it.
        assert members(themes["demo"], date(2024, 6, 1)) == ["005930"]

    def test_includes_symbol_on_its_end_date(self, themes):
        assert set(members(themes["demo"], date(2024, 3, 1))) == {"005930", "000660"}

    def test_before_any_member_is_empty(self, themes):
        assert members(themes["demo"], date(2022, 1, 1)) == []

    def test_result_is_sorted_for_reproducibility(self, themes):
        assert members(themes["demo"], date(2023, 12, 1)) == sorted(
            members(themes["demo"], date(2023, 12, 1))
        )
