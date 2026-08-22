from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import pytest

from tradingbot.account import toss
from tradingbot.account.toss import (
    KST,
    Token,
    TokenStore,
    TossAccountReader,
    TossAuthError,
    TossCredentials,
    TossError,
    TossIPNotAllowedError,
    TossRateLimitError,
    build_reader,
    to_snapshot,
)
from tradingbot.data.credentials import MissingCredentialsError
from tradingbot.report.briefing import render_briefing


DATA = Path(__file__).parent / "data"
FETCHED = datetime(2026, 8, 19, 20, 0, tzinfo=KST)


def load(name: str) -> Any:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


BALANCE = load("toss_balance_sample.json")
KRW_ONLY = load("toss_balance_krw_only_sample.json")
EMPTY = load("toss_balance_empty_sample.json")
ACCOUNTS = load("toss_accounts_sample.json")
BUYING_POWER = load("toss_buying_power_sample.json")
EXCHANGE_RATE = load("toss_exchange_rate_sample.json")


@dataclass
class FakeResponse:
    status_code: int
    payload: Any = None
    headers: Mapping[str, str] = field(default_factory=dict)
    text: str = ""

    def json(self) -> Any:
        return self.payload


class QueueTransport:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        assert self.responses, f"unexpected request: {method} {url}"
        return self.responses.pop(0)


def ok(payload: Any) -> FakeResponse:
    return FakeResponse(200, payload)


def token_response(token: str = "secret-token", expires_in: int = 86400) -> FakeResponse:
    return ok({"access_token": token, "token_type": "Bearer", "expires_in": expires_in})


def success_responses(*, with_token: bool = True, balance: dict | None = None):
    rows = [ok(ACCOUNTS), ok(balance or BALANCE), ok(BUYING_POWER["KRW"]), ok(BUYING_POWER["USD"]), ok(EXCHANGE_RATE)]
    return ([token_response()] if with_token else []) + rows


def reader(tmp_path: Path, responses: list[FakeResponse], **kwargs: Any):
    transport = QueueTransport(responses)
    instance = TossAccountReader(
        TossCredentials("client", "secret", "123-45678901"),
        transport=transport,
        token_store=TokenStore(tmp_path / "token.json"),
        sleeper=kwargs.pop("sleeper", lambda _: None),
        now=kwargs.pop("now", lambda: FETCHED),
        public_ip=kwargs.pop("public_ip", lambda: "203.0.113.7"),
        **kwargs,
    )
    return instance, transport


class TestToSnapshot:
    def test_maps_every_holding_in_the_sample(self):
        snap = to_snapshot(BALANCE, fetched_at=FETCHED, cash={"KRW": "5000000", "USD": "3500.5"}, usdkrw=1375)
        assert [h.symbol for h in snap.holdings] == ["005930", "AAPL"]

    def test_quantity_display_is_the_brokers_own_string(self):
        snap = to_snapshot(BALANCE, fetched_at=FETCHED, cash={}, usdkrw=1375)
        assert [h.qty_display for h in snap.holdings] == ["100", "10"]

    def test_a_fractional_quantity_keeps_every_digit_on_screen(self):
        payload = copy.deepcopy(BALANCE)
        payload["result"]["items"][1]["quantity"] = "3.1234567"
        snap = to_snapshot(payload, fetched_at=FETCHED, cash={}, usdkrw=1375)
        assert snap.holdings[1].qty_display == "3.1234567"

    def test_market_currency_and_prices_come_from_the_response(self):
        snap = to_snapshot(BALANCE, fetched_at=FETCHED, cash={}, usdkrw=1375)
        assert [(h.market, h.currency, h.avg_price, h.last_price) for h in snap.holdings] == [("KR", "KRW", 65000.0, 72000.0), ("US", "USD", 155.3, 178.5)]

    def test_the_total_value_matches_the_hand_calculation(self):
        snap = to_snapshot(BALANCE, fetched_at=FETCHED, cash={"KRW": "5000000", "USD": "3500.5"}, usdkrw=1375)
        assert snap.value_krw() == 19_467_562.5

    def test_an_empty_account_still_reports_cash(self):
        snap = to_snapshot(EMPTY, fetched_at=FETCHED, cash={"KRW": "12", "USD": "0"})
        assert snap.holdings == () and snap.cash == {"KRW": 12.0}
        assert snap.value_krw() == 12

    def test_an_empty_account_snapshot_can_be_valued(self):
        snap = to_snapshot(EMPTY, fetched_at=FETCHED, cash={"KRW": "12", "USD": "0"})

        assert snap.value_krw() == 12

    @pytest.mark.parametrize("payload", [{"unexpected": True}, {"result": {"items": [{"symbol": "A"}]}}])
    def test_an_invalid_shape_raises(self, payload):
        with pytest.raises((KeyError, TypeError, ValueError)):
            to_snapshot(payload, fetched_at=FETCHED, cash={})

    def test_a_non_numeric_amount_raises(self):
        payload = copy.deepcopy(BALANCE)
        payload["result"]["items"][0]["quantity"] = "많음"
        with pytest.raises(ValueError):
            to_snapshot(payload, fetched_at=FETCHED, cash={}, usdkrw=1375)

    @pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity"])
    def test_a_non_finite_amount_raises(self, invalid):
        payload = copy.deepcopy(BALANCE)
        payload["result"]["items"][0]["quantity"] = invalid
        with pytest.raises(ValueError):
            to_snapshot(payload, fetched_at=FETCHED, cash={}, usdkrw=1375)

    def test_a_gap_against_the_brokers_total_is_logged(self, caplog):
        payload = copy.deepcopy(BALANCE)
        payload["result"]["marketValue"]["amount"]["krw"] = "1"
        to_snapshot(payload, fetched_at=FETCHED, cash={}, usdkrw=1375)
        assert "0.5%" in caplog.text


class TestFxAndCash:
    def test_mid_rate_values_dollars_and_source_is_recorded(self):
        snap = to_snapshot(BALANCE, fetched_at=FETCHED, cash={}, usdkrw=1375)
        assert snap.holding_value_krw(snap.holdings[1]) == 2_454_375 and snap.fx_source == "broker"

    def test_a_fallback_rate_is_recorded_as_macro(self):
        snap = to_snapshot(BALANCE, fetched_at=FETCHED, cash={}, usdkrw=1350, fx_source="macro")
        assert snap.fx_source == "macro"

    def test_a_krw_only_account_needs_no_rate(self):
        snap = to_snapshot(KRW_ONLY, fetched_at=FETCHED, cash={"KRW": "1", "USD": "0"})
        assert snap.fx_to_krw == {"KRW": 1.0}
        assert snap.value_krw() == 7_200_001

    def test_a_krw_only_snapshot_can_be_valued(self):
        snap = to_snapshot(
            KRW_ONLY,
            fetched_at=FETCHED,
            cash={"KRW": "5000000", "USD": "0"},
        )

        assert snap.value_krw() == 12_200_000

    def test_a_krw_only_snapshot_renders(self):
        snap = to_snapshot(
            KRW_ONLY,
            fetched_at=FETCHED,
            cash={"KRW": "5000000", "USD": "0"},
        )

        assert isinstance(render_briefing(snap, now=FETCHED), str)

    def test_a_dollar_holding_without_any_rate_raises(self):
        with pytest.raises(TossError, match="USD"):
            to_snapshot(BALANCE, fetched_at=FETCHED, cash={})

    def test_a_balance_without_a_rate_is_named_not_a_keyerror(self):
        with pytest.raises(TossError, match="USD"):
            to_snapshot(
                KRW_ONLY,
                fetched_at=FETCHED,
                cash={"KRW": "1", "USD": "100"},
            )

    def test_an_unknown_currency_fails_at_mapping_time(self):
        payload = copy.deepcopy(KRW_ONLY)
        payload["result"]["items"][0]["currency"] = "JPY"

        with pytest.raises(TossError, match="JPY"):
            to_snapshot(payload, fetched_at=FETCHED, cash={"KRW": "1"})

    def test_the_crosscheck_is_skipped_when_there_is_no_rate(self):
        payload = copy.deepcopy(KRW_ONLY)
        payload["result"]["marketValue"]["amount"]["usd"] = "0"

        snap = to_snapshot(payload, fetched_at=FETCHED, cash={"KRW": "1", "USD": "0"})

        assert snap.value_krw() == 7_200_001

    def test_both_currencies_are_read(self):
        snap = to_snapshot(BALANCE, fetched_at=FETCHED, cash={"KRW": "5000000", "USD": "3500.5"}, usdkrw=1375)
        assert snap.cash == {"KRW": 5_000_000.0, "USD": 3500.5}


class TestReader:
    def test_configured_account_number_picks_sequence_and_read_headers(self, tmp_path):
        instance, transport = reader(tmp_path, success_responses())
        assert instance.snapshot().holdings[1].symbol == "AAPL"
        account_calls = [c for c in transport.calls if "/accounts" not in c[1] and "/oauth2/" not in c[1] and "/exchange-rate" not in c[1]]
        assert all(c[2]["headers"]["X-Tossinvest-Account"] == "1" for c in account_calls)

    def test_a_single_account_needs_no_configuration(self, tmp_path):
        instance, _ = reader(tmp_path, success_responses(), )
        instance.credentials = TossCredentials("client", "secret")
        assert instance.snapshot().holdings

    def test_several_accounts_without_selection_are_masked(self, tmp_path):
        accounts = {"result": [{"accountNo": "12345678901", "accountSeq": 1, "accountType": "BROKERAGE"}, {"accountNo": "99998888777", "accountSeq": 2, "accountType": "BROKERAGE"}]}
        instance, _ = reader(tmp_path, [token_response(), ok(accounts)])
        instance.credentials = TossCredentials("client", "secret")
        with pytest.raises(MissingCredentialsError) as exc:
            instance.snapshot()
        assert "78901" not in str(exc.value) and "88777" not in str(exc.value) and "***" in str(exc.value)

    def test_a_failed_cash_read_raises(self, tmp_path):
        broken = FakeResponse(500, {"error": {"code": "internal-error", "message": "bad"}})
        responses = [token_response(), ok(ACCOUNTS), ok(BALANCE), broken, broken, broken]
        instance, _ = reader(tmp_path, responses)
        with pytest.raises(TossError):
            instance.snapshot()

    def test_a_krw_only_account_does_not_request_exchange_rate(self, tmp_path):
        instance, transport = reader(tmp_path, [token_response(), ok(ACCOUNTS), ok(KRW_ONLY), ok(BUYING_POWER["KRW"]), ok({"result": {"currency": "USD", "cashBuyingPower": "0"}})])
        instance.snapshot()
        assert not any("exchange-rate" in url for _, url, _ in transport.calls)


class TestToken:
    def test_cached_token_is_reused(self, tmp_path):
        store = TokenStore(tmp_path / "token.json")
        store.save(Token("cached", FETCHED + timedelta(hours=1)))
        instance, transport = reader(tmp_path, success_responses(with_token=False))
        instance.token_store = store
        instance.snapshot()
        assert not any("oauth2/token" in url for _, url, _ in transport.calls)

    def test_token_close_to_expiry_is_reissued(self, tmp_path):
        store = TokenStore(tmp_path / "token.json")
        store.save(Token("old", FETCHED + timedelta(seconds=59)))
        instance, transport = reader(tmp_path, success_responses())
        instance.token_store = store
        instance.snapshot()
        assert "oauth2/token" in transport.calls[0][1]

    def test_token_cache_survives_a_new_store(self, tmp_path):
        path = tmp_path / "token.json"
        TokenStore(path).save(Token("kept", FETCHED + timedelta(hours=1)))
        assert TokenStore(path).load().access_token == "kept"

    def test_lifetime_comes_from_response(self, tmp_path):
        instance, _ = reader(tmp_path, [token_response(expires_in=120)] + success_responses(with_token=False))
        instance.snapshot()
        assert instance.token_store.load().expires_at == FETCHED + timedelta(seconds=120)

    def test_token_is_never_logged(self, tmp_path, caplog):
        instance, _ = reader(tmp_path, success_responses())
        instance.snapshot()
        assert "secret-token" not in caplog.text


class TestErrors:
    def test_an_ip_block_on_exchange_rate_is_not_masked_by_fallback(self, tmp_path):
        responses = [
            token_response(),
            ok(ACCOUNTS),
            ok(BALANCE),
            ok(BUYING_POWER["KRW"]),
            ok(BUYING_POWER["USD"]),
            FakeResponse(403, {"error": "access_denied"}),
        ]
        instance, _ = reader(tmp_path, responses, usdkrw_fallback=lambda: 1200.0)

        with pytest.raises(TossIPNotAllowedError):
            instance.snapshot()

    def test_403_names_the_ip_fix(self, tmp_path):
        instance, _ = reader(tmp_path, [FakeResponse(403, {"error": "access_denied"})])
        with pytest.raises(TossIPNotAllowedError, match="203.0.113.7") as exc:
            instance.snapshot()
        assert "허용" in str(exc.value)

    def test_ip_error_survives_lookup_failure(self, tmp_path):
        instance, _ = reader(tmp_path, [FakeResponse(403, {})], public_ip=lambda: (_ for _ in ()).throw(OSError()))
        with pytest.raises(TossIPNotAllowedError):
            instance.snapshot()

    def test_token_401_is_auth_error(self, tmp_path):
        instance, _ = reader(tmp_path, [FakeResponse(401, {"error": "invalid_client"})])
        with pytest.raises(TossAuthError):
            instance.snapshot()

    def test_expired_api_token_is_reissued_once(self, tmp_path):
        store = TokenStore(tmp_path / "token.json")
        store.save(Token("cached", FETCHED + timedelta(hours=1)))
        unauthorized = FakeResponse(401, {"error": {"code": "expired-token", "message": "expired"}})
        instance, _ = reader(tmp_path, [unauthorized, token_response("new")] + success_responses(with_token=False))
        instance.token_store = store
        assert instance.snapshot().holdings

    def test_a_second_401_gives_up(self, tmp_path):
        store = TokenStore(tmp_path / "token.json")
        store.save(Token("cached", FETCHED + timedelta(hours=1)))
        unauthorized = FakeResponse(401, {"error": {"code": "expired-token", "message": "expired"}})
        instance, _ = reader(tmp_path, [unauthorized, token_response("new"), unauthorized])
        instance.token_store = store
        with pytest.raises(TossAuthError):
            instance.snapshot()

    def test_429_waits_retry_after_then_raises(self, tmp_path):
        sleeps: list[float] = []
        limited = FakeResponse(429, {"error": {"code": "rate-limit-exceeded", "message": "slow"}}, {"Retry-After": "0.25"})
        instance, _ = reader(tmp_path, [limited, limited, limited], sleeper=sleeps.append)
        with pytest.raises(TossRateLimitError):
            instance.snapshot()
        assert sleeps == [0.25, 0.25]

    def test_a_malformed_retry_after_uses_backoff(self, tmp_path):
        sleeps: list[float] = []
        limited = FakeResponse(429, {"error": {"code": "rate-limit-exceeded", "message": "slow"}}, {"Retry-After": "later"})
        instance, _ = reader(tmp_path, [limited, limited, limited], sleeper=sleeps.append)
        with pytest.raises(TossRateLimitError):
            instance.snapshot()
        assert sleeps == [2, 4]

    def test_500_is_retried_before_giving_up(self, tmp_path):
        broken = FakeResponse(500, {"error": {"code": "internal-error", "message": "bad"}})
        instance, transport = reader(tmp_path, [broken, broken, broken])
        with pytest.raises(TossError):
            instance.snapshot()
        assert len(transport.calls) == 3

    def test_unknown_account_is_configuration_error(self, tmp_path):
        instance, _ = reader(tmp_path, [token_response(), ok(ACCOUNTS), FakeResponse(404, {"error": {"code": "account-not-found", "message": "missing"}})])
        with pytest.raises(MissingCredentialsError):
            instance.snapshot()

    def test_broker_error_code_is_in_message(self, tmp_path):
        instance, _ = reader(tmp_path, [FakeResponse(400, {"error": {"code": "invalid-request", "message": "bad"}})])
        with pytest.raises(TossError, match="invalid-request"):
            instance.snapshot()


def test_the_module_never_names_an_order_endpoint():
    source = Path(toss.__file__).read_text(encoding="utf-8")
    forbidden = ("/api/v1/" + "orders", "conditional-" + "orders")
    assert all(path not in source for path in forbidden)


class TestBuildReader:
    def test_missing_credentials_say_what_to_set(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TOSS_CLIENT_ID", raising=False)
        monkeypatch.delenv("TOSS_CLIENT_SECRET", raising=False)
        with pytest.raises(MissingCredentialsError, match="TOSS_CLIENT_ID"):
            build_reader(tmp_path)

    def test_token_cache_lives_under_state_root(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TOSS_CLIENT_ID", "id")
        monkeypatch.setenv("TOSS_CLIENT_SECRET", "secret")
        built = build_reader(tmp_path)
        assert built.token_store.path == tmp_path / "toss_token.json"
