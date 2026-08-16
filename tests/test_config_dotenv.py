from __future__ import annotations

import pytest

from tradingbot.config import load_dotenv, parse_dotenv


class TestParseDotenv:
    def test_reads_key_value_pairs(self):
        assert parse_dotenv("A=1\nB=two\n") == {"A": "1", "B": "two"}

    def test_ignores_comments_and_blank_lines(self):
        text = "# a comment\n\nA=1\n   \n# another\nB=2\n"
        assert parse_dotenv(text) == {"A": "1", "B": "2"}

    def test_strips_whitespace_around_the_key_and_value(self):
        assert parse_dotenv("  A  =  spaced value  \n") == {"A": "spaced value"}

    def test_an_export_prefix_is_tolerated(self):
        # Copy-pasted from a shell snippet.
        assert parse_dotenv("export A=1\n") == {"A": "1"}

    def test_quotes_are_stripped(self):
        assert parse_dotenv("A=\"quoted\"\nB='single'\n") == {"A": "quoted", "B": "single"}

    def test_a_value_may_contain_equals_signs(self):
        # Passwords and tokens do.
        assert parse_dotenv("A=abc=def=ghi\n") == {"A": "abc=def=ghi"}

    def test_a_value_may_contain_a_hash(self):
        # Only a line that *starts* with # is a comment; splitting on an inline
        # # would truncate any password containing one.
        assert parse_dotenv("A=pa#ssword\n") == {"A": "pa#ssword"}

    def test_an_email_value_survives_intact(self):
        assert parse_dotenv("SEC_USER_AGENT=Hong Gildong hong@example.com\n") == {
            "SEC_USER_AGENT": "Hong Gildong hong@example.com"
        }

    def test_a_line_without_an_equals_sign_is_skipped(self):
        assert parse_dotenv("nonsense\nA=1\n") == {"A": "1"}

    def test_an_empty_value_is_dropped(self):
        # `.env.template` ships keys with empty values; loading them would
        # shadow a real variable already set in the environment with "".
        assert parse_dotenv("A=\nB=2\n") == {"B": "2"}

    def test_empty_text(self):
        assert parse_dotenv("") == {}


class TestLoadDotenv:
    def test_loads_values_into_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)
        path = tmp_path / ".env"
        path.write_text("SEC_USER_AGENT=Hong Gildong hong@example.com\n", encoding="utf-8")
        assert load_dotenv(path) == ["SEC_USER_AGENT"]
        import os

        assert os.environ["SEC_USER_AGENT"] == "Hong Gildong hong@example.com"

    def test_an_already_set_variable_wins(self, tmp_path, monkeypatch):
        # An explicit `$env:X = ...` in the shell must beat the file, or there
        # is no way to override it for one run.
        monkeypatch.setenv("SEC_USER_AGENT", "from the shell")
        path = tmp_path / ".env"
        path.write_text("SEC_USER_AGENT=from the file\n", encoding="utf-8")
        assert load_dotenv(path) == []
        import os

        assert os.environ["SEC_USER_AGENT"] == "from the shell"

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        assert load_dotenv(tmp_path / "nope.env") == []

    def test_an_unreadable_file_is_not_fatal(self, tmp_path, monkeypatch):
        # A malformed or locked .env must not stop the whole CLI.
        path = tmp_path / ".env"
        path.write_text("A=1\n", encoding="utf-8")

        def boom(*args, **kwargs):
            raise OSError("locked")

        monkeypatch.setattr("pathlib.Path.read_text", boom)
        assert load_dotenv(path) == []

    def test_returns_the_names_it_set_not_the_values(self, tmp_path, monkeypatch):
        # The CLI logs this. Values are credentials and must never be logged.
        monkeypatch.delenv("KRX_ID", raising=False)
        monkeypatch.delenv("KRX_PW", raising=False)
        path = tmp_path / ".env"
        path.write_text("KRX_ID=someone\nKRX_PW=secret\n", encoding="utf-8")
        loaded = load_dotenv(path)
        assert sorted(loaded) == ["KRX_ID", "KRX_PW"]
        assert "secret" not in " ".join(loaded)


class TestCliLoadsDotenv:
    def test_main_reads_the_project_env_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)
        monkeypatch.setattr("tradingbot.config.PROJECT_ROOT", tmp_path)
        (tmp_path / ".env").write_text("SEC_USER_AGENT=from the file\n", encoding="utf-8")

        from tradingbot.cli import main

        main(["strategies"])
        import os

        assert os.environ["SEC_USER_AGENT"] == "from the file"
