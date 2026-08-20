# 설계서: 토스증권 계좌 읽기 어댑터 (주간 브리핑 M1 Task 6)

- 작성: 2026-08-19, 브랜치 `feat/weekly-briefing-m1`
- 상위 계획: `docs/superpowers/plans/2026-08-17-weekly-briefing-m1.md` Task 6
- **역할 분담: 이 문서는 설계다. 구현·테스트 작성은 codex가 한다.** 구현이 커밋되면
  설계자가 검토하고, 문제가 없으면 다음 설계서를 쓴다.
- 선행 상태: Task 1~5, 7 완료 (커밋 `f7e1dab`~`ead1865`), 테스트 829개 통과.
  이 태스크가 M1 완료 기준 4개를 막고 있다.

---

## 0. 무엇이 바뀌었나

M1 계획서 Task 6은 **"응답 스펙을 지어내지 않는다"**는 이유로 비워져 있었다. 계획을 쓴
세션과 구현 세션 모두 `developers.tossinvest.com` 문서에 도달하지 못했고, 필드명을 기억으로
쓰면 통과하는 테스트가 틀린 매핑을 보증하기 때문이다.

2026-08-19에 **공식 기계판독 스펙 원문을 확보했다.**

```
https://openapi.tossinvest.com/openapi-docs/latest/openapi.json
title: 토스증권 Open API   version: 1.2.14   servers: https://openapi.tossinvest.com
```

이 문서의 모든 필드명·타입·nullable 여부·오류 코드는 그 스펙에서 인용한 것이고,
추측이 아니다. **따라서 실계좌 호출 없이 Task 6 전체를 구현할 수 있다.** 남은 것은
실계좌 1회 대조(§12)이고, 그것은 구현 후 사용자가 확인한다.

계획서가 요구한 fixture는 **실계좌 응답 대신 스펙에 포함된 공식 예시(examples)를 쓴다.**
근거는 §4.10에 적었다.

---

## 1. 확정된 API 사실 (스펙 v1.2.14 인용)

### 1.1 인증

| 항목 | 값 |
|---|---|
| 토큰 | `POST /oauth2/token` |
| 요청 형식 | `application/x-www-form-urlencoded` |
| 본문 | `grant_type=client_credentials` (필수, 이 값만 지원), `client_id`, `client_secret` |
| 응답 | `access_token`, `token_type`(항상 `Bearer`), `expires_in`(초, 문서 예시 `86400`) |
| 사용 | 이후 모든 요청에 `Authorization: Bearer {access_token}` |
| refresh token | **없음.** 만료 시 같은 엔드포인트로 재발급 |
| 동시 유효 토큰 | **client당 1개. 재발급하면 이전 토큰이 즉시 무효화된다** |
| 스코프 | `securitySchemes.flows.clientCredentials.scopes = {}` — **읽기 전용 스코프가 없다** |

### 1.2 쓰는 엔드포인트 (4개, 전부 GET)

| 경로 | 쓰는 이유 | 헤더/파라미터 |
|---|---|---|
| `GET /api/v1/accounts` | `accountSeq`를 얻는다 | 없음 |
| `GET /api/v1/holdings` | 보유 종목 + 요약 금액 | `X-Tossinvest-Account: {accountSeq}` |
| `GET /api/v1/buying-power` | 현금 (통화별로 1회씩) | 같은 헤더 + `?currency=KRW\|USD` |
| `GET /api/v1/exchange-rate` | USD→KRW 환율 | `?baseCurrency=USD&quoteCurrency=KRW` |

`/api/v1/holdings`는 **국내(KR)·미국(US)을 한 응답에 담는다.** 해외 옵션·채권은 제외.
`?symbol=`로 필터할 수 있지만 우리는 쓰지 않는다(전체 보유가 필요하다).

### 1.3 응답 형태

성공은 공통 envelope `{"result": ...}`. 실패는 `{"error": {"requestId", "code", "message", "data"?}}`.
단 `/oauth2/token`만 OAuth2 표준 형식 `{"error", "error_description", "error_uri"}`를 쓴다.

**모든 금액·수량·환율은 문자열 decimal이다.** 숫자 타입이 아니다.

`GET /api/v1/holdings` → `result`:

```
totalPurchaseAmount   {krw: str, usd: str|null}      투자원금, 통화별 합산
marketValue.amount    {krw: str, usd: str|null}      평가금액, 통화별 합산
marketValue.amountAfterCost  {krw, usd}              세금·수수료 공제 후
profitLoss.amount / .amountAfterCost / .rate / .rateAfterCost
dailyProfitLoss.amount / .rate
items[]  symbol                str   KR: 6자리 숫자, US: 티커
         name                  str   종목명
         marketCountry         str   enum KR|US  (unknown 값 허용해야 함)
         currency              str   enum KRW|USD (unknown 값 허용해야 함)
         quantity              str   보유 수량 (decimal)
         lastPrice             str   현재가, 거래 통화 기준
         averagePurchasePrice  str   매수 평균가, 거래 통화 기준
         marketValue.purchaseAmount / .amount / .amountAfterCost   거래 통화 기준
         profitLoss.amount / .amountAfterCost / .rate / .rateAfterCost
         dailyProfitLoss.amount / .rate
         cost.commission       str
         cost.tax              str|null   세금이 없으면 null
```

`{krw, usd}` 쌍은 **통화별 합산이며 원화 환산 합계가 아니다.** `usd`는 해외 종목이 없으면
`null`, `krw`는 국내 종목이 없으면 `"0"`.

`GET /api/v1/accounts` → `result[]`: `accountNo`(str), `accountSeq`(int), `accountType`(enum,
현재 `BROKERAGE`만 반환). 계좌가 없으면 빈 배열.

`GET /api/v1/buying-power` → `result`: `currency`(enum), `cashBuyingPower`(str).
"미수 미발생 기준 현금 매수 가능 금액". KRW는 정수, USD는 소수 포함.

`GET /api/v1/exchange-rate` → `result`: `baseCurrency`, `quoteCurrency`, `rate`(매수 환율),
`midRate`(매매기준율), `basisPoint`, `rateChangeType`(UP|EQUAL|DOWN), `validFrom`, `validUntil`.
갱신 주기 1분, 참고용 표시 환율.

### 1.4 오류

| 상태 | 어디서 | 본문 | 뜻 |
|---|---|---|---|
| 403 | **`/oauth2/token`에서만 문서화** | `{"error":"access_denied","error_description":"IP address not allowed"}` | 허용 IP 목록에 없는 IP |
| 401 | `/oauth2/token` | `{"error":"invalid_client"}` + `WWW-Authenticate: Basic realm="openapi"` | 키가 틀림 / 클라이언트 비활성 |
| 401 | `/api/v1/*` | `error.code` = `invalid-token` \| `expired-token` \| `login-user-not-found` | 토큰 문제 |
| 400 | `/api/v1/*` | `error.code` = `account-header-required` \| `invalid-request` | 요청 형식 |
| 404 | `/api/v1/buying-power` | `error.code` = `account-not-found` | 계좌 없음 |
| 404 | `/api/v1/exchange-rate` | — | 환율 정보 없음 |
| 429 | 전부 | `error.code` = `rate-limit-exceeded` | 한도 초과 |
| 500 | 전부 | `error.code` = `internal-error` | 일시적 오류 |

429 응답 헤더: `X-RateLimit-Limit`(초당 허용 요청 수), `X-RateLimit-Remaining`,
`X-RateLimit-Reset`(재충전까지 예상 초), `Retry-After`(재시도 권장 초).
한도 그룹: `AUTH`, `ACCOUNT`, `ASSET`, `ORDER_INFO`, `MARKET_INFO`. 구체적 수치는 스펙에 없다.

### 1.5 없는 것

- **입출금 내역 API가 스펙 전체에 없다.** Task 3의 `net_flow_krw` 공급원은 존재하지 않는다.
- **종목별 원화 매입금액이 없다.** 종목별 누적 수익률은 거래 통화 기준으로만 낸다(계획서 §Task 3과 일치).
- **응답 기준 시각 필드가 없다.**
- **읽기 전용 스코프가 없다.**

---

## 2. 계획서의 「확인 결과 기록」 채움

계획서 Task 6 Step 1의 빈칸을 위 사실로 채운 것. (계획서 본문에도 같은 내용을 반영한다.)

```text
- 잔고 엔드포인트:            GET /api/v1/holdings — 요약 + items 를 한 응답에 담는다
- 계좌 헤더:                  X-Tossinvest-Account: <accountSeq> (정수)
                              값은 GET /api/v1/accounts 의 result[].accountSeq
- 토큰 엔드포인트 / expires_in: POST /oauth2/token (form-urlencoded), expires_in 은 초
                              (문서 예시 86400). client당 유효 토큰 1개
- 국내·해외가 한 응답인가:     예. result.items 에 KR/US 모두, marketCountry enum = KR|US
- 보유 수량 필드 / 타입:       items[].quantity — 문자열 decimal. 자릿수는 응답이 준 그대로
- 매입단가 필드 / 통화:        items[].averagePurchasePrice — items[].currency 기준
- 현재가 필드:                items[].lastPrice — items[].currency 기준
- 원화 평가금액 필드:          없음(종목별). result.marketValue.amount.{krw,usd} 는
                              통화별 합산이며 원화 환산 합계가 아니다
- 적용 환율 필드:              없음 → GET /api/v1/exchange-rate 별도 호출, midRate 사용
- 원화 매입금액 필드:          없음 → 종목별 원화 누적 수익률은 M1에서 내지 않는다
- 예수금 필드 (통화별):        없음 → GET /api/v1/buying-power?currency=KRW|USD 의
                              result.cashBuyingPower (예수금이 아니라 매수가능금액)
- 응답 기준 시각 필드:         없음 → 호출 시각(KST)을 as_of 로 쓰고 그 사실을 로그에 남긴다
- 입출금 내역 엔드포인트:      없음 → Task 3 net_flow_krw 는 항상 None,
                              "설명되지 않는 변화" 판별만 쓴다
- 읽기 전용 스코프:            없음. scopes = {} — 이 토큰으로 주문도 가능하다
```

---

## 3. 설계 결정과 근거

### 3.1 환율은 `midRate`(매매기준율)를 쓴다

`rate`는 매수 환율이고 문서 예시에서 `midRate` 대비 `basisPoint = 40`, 즉 0.4% 높다.
평가에 매수 환율을 쓰면 **달러 자산이 매번 0.4% 부풀려진다.** 그 편향은 두 스냅샷에
같은 방향으로 들어가므로 구간 수익률에서는 대부분 상쇄되지만, "전체 자산 N원"과
"현금 비중"에는 영구히 남는다.

fixture 값으로 차이를 계산하면: AAPL 10주 × 178.5달러 = 1,785달러.
- `midRate` 1375 → **2,454,375원**
- `rate` 1380.5 → 2,464,192.5원 (+9,817.5원)

교체가 한 줄로 끝나도록 `FX_FIELD = "midRate"` 모듈 상수로 둔다. §12의 앱 화면 대조에서
토스가 매수 환율로 환산하는 것이 확인되면 이 상수만 바꾼다.

**2026-08-20 실측 정정.** 위 0.4%는 스펙의 *예시* 값(`basisPoint = 40`)을 실제 스프레드로
읽은 것이고, 틀렸다. 실계좌 응답은 `rate = 1391.75`, `midRate = 1391.25`,
`basisPoint = "4"` — 스프레드는 **약 3.6 bp(0.036%)**로 예시의 10분의 1이다.
평가금액 1,000만원대에서 두 방식의 차이는 5천원 남짓이다.

결론은 바뀌지 않는다(평가에는 여전히 매매기준율이 맞다). 다만 `basisPoint`는 고정값이
아니라 그날의 스프레드이므로, "0.4%"를 어딘가에 상수처럼 적어두지 않는다. 그리고 앱
화면과 대조할 때 5천원 차이로는 어느 쪽인지 판정하기 어렵다는 점을 감안해야 한다 —
§12 질문 4는 이 정밀도로는 답이 안 나올 수 있다.

### 3.2 현금은 `cashBuyingPower`가 유일한 공급원이다

스펙에 예수금 API가 없다. `buying-power`의 `cashBuyingPower`는 "미수 미발생 기준 현금
매수 가능 금액"이므로 **앱의 예수금과 다를 수 있다** (매도 직후 미결제 금액 등).
그래도 이것이 유일한 선택지다. 두 가지를 지킨다.

- 이 값을 그대로 `AccountSnapshot.cash`에 넣는다. 보정하지 않는다.
- README에 "현금은 '지금 바로 살 수 있는 금액' 기준"이라고 적는다. 브리핑 독자에게는
  예수금보다 이쪽이 오히려 뜻이 분명하다.

### 3.3 두 통화 현금 조회는 **둘 다 성공해야 한다**

KRW·USD 각각 1회 호출한다. 어느 한쪽이라도 실패하면 **예외를 던지고 스냅샷을 만들지 않는다.**

한쪽을 0으로 채우면 총자산이 그만큼 작게 기록되고, **스냅샷은 앞으로 모든 수익률 계산의
유일한 원천이므로 그 오차가 영구히 남는다.** 실패는 사용자가 콘솔 앞에 있을 때 즉시
드러나고 다시 실행하면 복구되지만, 틀린 스냅샷은 복구되지 않는다.
(계획서의 "모르는 것을 0으로 채우지 않는다"의 직접 적용.)

USD 계좌가 없을 때 `?currency=USD`가 `"0"`을 주는지 400/404를 주는지는 스펙에서 확정할 수
없다(§12 미결 3). 400/404가 온다면 그때 이 결정을 바꾼다 — **바꾸기 전까지는 실패한다.**

### 3.4 `as_of`는 호출 시각(KST)이다

응답에 기준 시각이 없다. `datetime.now(KST)`를 쓰고 `LOGGER.info`로 그 사실을 남긴다.

**결과를 정직하게 기록한다:** `report/briefing.py`의 `STALE_AFTER`(6시간) 경고는 이 리더가
만든 스냅샷에서는 절대 발동하지 않는다. `as_of`가 우리 시계이므로 지연이 항상 0에 가깝다.
장이 닫힌 뒤의 `lastPrice`는 종가인데도 "방금 값"처럼 보인다는 뜻이다. M1에서는 이대로
두고(브리핑이 "지난 N일 동안"이라는 구간 표현을 쓰므로 오해가 크지 않다), 이 한계를
설계서와 계획서에 남긴다.

### 3.5 `accountSeq`를 헤더에 쓰고, `accountNo`는 안전 확인용으로만 쓴다

`X-Tossinvest-Account`는 **`accountSeq`(정수)** 를 받는다. 계좌번호가 아니다.

`TOSS_ACCOUNT_NO`는 그래서 **선택 항목**이 된다.

- 설정돼 있으면: `accounts` 응답에서 숫자만 남긴 `accountNo`와 정확히 일치하는 항목의
  `accountSeq`를 쓴다. 일치가 없으면 `MissingCredentialsError`.
- 비어 있고 계좌가 정확히 1개면: 그것을 쓴다.
- 비어 있고 계좌가 여러 개면: `MissingCredentialsError`로 **마스킹된 계좌번호 목록**을 보여준다.

일치 검사를 두는 이유는 잘못된 `accountSeq`가 **조용히 다른 계좌를 읽기** 때문이다.
`.env.template`·README의 문구를 "계좌가 여러 개일 때 필요, 안전 확인용"으로 고친다.

`accountSeq`는 캐시하지 않는다. 매 실행 1회 호출은 싸고, 캐시된 seq가 낡으면 조용히 틀린다.

### 3.6 transport는 예외 대신 상태 코드를 돌려준다

`notify/telegram.py`의 transport는 내부에서 `raise_for_status()`를 부른다. 텔레그램은
성공/실패만 구분하면 되기 때문이다. **토스는 401·403·429를 서로 다르게 처리해야 하므로
같은 방식이면 상태 코드가 예외 안에 묻힌다.**

```python
class HttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    text: str
    def json(self) -> Any: ...

Transport = Callable[..., HttpResponse]   # transport(method, url, **kwargs)

def _requests_transport(method: str, url: str, **kwargs: Any) -> HttpResponse:
    return requests.request(method, url, timeout=20, **kwargs)
```

계획서 Task 6 Step 2의 테스트 뼈대는 transport가 `_http_error(403)`을 **던지는** 형태였다.
그 뼈대는 문서에서 "뼈대"로 명시돼 있고, 여기서 상태 코드 반환형으로 바꾼다.

### 3.7 토큰 캐시는 반드시 존중한다

`state/toss_token.json`에 `{"access_token": ..., "expires_at": <ISO>}`.
만료 **60초 전**부터 재발급. 유효기간은 응답의 `expires_in`을 쓰고 상수로 박지 않는다.

이것이 편의가 아니라 정확성 문제인 이유: **client당 유효 토큰이 1개이고 재발급이 이전
토큰을 즉시 무효화한다.** 매 실행 새로 받으면 동시에 돌아가는 다른 호출이 깨진다.

- 토큰 값을 **로그에 절대 남기지 않는다** (테스트로 고정).
- `state/`는 `.gitignore` 대상이므로 커밋되지 않는다.
- `scratch/toss_probe.py`(§11)를 브리핑 실행 중에 돌리면 서로의 토큰을 무효화한다.
  README에 경고를 적는다.

### 3.8 403은 발급 단계에서 판별한다

스펙은 403(허용 IP 아님)을 **`/oauth2/token`에만** 문서화한다. 즉 IP 차단은 토큰 발급에서
먼저 걸린다. 그래도 `/api/v1/*`에서 403이 오면 같은 예외로 처리한다(문서화 안 된 응답을
정상으로 오인하지 않기 위해).

`TossIPNotAllowedError` 메시지에 **현재 공인 IP를 넣는다.** 집 공인 IP는 통신사가 바꿀 수
있고, 키가 맞아도 몇 달에 한 번 403이 난다. 사용자가 주소를 따로 찾아야 하게 만들지 않는다.

**공인 IP 조회가 실패해도 예외 자체는 반드시 뜬다.** 진단 편의가 오류 보고를 삼키면 안 된다.
조회 함수는 주입 가능하게 두어 테스트가 네트워크를 타지 않게 한다.

### 3.9 요약 금액과 우리 계산을 교차 검증한다

`to_snapshot`에서:

```
ours     = Σ (quantity × lastPrice × rate(currency))
theirs   = result.marketValue.amount.krw + result.marketValue.amount.usd × rate("USD")
|ours - theirs| / theirs > 0.005  →  LOGGER.warning
```

fixture로는 정확히 일치한다: 7,200,000 + 1,785 × 1375 = 9,654,375 = 100×72000 + 10×178.5×1375.

예외가 아니라 경고인 이유는 요약이 공제 전 값이고 반올림 차이가 있을 수 있기 때문이다.
하지만 `averagePurchasePrice`를 `lastPrice`로 잘못 매핑하는 식의 오류는 0.5%를 크게 넘는다.

**한계도 적어둔다:** 양변이 같은 환율을 쓰므로 이 검증은 **환율 오류를 잡지 못한다.**
잡는 것은 종목별 수량·가격 필드 매핑 오류다.

### 3.10 fixture는 공식 스펙의 예시 원문을 쓴다

계획서는 실계좌 응답을 가공해 fixture로 쓰라고 했다. 그 요구의 목적은 **"필드명을 기억으로
쓰지 않는 것"**이었고, 기계판독 스펙은 그 목적을 더 잘 달성한다 — 예시뿐 아니라 타입과
nullable 여부까지 명시돼 있고, 한 계좌의 우연(마침 해외 주식이 없다 등)에 좌우되지 않는다.
게다가 실계좌 응답을 쓰면 금액을 가공해야 하는데, 수량·단가·평가금액을 서로 어긋나게
가공하면 **fixture가 내부적으로 모순된 상태**가 되어 교차 검증(§3.9) 테스트를 쓸 수 없다.

그래서 fixture는 스펙의 `examples`를 **원문 그대로** 저장한다(출처와 버전을 파일 옆에 적는다).
계좌번호도 예시값 `"12345678901"`이므로 가공할 실데이터가 없다.

**대신 §12의 실계좌 1회 대조를 필수 후속 작업으로 남긴다.** 실응답이 스펙과 다르면
(추가 필드, 다른 소수 자릿수, USD 계좌 없을 때의 동작) 그때 fixture를 보강한다.

### 3.11 macro 환율 폴백은 배선만 하고 `build_reader`는 쓰지 않는다

`fx_source="macro"` 경로를 완전히 구현하고 테스트하지만, `build_reader()`는
`usdkrw_fallback=None`으로 만든다.

근거: 토스 환율은 1분마다 갱신되고 재시도가 공짜다. 조회 실패 시 낡은 macro 값을 쓰면
모든 달러 숫자가 조용히 달라지는데, 사용자는 콘솔 앞에 있으므로 **큰 소리로 실패하고 다시
실행하는 편이 낫다.** 배선을 남겨두는 이유는 M2에서 macro 패널을 연결할 때 인터페이스와
`fx_source` 표기가 이미 검증돼 있게 하려는 것이다.

### 3.12 주문 경로의 부재를 테스트로 고정한다

**이 토큰은 주문을 넣을 수 있다** (`scopes = {}`). M1의 전제 — "계좌를 읽는 코드에 돈을
움직일 수 있는 줄이 하나도 없다" — 는 이제 포트 분리만으로는 부족하고, 모듈이 주문
경로 문자열을 담지 않는다는 것까지 확인해야 한다.

```python
def test_the_module_never_names_an_order_endpoint():
    source = Path(toss.__file__).read_text(encoding="utf-8")
    for forbidden in ("/api/v1/orders", "conditional-orders"):
        assert forbidden not in source
```

### 3.13 `build_account_reader`에 `state_root`를 넘긴다

토큰 캐시는 스냅샷과 같은 state 루트 아래 있어야 하고, `--config`가 지정한 경로를 따라야
한다. 현재 `briefing_service.build_account_reader()`는 인자가 없고, `cli.py`는 바로 위에서
이미 `state_root`를 계산해 둔다. 시그니처를 바꾼다.

```python
# briefing_service.py
def build_account_reader(state_root: str | Path) -> AccountReader:
    try:
        from tradingbot.account.toss import build_reader
    except ImportError as exc:
        raise MissingCredentialsError(...) from exc
    return build_reader(state_root)

# cli.py  (state_root 는 바로 위에서 이미 계산돼 있다)
reader = build_account_reader(state_root)
```

committed 테스트 중 `build_account_reader`를 부르는 것은 없다(확인함). `cli.py` 1줄 +
`briefing_service.py` 시그니처만 바뀐다.

---

## 4. 필드 매핑

`items[]` 하나 → `Holding` 하나.

| `Holding` | 출처 | 변환 |
|---|---|---|
| `symbol` | `items[].symbol` | 그대로 |
| `market` | `items[].marketCountry` | 그대로 (`"KR"`/`"US"`) |
| `qty` | `items[].quantity` | `float(Decimal(...))` |
| `qty_display` | `items[].quantity` | **문자열 원문 그대로** |
| `avg_price` | `items[].averagePurchasePrice` | `float(Decimal(...))` |
| `last_price` | `items[].lastPrice` | `float(Decimal(...))` |
| `currency` | `items[].currency` | 그대로 (`"KRW"`/`"USD"`) |

`items[].name`은 `Holding`에 자리가 없다. M1에서는 버린다(`Holding` 스키마를 늘리면
`SNAPSHOT_SCHEMA_VERSION`을 올려야 하고 기존 스냅샷을 못 읽는다). 종목명 표시는 M2 과제.

`AccountSnapshot`:

| 필드 | 출처 |
|---|---|
| `as_of` | 호출 시각 `datetime.now(KST)` (§3.4) |
| `holdings` | 위 표 |
| `cash` | `{"KRW": cashBuyingPower(KRW), "USD": cashBuyingPower(USD)}` |
| `fx_to_krw` | `{"KRW": 1.0}` + USD가 필요하면 `{"USD": midRate}` |
| `fx_source` | `/exchange-rate`에서 왔으면 `"broker"`, 폴백이면 `"macro"` |

**USD 환율은 "필요할 때만" 넣는다.** 필요 조건 = USD 보유 종목이 있거나 USD 현금이 0이 아님.
필요한데 환율이 없으면 **예외** (`rate()`가 KeyError를 내기 전에 뜻이 분명한 오류로).

원화만 있는 계좌는 `fx_to_krw = {"KRW": 1.0}`, `fx_source = "broker"`.
1.0은 시세가 아니라 정의이므로 환율 조회를 하지 않고, `"broker"`로 두면 브리핑의
"환율은 시장에서 가져온 값" 주석이 뜨지 않는다 — 원화 계좌에는 그 주석이 맞지 않다.

### 기대값 (fixture 기준, 테스트에 그대로 쓸 수 있다)

```
midRate = 1375,  cash = {KRW: 5000000, USD: 3500.5}

005930  100 × 72000 × 1      =  7,200,000
AAPL     10 × 178.5 × 1375   =  2,454,375
현금      5,000,000 + 3500.5 × 1375 = 9,813,187.5
------------------------------------------------
value_krw()                  = 19,467,562.5
```

---

## 5. 모듈 인터페이스

`src/tradingbot/account/toss.py`

```python
API_BASE = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"
ACCOUNTS_PATH = "/api/v1/accounts"
HOLDINGS_PATH = "/api/v1/holdings"
BUYING_POWER_PATH = "/api/v1/buying-power"
EXCHANGE_RATE_PATH = "/api/v1/exchange-rate"

SPEC_VERSION = "1.2.14"          # fixture 와 매핑의 출처
FX_FIELD = "midRate"             # §3.1
KST = timezone(timedelta(hours=9))
TOKEN_REFRESH_MARGIN = timedelta(seconds=60)
CASH_CURRENCIES = ("KRW", "USD")
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2, 4)
VALUE_CROSSCHECK_TOLERANCE = 0.005


class TossError(RuntimeError): ...
class TossAuthError(TossError): ...
class TossIPNotAllowedError(TossError): ...
class TossRateLimitError(TossError): ...


@dataclass(frozen=True)
class TossCredentials:
    client_id: str
    client_secret: str
    account_no: str = ""          # 선택 (§3.5)


@dataclass(frozen=True)
class Token:
    access_token: str
    expires_at: datetime

    def expired(self, now: datetime) -> bool: ...


class TokenStore:
    """state/toss_token.json 한 파일. load/save/clear 세 메서드."""
    def __init__(self, path: str | Path) -> None: ...
    def load(self) -> Token | None: ...
    def save(self, token: Token) -> None: ...
    def clear(self) -> None: ...


def to_snapshot(
    payload: dict,
    *,
    fetched_at: datetime,
    cash: Mapping[str, str | float],
    usdkrw: float | None = None,
    fx_source: str = "broker",
) -> AccountSnapshot:
    """순수 함수. GET /api/v1/holdings 응답 dict → AccountSnapshot."""


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
    ) -> None: ...

    def snapshot(self) -> AccountSnapshot: ...


def build_reader(state_root: str | Path) -> TossAccountReader:
    """환경변수에서 자격증명을 읽는다. 없으면 MissingCredentialsError."""
```

`snapshot()` 순서:

1. 토큰 확보 (캐시 유효하면 재사용)
2. `GET /api/v1/accounts` → `accountSeq` 선택 (§3.5)
3. `GET /api/v1/holdings`
4. `GET /api/v1/buying-power` × 2 (KRW, USD)
5. USD가 필요하면 `GET /api/v1/exchange-rate`
6. `to_snapshot(...)`

실행 1회당 요청 4~6개 (토큰은 만료 시에만).

재시도 정책:

| 상황 | 동작 |
|---|---|
| 토큰 403 (또는 API 403) | 즉시 `TossIPNotAllowedError` (공인 IP 포함). 재시도 없음 |
| 토큰 401 | 즉시 `TossAuthError`. **재발급 재시도 안 함** — 키가 틀렸으므로 반복해도 같다 |
| API 401 (`invalid-token`/`expired-token`) | 캐시 폐기 → 1회 재발급 → 같은 요청 1회 재시도. 또 401이면 `TossAuthError` |
| 429 | `Retry-After` (없으면 `BACKOFF_SECONDS`) 만큼 대기, `MAX_ATTEMPTS`까지. 이후 `TossRateLimitError` |
| 5xx | `BACKOFF_SECONDS`로 `MAX_ATTEMPTS`까지. 이후 `TossError` |
| 404 `account-not-found` | `MissingCredentialsError` — 재시도로 못 고치는 설정 문제 |
| 그 외 4xx | `TossError`에 `error.code`와 `error.message`를 담아 던진다 |

`MissingCredentialsError` 힌트 문구(기존 `krx_credentials`·`build_notifier` 패턴):

```
토스증권 WTS > 설정 > Open API 에서 client_id / client_secret 을 발급하고,
이 PC의 공인 IP를 허용 IP 목록에 등록한 뒤 TOSS_CLIENT_ID, TOSS_CLIENT_SECRET
환경변수로 등록하세요. 계좌가 여러 개면 TOSS_ACCOUNT_NO 도 지정하세요.
저장소에 커밋하지 마세요.
```

---

## 6. 파일 목록

| 구분 | 경로 |
|---|---|
| 생성 | `src/tradingbot/account/toss.py` |
| 생성 | `tests/test_account_toss.py` |
| 생성 | `tests/data/toss_balance_sample.json` |
| 생성 | `tests/data/toss_balance_krw_only_sample.json` |
| 생성 | `tests/data/toss_balance_empty_sample.json` |
| 생성 | `tests/data/toss_accounts_sample.json` |
| 생성 | `tests/data/toss_buying_power_sample.json` |
| 생성 | `tests/data/toss_exchange_rate_sample.json` |
| 생성 | `tests/data/README.md` — fixture 출처와 스펙 버전 (없으면) |
| 수정 | `src/tradingbot/briefing_service.py` — `build_account_reader(state_root)` |
| 수정 | `src/tradingbot/cli.py` — 호출부 1줄 |
| 수정 | `.env.template` — `TOSS_ACCOUNT_NO` 는 선택임을 명시 |
| 수정 | `README.md` — 현금의 뜻, 토큰 1개 정책, 프로브 동시 실행 금지 |
| 수정 | `docs/superpowers/plans/2026-08-17-weekly-briefing-m1.md` — Task 6 체크 + 확인 결과 기록 |

`broker/` 는 열지 않는다.

---

## 7. fixture 만들기

**손으로 옮겨 적지 않는다.** 스펙에서 뽑는다. `scratch/make_toss_fixtures.py`로 저장해
1회 실행하고 버린다(`scratch/`는 gitignore 대상).

```python
"""Extract the committed fixtures from the official spec. Run once."""
import json, pathlib, urllib.request

SPEC = "https://openapi.tossinvest.com/openapi-docs/latest/openapi.json"
spec = json.loads(urllib.request.urlopen(SPEC).read())
assert spec["info"]["version"] == "1.2.14", spec["info"]["version"]


def ex(path, method, name):
    content = spec["paths"][path][method]["responses"]["200"]["content"]
    return next(iter(content.values()))["examples"][name]["value"]


out = pathlib.Path("tests/data")
out.mkdir(parents=True, exist_ok=True)
files = {
    "toss_balance_sample.json": ex("/api/v1/holdings", "get", "withHoldings"),
    "toss_balance_krw_only_sample.json": ex("/api/v1/holdings", "get", "filteredBySymbol"),
    "toss_balance_empty_sample.json": ex("/api/v1/holdings", "get", "filteredBySymbolNotFound"),
    "toss_accounts_sample.json": ex("/api/v1/accounts", "get", "brokerageAccount"),
    "toss_exchange_rate_sample.json": ex("/api/v1/exchange-rate", "get", "usdToKrwUp"),
    "toss_buying_power_sample.json": {
        "KRW": ex("/api/v1/buying-power", "get", "krw"),
        "USD": ex("/api/v1/buying-power", "get", "usd"),
    },
}
for name, payload in files.items():
    (out / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("wrote", name)
```

이 스크립트는 설계자가 실행해 동작을 확인했다. 뽑히는 내용:

```
toss_balance_sample.json            005930 KR/KRW qty "100",  AAPL US/USD qty "10"
toss_balance_krw_only_sample.json   005930 만, marketValue.amount.usd = null
toss_balance_empty_sample.json      items = []
toss_accounts_sample.json           accountNo "12345678901", accountSeq 1, BROKERAGE
toss_exchange_rate_sample.json      rate "1380.5", midRate "1375", basisPoint "40"
toss_buying_power_sample.json       KRW "5000000", USD "3500.5"
```

**소수점 수량은 fixture를 고치지 않고 테스트 안에서 복사해 바꾼다.**

```python
payload = json.loads(json.dumps(SAMPLE))
payload["result"]["items"][1]["quantity"] = "3.1234567"
```

fixture 원문은 벤더가 준 그대로 남고, 소수점 표시 동작도 고정된다.

---

## 8. 테스트 명세

`tests/test_account_toss.py`. **네트워크 접근 금지** — transport·sleeper·now·public_ip를
전부 주입한다.

```python
@dataclass
class FakeResponse:
    status_code: int
    payload: Any = None
    headers: Mapping[str, str] = field(default_factory=dict)
    text: str = ""
    def json(self): return self.payload
```

`transport(method, url, **kwargs)`를 흉내내는 fake는 호출을 기록해 **요청 횟수**와
**헤더**를 검사할 수 있어야 한다.

### TestToSnapshot
1. `test_maps_every_holding_in_the_sample` — 2개
2. `test_quantity_display_is_the_brokers_own_string` — `"100"`, `"10"`
3. `test_a_fractional_quantity_keeps_every_digit_on_screen` — `"3.1234567"` 유지
4. `test_market_and_currency_come_from_the_response` — KR/KRW, US/USD
5. `test_average_and_last_price_are_the_brokers_figures` — 65000/72000, 155.3/178.5
6. `test_the_total_value_matches_the_hand_calculation` — `19_467_562.5`
7. `test_an_empty_account_has_no_holdings_but_still_reports_cash`
8. `test_an_unexpected_shape_raises_instead_of_producing_zeros` — `{"unexpected": True}`
9. `test_a_missing_required_field_raises` — `lastPrice` 삭제
10. `test_a_non_numeric_amount_raises` — `quantity = "많음"`
11. `test_a_gap_against_the_brokers_own_total_is_logged` — 요약을 어긋나게 하고 `caplog`

### TestFxRate
12. `test_the_mid_rate_is_what_values_a_dollar_holding` — AAPL 2,454,375 (1380.5 아님)
13. `test_the_brokers_rate_is_recorded_as_the_source` — `fx_source == "broker"`
14. `test_a_fallback_rate_is_recorded_as_macro` — `fx_source == "macro"`
15. `test_a_krw_only_account_needs_no_rate` — `fx_to_krw == {"KRW": 1.0}`, 환율 호출 0회
16. `test_a_dollar_holding_without_any_rate_raises` — 1.0으로 기본값 넣지 않는다

### TestCash
17. `test_both_currencies_are_read_into_the_snapshot` — 5,000,000 / 3500.5
18. `test_a_failed_cash_read_raises_rather_than_reporting_zero_cash` (§3.3)

### TestAccountSelection
19. `test_the_configured_account_number_picks_the_sequence` — 헤더가 `"1"`
20. `test_hyphens_in_the_configured_account_number_are_ignored`
21. `test_a_single_account_needs_no_configuration`
22. `test_several_accounts_with_no_match_says_which_ones_exist` — `MissingCredentialsError`,
    계좌번호는 마스킹돼 있어야 한다

### TestToken
23. `test_a_cached_token_is_reused_within_its_lifetime` — 토큰 요청 0회
24. `test_a_token_close_to_expiry_is_reissued_early` — 59초 남았으면 재발급
25. `test_the_token_cache_survives_a_new_reader` — `tmp_path`
26. `test_the_token_is_never_written_to_the_log` — `caplog` 전체에 토큰 문자열 없음
27. `test_the_lifetime_comes_from_the_response_not_a_constant` — `expires_in=120`을 반영

### TestErrors
28. `test_403_at_the_token_endpoint_names_the_ip_fix` — `"허용"` + IP 문자열 포함
29. `test_the_ip_error_is_raised_even_when_the_ip_lookup_fails` — `public_ip` 가 None/예외
30. `test_401_at_the_token_endpoint_is_an_auth_error_not_an_ip_error`
31. `test_an_expired_token_is_reissued_once_and_the_request_retried` — 성공으로 끝난다
32. `test_a_second_401_gives_up_with_an_auth_error`
33. `test_429_waits_the_retry_after_then_raises_a_rate_limit_error` — `sleeper` 기록 검사
34. `test_a_500_is_retried_before_giving_up`
35. `test_an_unknown_account_is_a_configuration_error` — 404 → `MissingCredentialsError`
36. `test_an_error_message_carries_the_brokers_own_code` — `str(exc)` 에 `error.code`

### TestNoOrderPath
37. `test_the_module_never_names_an_order_endpoint` (§3.12)

### TestBuildReader
38. `test_missing_credentials_say_what_to_set` — `monkeypatch.delenv`
39. `test_the_token_cache_lives_under_the_state_root` — `tmp_path`

---

## 9. 하지 말 것

- **주문·정정·취소 엔드포인트를 이 모듈에 넣지 않는다.** 경로 문자열조차 넣지 않는다(§3.12).
- **`broker/` 를 열지 않는다.**
- **평단·수량·환율을 우리가 다시 계산하지 않는다.** 응답 값이 진실이다.
  `Portfolio.apply_fill` 을 재사용하지 않는다.
- **모르는 값을 0이나 추정치로 채우지 않는다.**
- **테스트에서 네트워크를 쓰지 않는다.**
- **`AccountSnapshot`/`Holding` 스키마를 늘리지 않는다.** 늘리면
  `SNAPSHOT_SCHEMA_VERSION`을 올려야 하고 기존 스냅샷을 못 읽는다.
- **자격증명·토큰을 커밋하거나 로그에 남기지 않는다.** `config/*.toml`에 쓰지 않는다
  (`.gitignore`가 그 경로를 보호하지 않는다).
- **기존 CLI·백테스트·모의투자 동작을 바꾸지 않는다.** 829개 테스트가 회귀 방지선이다.
- 파일 쓰기는 `encoding="utf-8"` 명시. `.bat`도 UTF-8(BOM 없이)이며 첫 한글 줄 앞에
  `chcp 65001 > nul`을 둔다 (2026-08-20 정정 — M1 계획서 「지켜야 할 것」 참고).

---

## 10. 실행 순서 (TDD)

- [ ] **Step 1: fixture 6개 생성** (§7). 스펙 버전 `1.2.14` assert 를 통과해야 한다.
      `tests/data/README.md`에 출처 URL·버전·"실계좌 응답이 아님"을 적는다.
- [ ] **Step 2: 실패하는 테스트 작성** (§8, 39개).
- [ ] **Step 3: 실패 확인** — `ModuleNotFoundError: tradingbot.account.toss`
      ```powershell
      .\.venv\Scripts\python.exe -m pytest tests\test_account_toss.py -v --basetemp="$env:TEMP\pytest_tmp"
      ```
- [ ] **Step 4: `to_snapshot` 먼저 구현** (순수 함수). TestToSnapshot·TestFxRate 통과.
- [ ] **Step 5: `TokenStore` + 토큰 흐름 구현.** TestToken 통과.
- [ ] **Step 6: `TossAccountReader` + 오류 분류 구현.** 나머지 전부 통과.
- [ ] **Step 7: `build_reader` + 배선** — `build_account_reader(state_root)` (§3.13),
      `.env.template`, `README.md`.
- [ ] **Step 8: 전체 통과 확인** — 829 + 39 = **868개**, 회귀 0.
      ```powershell
      .\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\pytest_tmp"
      git diff --name-only main...HEAD    # broker/ 파일이 없어야 한다
      ```
- [ ] **Step 9: 계획서 갱신** — Task 6 체크박스, 「확인 결과 기록」(§2), 실행 기록에 이 설계서 링크.
- [ ] **Step 10: 커밋**
      ```
      BRIEF(part): Map the Toss balance response onto the read-only port
      ```

실계좌 대조(§12)는 사용자만 할 수 있으므로 이 커밋의 조건이 아니다.
계획서의 완료 기준 4개는 그 대조까지 끝난 뒤에 체크한다.

---

## 11. 실계좌 1회 호출 방법 (사용자용)

`scratch/toss_probe.py`가 이미 준비돼 있다(gitignore 대상, 커밋되지 않음).
토큰 발급 → 계좌 목록 → 보유 주식 → 현금 → 환율을 순서대로 1회씩 호출하고 원문을
`scratch/raw/*.json`에 저장한다. 시크릿은 출력하지 않고 토큰은 마스킹한다.

```powershell
# 1. 토스증권 WTS > 설정 > Open API 에서 client_id / client_secret 발급
# 2. 같은 화면 '허용 IP 관리'에 이 PC의 공인 IP 등록 (https://ifconfig.me 로 확인)
# 3. 이 세션에만 환경변수 설정 (파일에 적지 말 것)
$env:TOSS_CLIENT_ID = "c_..."
$env:TOSS_CLIENT_SECRET = "..."
$env:TOSS_ACCOUNT_NO = "12345678901"     # 계좌가 하나면 생략 가능

# 4. 실행
.\.venv\Scripts\python.exe scratch\toss_probe.py
```

`curl` 로 최소 확인만 하려면 두 줄이다.

```powershell
curl -X POST https://openapi.tossinvest.com/oauth2/token `
  -H "Content-Type: application/x-www-form-urlencoded" `
  -d "grant_type=client_credentials&client_id=<ID>&client_secret=<SECRET>"

curl https://openapi.tossinvest.com/api/v1/holdings `
  -H "Authorization: Bearer <ACCESS_TOKEN>" `
  -H "X-Tossinvest-Account: <accountSeq>"
```

**주의:** 토큰을 새로 받으면 이전 토큰이 즉시 무효화된다(§3.7). 브리핑이 돌고 있는 동안
이 스크립트를 실행하지 않는다.

403이 나오면 IP 문제다 — 스크립트가 현재 공인 IP를 함께 알려준다.
401이면 키 문제다.

---

## 12. 미결 사항 — 실계좌로만 확인된다

구현을 막지는 않지만, 확인 결과에 따라 **한 줄씩** 고칠 곳이다.

| # | 확인할 것 | 어디를 고치나 |
|---|---|---|
| 1 | 앱 화면의 수량·평단·평가금액이 응답 값과 일치하는지 | 일치하지 않으면 매핑(§4) |
| 2 | 앱의 '예수금'과 `cashBuyingPower(KRW)`가 같은지 | 다르면 README 문구(§3.2) |
| 3 | USD 계좌가 없을 때 `?currency=USD`가 `"0"`인지 400/404인지 | 400/404면 §3.3 결정 |
| 4 | 앱의 원화 환산 평가금액이 `rate`인지 `midRate`인지 | 다르면 `FX_FIELD` 한 줄(§3.1) |
| 5 | 실응답에 스펙에 없는 필드가 있는지 | 있으면 fixture 보강(§3.10) |
| 6 | 소수점 수량의 실제 자릿수 | fixture 파생 테스트 값(§7) |
| 7 | `expires_in` 실제 값 (문서 예시는 86400) | 상수화하지 않았으므로 코드 변경 없음 |

구현 후 사용자가 `주간 브리핑.bat`을 한 번 더블클릭해 §12를 확인하면 M1 완료 기준
4개가 모두 닫힌다.

### 2026-08-20 실계좌 확인 결과

`briefing weekly --no-notify`가 실계좌를 읽고 스냅샷까지 저장하는 데 성공했다
(공인 IP 허용 등록 완료 상태). 아래는 그 응답으로 답이 나온 항목이다.

| # | 결과 | 조치 |
|---|---|---|
| 3 | **미확인.** 이 계좌는 달러 보유가 있어 `?currency=USD`가 `"4.19"`를 정상 반환했다. 원화 전용 계좌 케이스는 여전히 열려 있다 | 없음 (§3.3 유지) |
| 4 | **판정 불가.** 실측 스프레드가 3.6 bp라 두 방식 차이가 1,400만원 기준 5천원뿐이다 | 없음. 근거는 §3.1 정정 참고 |
| 5 | **없음.** 응답 필드가 스펙과 정확히 일치한다. 보유 항목은 `symbol, name, marketCountry, currency, quantity, lastPrice, averagePurchasePrice, marketValue, profitLoss, dailyProfitLoss, cost` 11개뿐이고 그 외는 없다 | 없음 (fixture 유지) |
| 6 | **소수점 없음.** 실제 수량은 `"1", "5", "44", "43", "1"` — 전부 정수 문자열이다. 다만 타입은 문자열이므로 소수 파싱 경로는 그대로 둔다 | 없음 |
| 7 | `expires_in = 86399` (문서 예시 86400과 1초 차이). 상수화하지 않았으므로 무관 | 없음 |

1번과 2번은 앱 화면을 눈으로 대조해야 하므로 사용자 몫으로 남는다.

---

## 13. 이 설계가 남기는 알려진 한계

정직하게 적어둔다. 고칠 계획이 아니라, 다음 사람이 다시 발견하지 않게 하려는 기록이다.

1. **`as_of`가 우리 시계다.** 브리핑의 6시간 지연 경고는 이 리더에서 발동하지 않는다(§3.4).
2. **입출금을 알 수 없다.** 토스에 입출금 API가 없으므로 Task 3의 `net_flow_krw`는 항상
   `None`이고, 입금과 직접 매매를 구분하지 못한다. 그래서 브리핑의 사유 문구가 둘 다 묻는다.
3. **종목별 원화 누적 수익률을 낼 수 없다.** 원화 매입금액 필드가 없다. 거래 통화 기준만 낸다.
4. **종목명을 쓰지 않는다.** 응답에 `name`이 있지만 `Holding`에 자리가 없다(M2).
5. **교차 검증이 환율 오류를 못 잡는다**(§3.9).
6. **토큰이 주문 권한을 갖는다.** 읽기 전용 스코프가 없으므로, 이 자격증명이 유출되면
   주문도 가능하다. 완화책은 허용 IP 목록과 §3.12의 테스트뿐이다. 이 사실을 README에 적는다.
