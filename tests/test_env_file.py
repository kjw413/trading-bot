from __future__ import annotations

from tradingbot.env_file import load_env_file


class TestLoadEnvFile:
    def test_sets_names_from_the_file(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("TOSS_CLIENT_ID=abc\nTOSS_CLIENT_SECRET=def\n", encoding="utf-8")
        environ: dict[str, str] = {}
        assert load_env_file(path, environ=environ) == ["TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"]
        assert environ == {"TOSS_CLIENT_ID": "abc", "TOSS_CLIENT_SECRET": "def"}

    def test_a_real_environment_variable_wins(self, tmp_path):
        """The file is a convenience, never an override.

        Someone debugging with a one-off `$env:X` in their shell must not be
        silently overruled by a file they set up weeks earlier."""
        path = tmp_path / ".env"
        path.write_text("TOSS_CLIENT_ID=from-file\n", encoding="utf-8")
        environ = {"TOSS_CLIENT_ID": "from-shell"}
        assert load_env_file(path, environ=environ) == []
        assert environ["TOSS_CLIENT_ID"] == "from-shell"

    def test_spaces_around_the_equals_sign_are_stripped(self, tmp_path):
        """`KEY = value` is what people type. Without stripping, the name
        carries a trailing space and no lookup ever finds it."""
        path = tmp_path / ".env"
        path.write_text("TOSS_CLIENT_ID = abc \n", encoding="utf-8")
        environ: dict[str, str] = {}
        load_env_file(path, environ=environ)
        assert environ == {"TOSS_CLIENT_ID": "abc"}

    def test_skips_comments_and_blank_lines(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("# a comment\n\n   \nKEY=value\n  # indented comment\n", encoding="utf-8")
        environ: dict[str, str] = {}
        load_env_file(path, environ=environ)
        assert environ == {"KEY": "value"}

    def test_strips_surrounding_quotes(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("A=\"quoted\"\nB='single'\n", encoding="utf-8")
        environ: dict[str, str] = {}
        load_env_file(path, environ=environ)
        assert environ == {"A": "quoted", "B": "single"}

    def test_keeps_a_lone_quote_inside_a_value(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("A=it's\n", encoding="utf-8")
        environ: dict[str, str] = {}
        load_env_file(path, environ=environ)
        assert environ == {"A": "it's"}

    def test_accepts_an_export_prefix(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("export KEY=value\n", encoding="utf-8")
        environ: dict[str, str] = {}
        load_env_file(path, environ=environ)
        assert environ == {"KEY": "value"}

    def test_splits_on_the_first_equals_only(self, tmp_path):
        """Telegram tokens and base64 secrets contain '='."""
        path = tmp_path / ".env"
        path.write_text("KEY=a=b=c\n", encoding="utf-8")
        environ: dict[str, str] = {}
        load_env_file(path, environ=environ)
        assert environ == {"KEY": "a=b=c"}

    def test_ignores_a_line_without_an_equals_sign(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("this is not a setting\nKEY=value\n", encoding="utf-8")
        environ: dict[str, str] = {}
        load_env_file(path, environ=environ)
        assert environ == {"KEY": "value"}

    def test_skips_an_empty_value(self, tmp_path):
        """.env.template ships every name with a blank value. Copying it must
        not plant empty names that shadow nothing and read as 'set'."""
        path = tmp_path / ".env"
        path.write_text("KRX_ID=\nKEY=value\n", encoding="utf-8")
        environ: dict[str, str] = {}
        load_env_file(path, environ=environ)
        assert environ == {"KEY": "value"}

    def test_tolerates_a_utf8_bom(self, tmp_path):
        """Notepad writes one by default, and it would corrupt the first name."""
        path = tmp_path / ".env"
        path.write_bytes("KEY=value\n".encode("utf-8-sig"))
        environ: dict[str, str] = {}
        load_env_file(path, environ=environ)
        assert environ == {"KEY": "value"}

    def test_tolerates_crlf(self, tmp_path):
        path = tmp_path / ".env"
        path.write_bytes(b"KEY=value\r\nOTHER=second\r\n")
        environ: dict[str, str] = {}
        load_env_file(path, environ=environ)
        assert environ == {"KEY": "value", "OTHER": "second"}

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        environ: dict[str, str] = {}
        assert load_env_file(tmp_path / "absent", environ=environ) == []
        assert environ == {}

    def test_an_unreadable_file_is_not_an_error(self, tmp_path):
        """A directory named .env, a permission problem -- none of it should
        stop a run whose credentials may already be in the environment."""
        path = tmp_path / ".env"
        path.mkdir()
        environ: dict[str, str] = {}
        assert load_env_file(path, environ=environ) == []


class TestDefaultSearch:
    def test_prefers_the_working_directory(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("KEY=from-cwd\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        environ: dict[str, str] = {}
        load_env_file(environ=environ)
        assert environ == {"KEY": "from-cwd"}

    def test_falls_back_to_the_project_root(self, tmp_path, monkeypatch):
        """The launcher cds to the repo, but a hand-typed command need not.
        Reading the project's own .env keeps that case from failing blind."""
        from tradingbot import env_file

        root = tmp_path / "root"
        root.mkdir()
        (root / ".env").write_text("KEY=from-root\n", encoding="utf-8")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.setattr(env_file, "PROJECT_ROOT", root)

        environ: dict[str, str] = {}
        load_env_file(environ=environ)
        assert environ == {"KEY": "from-root"}

    def test_the_working_directory_shadows_the_project_root(self, tmp_path, monkeypatch):
        from tradingbot import env_file

        root = tmp_path / "root"
        root.mkdir()
        (root / ".env").write_text("KEY=from-root\n", encoding="utf-8")
        here = tmp_path / "here"
        here.mkdir()
        (here / ".env").write_text("KEY=from-cwd\n", encoding="utf-8")
        monkeypatch.chdir(here)
        monkeypatch.setattr(env_file, "PROJECT_ROOT", root)

        environ: dict[str, str] = {}
        load_env_file(environ=environ)
        assert environ == {"KEY": "from-cwd"}


class TestCliLoadsTheFile:
    def test_main_reads_the_env_file_in_the_working_directory(self, tmp_path, monkeypatch):
        """The regression this module exists for: a double-clicked launcher is
        a fresh process, so a credential typed into a shell never reaches it."""
        from tradingbot.cli import main

        import os

        (tmp_path / ".env").write_text("TRADINGBOT_ENV_PROBE=loaded\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        # setenv then delenv so monkeypatch records an undo for a name that
        # starts absent; a bare delenv(raising=False) records nothing and the
        # value main() sets would leak into the rest of the session.
        monkeypatch.setenv("TRADINGBOT_ENV_PROBE", "placeholder")
        monkeypatch.delenv("TRADINGBOT_ENV_PROBE")

        main([])  # no subcommand: prints help, but startup has already run

        assert os.environ["TRADINGBOT_ENV_PROBE"] == "loaded"


class TestCredentialHintsPointAtTheFile:
    """The hint is the only thing a stuck person reads.

    This bug happened because both hints said "환경변수로 등록하세요" and the
    user did exactly that, in a PowerShell window the launcher cannot see.
    Naming `.env` is what makes the advice work for a double-clicked `.bat`.
    """

    def test_toss_hint_names_the_env_file(self):
        from tradingbot.account.toss import _CREDENTIAL_HINT

        assert ".env" in _CREDENTIAL_HINT

    def test_telegram_hint_names_the_env_file(self, monkeypatch):
        import pytest

        from tradingbot.data.credentials import MissingCredentialsError
        from tradingbot.notify.telegram import build_notifier

        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with pytest.raises(MissingCredentialsError, match=r"\.env"):
            build_notifier()
