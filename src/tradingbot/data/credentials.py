from __future__ import annotations

import os


class MissingCredentialsError(RuntimeError):
    """A required credential is absent.

    Distinct from a transient failure: retrying cannot fix it, and the
    pipeline reports the source as skipped rather than failed."""


def require_env(name: str, *, hint: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingCredentialsError(f"{name} is not set. {hint}")
    return value


def krx_credentials() -> tuple[str, str]:
    """KRX member login, required by pykrx for investor-flow and valuation data.

    Price (OHLCV) data needs no login; only the member-only endpoints do."""
    hint = (
        "Register a free member account at https://data.krx.co.kr and set KRX_ID and KRX_PW "
        "as environment variables; never commit them to the repository."
    )
    return require_env("KRX_ID", hint=hint), require_env("KRX_PW", hint=hint)
