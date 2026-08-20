"""Telegram Bot API delivery.

Chosen over printing to the console because the console dies with the PC.
The requirement that survived the move to local execution is that the
briefing outlive the session it was made in, and a message on the phone does
exactly that.

Failure is never swallowed. A notifier that reports success while the phone
stays silent is worse than no notifier at all: the run looks fine and the
reader simply never learns anything went wrong.
"""

from __future__ import annotations

import time
from typing import Callable

import requests

from tradingbot.data.credentials import require_env
from tradingbot.report.briefing import split_for_telegram
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

API_BASE = "https://api.telegram.org"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2, 4)

Transport = Callable[[str, dict], dict]


class NotifyError(RuntimeError):
    """Delivery failed after every retry."""


def _requests_transport(url: str, payload: dict) -> dict:
    response = requests.post(url, json=payload, timeout=20)
    response.raise_for_status()
    return response.json()


class TelegramNotifier:
    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        transport: Transport = _requests_transport,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._transport = transport
        self._sleep = sleeper

    @property
    def _url(self) -> str:
        return f"{API_BASE}/bot{self._token}/sendMessage"

    def send(self, text: str) -> None:
        for part in split_for_telegram(text):
            self._send_one(part)

    def _send_one(self, text: str) -> None:
        payload = {"chat_id": self._chat_id, "text": text}
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                result = self._transport(self._url, payload)
            except Exception as exc:  # transport-level failure
                last = exc
            else:
                if result.get("ok"):
                    return
                last = NotifyError(
                    f"Telegram rejected the message: {result.get('description')}"
                )
            LOGGER.warning(
                "Telegram send attempt %s/%s failed: %s", attempt + 1, MAX_ATTEMPTS, last
            )
            if attempt < MAX_ATTEMPTS - 1:
                self._sleep(BACKOFF_SECONDS[attempt])
        # The final message carries the last failure's text, not just a chain:
        # a caller printing str(exc) has to see "chat not found", or the user
        # is told delivery failed with no way to learn why.
        raise NotifyError(
            f"Telegram delivery failed after {MAX_ATTEMPTS} attempts: {last}"
        ) from last


def build_notifier() -> TelegramNotifier:
    hint = (
        "@BotFather에서 봇을 만들어 토큰을 받고, 그 봇과 대화를 시작한 뒤 "
        "chat id를 확인하세요. 그 두 값을 저장소 폴더의 .env 파일에 "
        "TELEGRAM_BOT_TOKEN=..., TELEGRAM_CHAT_ID=... 형태로 적으면 됩니다 "
        "(.env.template 참고). 더블클릭 실행은 새 프로세스라 PowerShell 창에서 "
        "$env: 로 넣은 값은 보지 못합니다. 저장소에 커밋하지 마세요."
    )
    return TelegramNotifier(
        token=require_env("TELEGRAM_BOT_TOKEN", hint=hint),
        chat_id=require_env("TELEGRAM_CHAT_ID", hint=hint),
    )
