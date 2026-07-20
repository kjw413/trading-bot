from __future__ import annotations

import pytest

from tradingbot.data.credentials import MissingCredentialsError, krx_credentials, require_env


class TestRequireEnv:
    def test_raises_when_unset(self, monkeypatch):
        monkeypatch.delenv("SOME_VAR", raising=False)
        with pytest.raises(MissingCredentialsError):
            require_env("SOME_VAR", hint="set it please")

    def test_raises_when_blank(self, monkeypatch):
        monkeypatch.setenv("SOME_VAR", "   ")
        with pytest.raises(MissingCredentialsError):
            require_env("SOME_VAR", hint="set it please")

    def test_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("SOME_VAR", "actual-value")
        assert require_env("SOME_VAR", hint="set it please") == "actual-value"

    def test_message_contains_hint(self, monkeypatch):
        monkeypatch.delenv("SOME_VAR", raising=False)
        with pytest.raises(MissingCredentialsError, match="set it please"):
            require_env("SOME_VAR", hint="set it please")


class TestKrxCredentials:
    def test_raises_when_krx_id_missing(self, monkeypatch):
        monkeypatch.delenv("KRX_ID", raising=False)
        monkeypatch.setenv("KRX_PW", "pw")
        with pytest.raises(MissingCredentialsError, match="KRX_ID"):
            krx_credentials()

    def test_raises_when_krx_pw_missing(self, monkeypatch):
        monkeypatch.setenv("KRX_ID", "id")
        monkeypatch.delenv("KRX_PW", raising=False)
        with pytest.raises(MissingCredentialsError, match="KRX_PW"):
            krx_credentials()

    def test_returns_both_when_set(self, monkeypatch):
        monkeypatch.setenv("KRX_ID", "id")
        monkeypatch.setenv("KRX_PW", "pw")
        assert krx_credentials() == ("id", "pw")
