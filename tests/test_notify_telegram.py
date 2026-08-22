from __future__ import annotations

import pytest

from tradingbot.notify.telegram import NotifyError, TelegramNotifier
from tradingbot.notify.telegram import _http_error_from_response


class FakeTransport:
    def __init__(self, failures=0, exc=None):
        self.calls: list[dict] = []
        self.failures = failures
        self.exc = exc or RuntimeError("telegram 500")

    def __call__(self, url: str, payload: dict) -> dict:
        self.calls.append({"url": url, "payload": payload})
        if len(self.calls) <= self.failures:
            raise self.exc
        return {"ok": True}


def notifier(transport, **kwargs):
    return TelegramNotifier(
        token="T", chat_id="C", transport=transport, sleeper=lambda _s: None, **kwargs
    )


class TestSend:
    def test_posts_the_text_to_the_chat(self):
        transport = FakeTransport()
        notifier(transport).send("안녕하세요")
        assert len(transport.calls) == 1
        assert transport.calls[0]["payload"]["chat_id"] == "C"
        assert transport.calls[0]["payload"]["text"] == "안녕하세요"

    def test_the_token_is_in_the_url_not_the_payload(self):
        transport = FakeTransport()
        notifier(transport).send("안녕")
        assert "T" in transport.calls[0]["url"]
        assert "T" not in str(transport.calls[0]["payload"])

    def test_a_long_briefing_is_sent_as_several_messages(self):
        transport = FakeTransport()
        notifier(transport).send("\n\n".join(["가" * 1000] * 10))
        assert len(transport.calls) > 1

    def test_parts_are_sent_in_order(self):
        transport = FakeTransport()
        notifier(transport).send("첫째" + "\n\n" + "둘" * 3000 + "\n\n" + "마지막")
        texts = [call["payload"]["text"] for call in transport.calls]
        assert texts[0].startswith("첫째")
        assert texts[-1].endswith("마지막")


class TestRetry:
    def test_a_transient_failure_is_retried(self):
        transport = FakeTransport(failures=2)
        notifier(transport).send("안녕")
        assert len(transport.calls) == 3

    def test_giving_up_raises_rather_than_returning_quietly(self):
        # A notifier that swallows its own failure is worse than none: the run
        # would report success while the phone stayed silent.
        transport = FakeTransport(failures=99)
        with pytest.raises(NotifyError):
            notifier(transport).send("안녕")

    def test_it_stops_after_three_attempts(self):
        transport = FakeTransport(failures=99)
        with pytest.raises(NotifyError):
            notifier(transport).send("안녕")
        assert len(transport.calls) == 3

    def test_backoff_waits_between_attempts(self):
        waits: list[float] = []
        transport = FakeTransport(failures=2)
        TelegramNotifier(
            token="T", chat_id="C", transport=transport, sleeper=waits.append
        ).send("안녕")
        assert waits == [2, 4]

    def test_a_response_saying_not_ok_is_a_failure(self):
        class NotOk:
            def __init__(self):
                self.calls = 0

            def __call__(self, url, payload):
                self.calls += 1
                return {"ok": False, "description": "chat not found"}

        transport = NotOk()
        with pytest.raises(NotifyError) as excinfo:
            notifier(transport).send("안녕")
        assert "chat not found" in str(excinfo.value)


FAKE_TOKEN = "123456789:AAFakeFakeFakeFakeFakeFakeFakeFakeFake"


class FakeHttpResponse:
    def __init__(
        self,
        status_code: int,
        body=None,
        json_error: Exception | None = None,
    ):
        self.status_code = status_code
        self._body = body
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._body


def secure_notifier(transport):
    return TelegramNotifier(
        token=FAKE_TOKEN,
        chat_id="C",
        transport=transport,
        sleeper=lambda _s: None,
    )


class TestErrorHygiene:
    def test_a_400_response_keeps_the_chat_not_found_description_and_hides_the_token(
        self,
    ):
        response = FakeHttpResponse(
            400,
            {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: chat not found",
            },
        )

        def transport(_url, _payload):
            raise _http_error_from_response(response)

        with pytest.raises(NotifyError) as excinfo:
            secure_notifier(transport).send("안녕")

        message = str(excinfo.value)
        assert "HTTP 400" in message
        assert "error_code 400" in message
        assert "Bad Request: chat not found" in message
        assert FAKE_TOKEN not in message

    def test_a_connection_error_does_not_expose_the_token_from_its_url(self):
        # requests includes the credential-bearing URL in real connection failures.
        def transport(url, _payload):
            raise ConnectionError(f"connection failed for {url}")

        with pytest.raises(NotifyError) as excinfo:
            secure_notifier(transport).send("안녕")

        message = str(excinfo.value)
        assert FAKE_TOKEN not in message
        assert "<redacted-bot-token>" in message

    def test_a_non_json_error_response_still_names_the_http_status(self):
        # Telegram's edge can answer with an empty or HTML error body.
        response = FakeHttpResponse(502, json_error=ValueError("empty body"))

        def transport(_url, _payload):
            raise _http_error_from_response(response)

        with pytest.raises(NotifyError) as excinfo:
            secure_notifier(transport).send("안녕")

        message = str(excinfo.value)
        assert "HTTP 502" in message
        assert "response body was empty or not valid JSON" in message
