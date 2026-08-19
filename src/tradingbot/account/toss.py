"""Read a Toss Securities account without exposing an order-capable interface.

The broker response is authoritative for quantities, prices, and cash. This
module only maps those values onto AccountSnapshot and supplies the exchange
rate required to value them in won.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

import requests

from tradingbot.account.base import AccountSnapshot, Holding
from tradingbot.data.credentials import MissingCredentialsError
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)

API_BASE = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"
ACCOUNTS_PATH = "/api/v1/accounts"
HOLDINGS_PATH = "/api/v1/holdings"
BUYING_POWER_PATH = "/api/v1/buying-power"
EXCHANGE_RATE_PATH = "/api/v1/exchange-rate"
SPEC_VERSION = "1.2.14"
# The buy rate includes a spread that would permanently overstate dollar assets.
FX_FIELD = "midRate"
KST = timezone(timedelta(hours=9))
TOKEN_REFRESH_MARGIN = timedelta(seconds=60)
CASH_CURRENCIES = ("KRW", "USD")
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2, 4)
VALUE_CROSSCHECK_TOLERANCE = 0.005


class TossError(RuntimeError):
    pass


class TossAuthError(TossError):
    pass


class TossIPNotAllowedError(TossError):
    pass


class TossRateLimitError(TossError):
    pass


@dataclass(frozen=True)
class TossCredentials:
    client_id: str
    client_secret: str
    account_no: str = ""


@dataclass(frozen=True)
class Token:
    access_token: str
    expires_at: datetime

    def expired(self, now: datetime) -> bool:
        return self.expires_at <= now + TOKEN_REFRESH_MARGIN


class TokenStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> Token | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return Token(
                access_token=payload["access_token"],
                expires_at=datetime.fromisoformat(payload["expires_at"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, token: Token) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "access_token": token.access_token,
                "expires_at": token.expires_at.isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class HttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    text: str

    def json(self) -> Any: ...


Transport = Callable[..., HttpResponse]


def _requests_transport(method: str, url: str, **kwargs: Any) -> HttpResponse:
    """Return status and body intact so callers can distinguish 401/403/429."""
    return requests.request(method, url, timeout=20, **kwargs)


def _lookup_public_ip() -> str | None:
    try:
        response = requests.get("https://ifconfig.me/ip", timeout=5)
        if response.ok:
            return response.text.strip() or None
    except requests.RequestException:
        pass
    return None


def _number(value: str | float) -> float:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid decimal amount: {value!r}") from exc
    if not decimal.is_finite():
        raise ValueError(f"Non-finite decimal amount: {value!r}")
    return float(decimal)


def to_snapshot(
    payload: dict,
    *,
    fetched_at: datetime,
    cash: Mapping[str, str | float],
    usdkrw: float | None = None,
    fx_source: str = "broker",
) -> AccountSnapshot:
    result = payload["result"]
    rows = result["items"]
    if not isinstance(rows, list):
        raise TypeError("holdings result.items must be a list")

    holdings = tuple(
        Holding(
            symbol=row["symbol"],
            market=row["marketCountry"],
            qty=_number(row["quantity"]),
            qty_display=row["quantity"],
            avg_price=_number(row["averagePurchasePrice"]),
            last_price=_number(row["lastPrice"]),
            currency=row["currency"],
        )
        for row in rows
    )
    values = {currency: _number(amount) for currency, amount in cash.items()}
    needs_usd = any(h.currency == "USD" for h in holdings) or values.get("USD", 0) != 0
    fx = {"KRW": 1.0}
    if needs_usd:
        if usdkrw is None:
            raise TossError("USD 자산을 원화로 평가할 환율이 없습니다.")
        fx["USD"] = _number(usdkrw)

    # A zero balance needs no rate. Keeping its currency would make
    # AccountSnapshot.cash_krw() demand a rate that was intentionally not read.
    cash_values = {currency: value for currency, value in values.items() if currency in fx or value}
    unpriced = {currency for currency in cash_values if currency not in fx}
    unpriced |= {holding.currency for holding in holdings if holding.currency not in fx}
    if unpriced:
        raise TossError(f"원화로 환산할 환율이 없는 통화가 있습니다: {sorted(unpriced)}")

    snapshot = AccountSnapshot(
        as_of=fetched_at,
        holdings=holdings,
        cash=cash_values,
        fx_to_krw=fx,
        fx_source=fx_source,
    )

    amount = result["marketValue"]["amount"]
    theirs = _number(amount["krw"])
    reported_usd = amount.get("usd")
    if reported_usd is not None:
        rate = snapshot.fx_to_krw.get("USD")
        if rate is None:
            # Cross-checking is diagnostic; it must not make a valid snapshot fail.
            LOGGER.info("달러 평가금액 교차 검증을 건너뜁니다 (적용 환율 없음).")
        else:
            theirs += _number(reported_usd) * rate
    ours = sum(snapshot.holding_value_krw(h) for h in holdings)
    if theirs and abs(ours - theirs) / abs(theirs) > VALUE_CROSSCHECK_TOLERANCE:
        LOGGER.warning(
            "보유 종목 평가 합계가 토스 요약과 0.5%% 넘게 다릅니다: ours=%s theirs=%s",
            ours,
            theirs,
        )
    return snapshot


class TossAccountReader:
    def __init__(
        self,
        credentials: TossCredentials,
        *,
        transport: Transport = _requests_transport,
        token_store: TokenStore | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(KST),
        public_ip: Callable[[], str | None] = _lookup_public_ip,
        usdkrw_fallback: Callable[[], float | None] | None = None,
    ) -> None:
        self.credentials = credentials
        self.transport = transport
        self.token_store = token_store or TokenStore(Path("state") / "toss_token.json")
        self.sleeper = sleeper
        self.now = now
        self.public_ip = public_ip
        self.usdkrw_fallback = usdkrw_fallback

    def _error_parts(self, response: HttpResponse) -> tuple[str, str]:
        try:
            payload = response.json()
        except (ValueError, TypeError):
            return "http-error", response.text
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            return str(error.get("code", "http-error")), str(error.get("message", response.text))
        return str(error or "http-error"), str(payload.get("error_description", response.text))

    def _ip_error(self) -> TossIPNotAllowedError:
        try:
            ip = self.public_ip()
        except Exception:  # diagnostic lookup must not hide the real failure
            ip = None
        suffix = f" (현재 공인 IP: {ip})" if ip else ""
        return TossIPNotAllowedError(
            "현재 공인 IP가 토스증권 Open API 허용 IP 목록에 없습니다. "
            "WTS 설정에서 허용 IP를 확인하세요." + suffix
        )

    def _send(self, method: str, path: str, **kwargs: Any) -> HttpResponse:
        for attempt in range(MAX_ATTEMPTS):
            response = self.transport(method, API_BASE + path, **kwargs)
            if response.status_code < 400:
                return response
            if response.status_code == 403:
                raise self._ip_error()
            if response.status_code == 401:
                return response
            code, message = self._error_parts(response)
            if response.status_code == 404 and code == "account-not-found":
                raise MissingCredentialsError("선택한 토스증권 계좌를 찾을 수 없습니다. TOSS_ACCOUNT_NO를 확인하세요.")
            if response.status_code == 429:
                if attempt < MAX_ATTEMPTS - 1:
                    delay = response.headers.get("Retry-After")
                    try:
                        wait = float(delay) if delay is not None else BACKOFF_SECONDS[attempt]
                    except (TypeError, ValueError):
                        wait = BACKOFF_SECONDS[attempt]
                    self.sleeper(wait)
                    continue
                raise TossRateLimitError(f"{code}: {message}")
            if response.status_code >= 500:
                if attempt < MAX_ATTEMPTS - 1:
                    self.sleeper(BACKOFF_SECONDS[attempt])
                    continue
                raise TossError(f"{code}: {message}")
            raise TossError(f"{code}: {message}")

    def _issue_token(self) -> Token:
        response = self._send(
            "POST",
            TOKEN_PATH,
            data={
                "grant_type": "client_credentials",
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code == 401:
            raise TossAuthError("토스증권 client_id 또는 client_secret 인증에 실패했습니다.")
        payload = response.json()
        token = Token(
            access_token=payload["access_token"],
            expires_at=self.now() + timedelta(seconds=int(payload["expires_in"])),
        )
        self.token_store.save(token)
        return token

    def _token(self) -> Token:
        cached = self.token_store.load()
        if cached is not None and not cached.expired(self.now()):
            return cached
        return self._issue_token()

    def _get(
        self,
        path: str,
        token: Token,
        *,
        params: Mapping[str, str] | None = None,
        account_seq: int | None = None,
        retry_auth: bool = True,
    ) -> tuple[Any, Token]:
        headers = {"Authorization": f"Bearer {token.access_token}"}
        if account_seq is not None:
            headers["X-Tossinvest-Account"] = str(account_seq)
        response = self._send("GET", path, headers=headers, params=params)
        if response.status_code == 401:
            if not retry_auth:
                raise TossAuthError("토스증권 액세스 토큰 인증에 두 번 실패했습니다.")
            self.token_store.clear()
            replacement = self._issue_token()
            return self._get(
                path,
                replacement,
                params=params,
                account_seq=account_seq,
                retry_auth=False,
            )
        return response.json(), token

    @staticmethod
    def _digits(value: str) -> str:
        return "".join(ch for ch in value if ch.isdigit())

    @staticmethod
    def _mask(value: str) -> str:
        digits = TossAccountReader._digits(value)
        return f"{digits[:2]}***{digits[-2:]}" if len(digits) >= 4 else "***"

    def _select_account(self, payload: Any) -> int:
        accounts = payload["result"]
        wanted = self._digits(self.credentials.account_no)
        if wanted:
            for account in accounts:
                if self._digits(account["accountNo"]) == wanted:
                    return int(account["accountSeq"])
            raise MissingCredentialsError("TOSS_ACCOUNT_NO와 일치하는 토스증권 계좌가 없습니다.")
        if len(accounts) == 1:
            return int(accounts[0]["accountSeq"])
        if not accounts:
            raise MissingCredentialsError("조회 가능한 토스증권 계좌가 없습니다.")
        shown = ", ".join(self._mask(account["accountNo"]) for account in accounts)
        raise MissingCredentialsError(f"계좌가 여러 개입니다. TOSS_ACCOUNT_NO를 지정하세요: {shown}")

    def snapshot(self) -> AccountSnapshot:
        token = self._token()
        accounts, token = self._get(ACCOUNTS_PATH, token)
        account_seq = self._select_account(accounts)
        holdings, token = self._get(HOLDINGS_PATH, token, account_seq=account_seq)
        cash: dict[str, str] = {}
        # Both reads must succeed. A zero substituted after failure would poison
        # the only snapshot future interval returns can use.
        for currency in CASH_CURRENCIES:
            payload, token = self._get(
                BUYING_POWER_PATH,
                token,
                params={"currency": currency},
                account_seq=account_seq,
            )
            cash[currency] = payload["result"]["cashBuyingPower"]

        needs_usd = (
            any(row["currency"] == "USD" for row in holdings["result"]["items"])
            or _number(cash["USD"]) != 0
        )
        rate: float | None = None
        source = "broker"
        if needs_usd:
            try:
                payload, token = self._get(
                    EXCHANGE_RATE_PATH,
                    token,
                    params={"baseCurrency": "USD", "quoteCurrency": "KRW"},
                )
                rate = _number(payload["result"][FX_FIELD])
            except (TossAuthError, TossIPNotAllowedError):
                raise
            except TossError:
                if self.usdkrw_fallback is None:
                    raise
                rate = self.usdkrw_fallback()
                source = "macro"
            if rate is None:
                raise TossError("USD 자산을 원화로 평가할 환율이 없습니다.")

        fetched_at = self.now()
        LOGGER.info(
            "토스 응답에 기준 시각이 없어 호출 시각(KST)을 스냅샷 시각으로 사용합니다."
        )
        return to_snapshot(
            holdings,
            fetched_at=fetched_at,
            cash=cash,
            usdkrw=rate,
            fx_source=source,
        )


_CREDENTIAL_HINT = (
    "토스증권 WTS > 설정 > Open API 에서 client_id / client_secret 을 발급하고, "
    "이 PC의 공인 IP를 허용 IP 목록에 등록한 뒤 TOSS_CLIENT_ID, TOSS_CLIENT_SECRET "
    "환경변수로 등록하세요. 계좌가 여러 개면 TOSS_ACCOUNT_NO 도 지정하세요. "
    "저장소에 커밋하지 마세요."
)


def build_reader(state_root: str | Path) -> TossAccountReader:
    client_id = os.environ.get("TOSS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TOSS_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise MissingCredentialsError(_CREDENTIAL_HINT)
    return TossAccountReader(
        TossCredentials(client_id, client_secret, os.environ.get("TOSS_ACCOUNT_NO", "").strip()),
        token_store=TokenStore(Path(state_root) / "toss_token.json"),
    )
