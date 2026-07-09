"""한국투자증권 KIS Developers REST 브로커 스켈레톤 (v1: 시그니처만, 미구현).

실전/모의 도메인:
    실전: https://openapi.koreainvestment.com:9443
    모의: https://openapivts.koreainvestment.com:29443

구현 시 필요한 엔드포인트 (TR ID는 반드시 KIS Developers 최신 문서로 재검증할 것):
    - 토큰 발급:      POST /oauth2/tokenP  (access_token, 유효 24시간 — 재사용/캐시 필요)
    - 해시키:         POST /uapi/hashkey   (주문 등 POST body 서명)
    - 국내 현금 주문:  POST /uapi/domestic-stock/v1/trading/order-cash
                       TR: 매수 TTTC0802U / 매도 TTTC0801U (모의: VTTC0802U / VTTC0801U)
    - 국내 정정/취소:  POST /uapi/domestic-stock/v1/trading/order-rvsecncl (TR: TTTC0803U)
    - 국내 잔고:       GET  /uapi/domestic-stock/v1/trading/inquire-balance
                       TR: TTTC8434R (모의: VTTC8434R)
    - 해외(미국) 주문: POST /uapi/overseas-stock/v1/trading/order
                       TR: 매수 TTTT1002U / 매도 TTTT1006U (모의: VTTT1002U / VTTT1006U)
    - 해외 잔고:       GET  /uapi/overseas-stock/v1/trading/inquire-balance (TR: TTTS3012R)

설계 노트:
    - PaperBroker/BacktestBroker와 달리 체결 시뮬레이션이 없다. on_session_open /
      on_intraday_bars / on_session_close는 "브로커에 체결 내역을 조회해 새 Fill을
      돌려주는" 폴링 훅으로 구현한다 (주문 체결 통보 웹소켓은 v1 범위 밖).
    - DAY 주문 만료는 거래소가 처리하므로 expire_day_orders는 미체결 조회 후
      로컬 상태 동기화만 수행하면 된다.
    - app_key/app_secret은 환경변수나 별도 비밀 파일로 관리하고 git에 커밋하지 말 것.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from tradingbot.broker.base import Broker
from tradingbot.models import Bar, Fill, Order, Position

LIVE_BASE_URL = "https://openapi.koreainvestment.com:9443"
PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"


@dataclass(frozen=True)
class KISConfig:
    app_key: str
    app_secret: str
    account_no: str  # "12345678-01" 형식 (종합계좌번호-상품코드)
    paper: bool = True  # 모의투자 여부 — 실전 전환은 반드시 명시적으로

    @property
    def base_url(self) -> str:
        return PAPER_BASE_URL if self.paper else LIVE_BASE_URL

    @property
    def cano(self) -> str:
        return self.account_no.split("-")[0]

    @property
    def acnt_prdt_cd(self) -> str:
        parts = self.account_no.split("-")
        return parts[1] if len(parts) > 1 else "01"


class KISBroker(Broker):
    """KIS Developers REST 브로커. v1에서는 전 메서드 미구현(NotImplementedError)."""

    def __init__(self, config: KISConfig, market: str = "KR") -> None:
        self.config = config
        self.market = market.upper()
        self._access_token: str | None = None

    def _authenticate(self) -> str:
        # TODO: POST {base_url}/oauth2/tokenP with appkey/appsecret,
        # cache token (24h) to disk to respect KIS token issuance rate limits.
        raise NotImplementedError("KISBroker.authenticate: not implemented in v1")

    def submit(self, order: Order) -> Order:
        # TODO: KR -> order-cash (지정가 00 / 시장가 01), US -> overseas order.
        # STOP/MOC는 KIS 주문유형과 매핑 필요: STOP은 조건부지정가/감시주문 여부 확인,
        # MOC는 KR 장마감동시호가(시간외 아님) 매핑을 문서로 확인할 것.
        raise NotImplementedError("KISBroker.submit: not implemented in v1")

    def cancel(self, order_id: str) -> bool:
        # TODO: order-rvsecncl (취소는 정정취소구분코드 02).
        raise NotImplementedError("KISBroker.cancel: not implemented in v1")

    def open_orders(self) -> list[Order]:
        # TODO: 미체결 조회 (inquire-daily-ccld 또는 정정취소가능주문 조회).
        raise NotImplementedError("KISBroker.open_orders: not implemented in v1")

    def on_session_open(self, dt: date, opens: dict[str, float]) -> list[Fill]:
        # TODO: 체결 내역 조회 후 로컬에서 아직 반영 안 된 Fill 목록 반환.
        raise NotImplementedError("KISBroker.on_session_open: not implemented in v1")

    def on_intraday_bars(self, dt: date, bars: dict[str, Bar]) -> list[Fill]:
        # TODO: 위와 동일한 체결 동기화 폴링 훅.
        raise NotImplementedError("KISBroker.on_intraday_bars: not implemented in v1")

    def on_session_close(self, dt: date, bars: dict[str, Bar]) -> list[Fill]:
        # TODO: 장 마감 후 최종 체결 동기화.
        raise NotImplementedError("KISBroker.on_session_close: not implemented in v1")

    def expire_day_orders(self, dt: date) -> list[Order]:
        # TODO: 거래소가 만료시킨 미체결 주문을 조회해 로컬 상태와 동기화.
        raise NotImplementedError("KISBroker.expire_day_orders: not implemented in v1")

    def mark_to_market(self, prices: dict[str, float]) -> None:
        # TODO: 잔고 조회의 평가금액 사용 또는 로컬 포지션에 현재가 반영.
        raise NotImplementedError("KISBroker.mark_to_market: not implemented in v1")

    def position(self, symbol: str) -> Position:
        # TODO: inquire-balance 응답의 보유 종목에서 매핑.
        raise NotImplementedError("KISBroker.position: not implemented in v1")

    @property
    def cash(self) -> float:
        # TODO: inquire-balance 응답의 예수금(dnca_tot_amt) 사용.
        raise NotImplementedError("KISBroker.cash: not implemented in v1")

    @property
    def equity(self) -> float:
        # TODO: inquire-balance 응답의 총평가금액(tot_evlu_amt) 사용.
        raise NotImplementedError("KISBroker.equity: not implemented in v1")
