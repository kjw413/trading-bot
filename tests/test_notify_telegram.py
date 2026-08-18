from __future__ import annotations

import pytest

from tradingbot.notify.telegram import NotifyError, TelegramNotifier


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
