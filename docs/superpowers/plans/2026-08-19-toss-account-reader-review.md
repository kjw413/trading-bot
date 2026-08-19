# 검토서: 토스 계좌 어댑터 구현 (커밋 `3edd8e2`)

- 검토: 2026-08-19, 설계자
- 대상: `3edd8e2 BRIEF(part): Map the Toss balance response onto the read-only port`
- 설계서: `2026-08-19-toss-account-reader-design.md`
- 결론: **결함 3건 수정 후 재검토. 그중 1건은 원화 전용 계좌의 첫 실행을 막는다.**

---

## 0. 잘 된 것

설계와 다른 판단을 한 곳이 없다. 확인한 것:

- fixture 6개가 **공식 스펙 v1.2.14 예시와 완전히 동일**하다 (프로그램으로 대조함).
- 매핑이 설계 §4대로다. `qty_display`가 응답 문자열 원문이고, 환산 총액이 손계산과
  일치한다 (`value_krw() == 19,467,562.5`).
- `FX_FIELD = "midRate"`, `VALUE_CROSSCHECK_TOLERANCE = 0.005` 등 설계가 지정한 상수가
  그대로 있고 교체 지점이 한 줄이다.
- 주문 경로 부재 테스트가 있다. 금지 문자열을 문자열 연결로 만들어 테스트 파일 자체가
  검사에 걸리지 않게 한 것은 설계보다 나은 처리다.
- 토큰 캐시가 임시파일 + `os.replace` + `chmod 600` 원자적 쓰기다. 설계보다 강하다.
- 자격증명 없을 때 CLI 경로가 여전히 정상적으로 퇴화한다 (안내 출력 + 종료코드 1, 확인함).
- `broker/` 를 열지 않았다. 테스트 871개 통과, 회귀 없음.

아래 3건은 전부 **재현했다.** 추측이 아니다.

---

## 1. 결함 1 (블로킹): 원화 전용 계좌의 스냅샷을 원화로 환산할 수 없다

### 증상

```
국내 종목만 보유 + USD 현금 0  (리더가 실제로 만드는 조합)
  cash      = {'KRW': 5000000.0, 'USD': 0.0}
  fx_to_krw = {'KRW': 1.0}
  cash_krw()      -> KeyError: 'USD'
  value_krw()     -> KeyError: 'USD'
  weights_krw()   -> KeyError: 'USD'
  render_briefing -> KeyError: 'USD'
```

### 왜 반드시 일어나는가

`AccountSnapshot`이 전제하는 불변식은 **"`cash`와 보유 종목에 등장하는 모든 통화는
`fx_to_krw`에 있어야 한다"**이다. `cash_krw()`가 `self.cash`의 모든 키에 대해
`rate(cur)`를 부르고, `rate()`는 없는 통화를 1.0으로 채우지 않고 `KeyError`를 내도록
`base.py`에 일부러 그렇게 쓰여 있다.

리더는 `CASH_CURRENCIES = ("KRW", "USD")`로 **항상 두 통화를 조회**하므로 `cash`에는
언제나 `"USD"` 키가 있다. 그런데 `to_snapshot`은 `needs_usd`가 False일 때
`fx_to_krw`에 `"USD"`를 넣지 않는다. 즉 **달러 자산이 없는 모든 계좌에서 불변식이 깨진다.**

- 미국 주식이 없고 달러 예수금이 0인 계좌 → 첫 실행부터 깨진다
- 아무것도 없는 계좌 → 같다

이 사용자가 시작하는 가장 흔한 상태가 정확히 이 경우다.

### 왜 테스트 871개가 못 잡았는가

`to_snapshot`으로 만든 스냅샷의 **모양만 검사하고, 그 스냅샷을 쓰지 않는다.**

```python
def test_a_krw_only_account_needs_no_rate(self):
    snap = to_snapshot(KRW_ONLY, fetched_at=FETCHED, cash={"KRW": "1", "USD": "0"})
    assert snap.fx_to_krw == {"KRW": 1.0}          # 여기서 끝난다
```

`test_an_empty_account_still_reports_cash`도 `holdings`와 `cash`만 본다.
**어느 테스트도 `value_krw()`를 부르지 않는다.**

### 왜 나쁜 실패인가

`reader.snapshot()`은 성공한다. 따라서 `run_briefing`의 `try/except`를 통과하고
**깨진 스냅샷이 `state/account/`에 먼저 저장된다.** 그 다음 `render_briefing`에서
`KeyError: 'USD'`가 터지는데, 그 호출은 `try` 밖이라 콘솔에 파이썬 트레이스백이 그대로 뜬다.
게다가 저장된 그 파일은 앞으로 모든 실행에서 다시 읽히므로, **한 번 만들어지면 이후
실행도 계속 같은 자리에서 죽는다.**

### 고치는 방법

`to_snapshot`에서 불변식을 한곳에서 지킨다. 빈 통화는 버리고, 값이 있는데 환율이 없으면
`KeyError`가 아니라 **뜻이 분명한 오류**를 낸다.

```python
    values = {currency: _number(amount) for currency, amount in cash.items()}
    needs_usd = any(h.currency == "USD" for h in holdings) or values.get("USD", 0) != 0
    fx = {"KRW": 1.0}
    if needs_usd:
        if usdkrw is None:
            raise TossError("USD 자산을 원화로 평가할 환율이 없습니다.")
        fx["USD"] = _number(usdkrw)

    # 잔액이 0인 통화는 환율이 필요 없다. 빈 칸을 버리면 어떤 합계도 달라지지 않고,
    # 남겨두면 cash_krw() 가 애초에 조회하지 않은 환율을 요구하게 된다.
    cash_values = {cur: val for cur, val in values.items() if cur in fx or val}

    # 환율 없는 통화가 스냅샷에 남으면 지금 여기서 실패해야 한다. 저장된 뒤에
    # value_krw() 가 KeyError 로 죽으면, 그 파일은 이후 모든 실행을 같이 죽인다.
    unpriced = {cur for cur in cash_values if cur not in fx}
    unpriced |= {h.currency for h in holdings if h.currency not in fx}
    if unpriced:
        raise TossError(
            f"원화로 환산할 환율이 없는 통화가 있습니다: {sorted(unpriced)}"
        )
```

마지막 검사는 덤으로 하나를 더 막는다. 스펙이 "unknown enum 값을 허용하도록 구현하라"고
경고했으므로 `currency`에 `KRW`/`USD`가 아닌 값이 올 수 있는데, 지금 코드는 그것도
저장한 뒤 나중에 `KeyError`로 죽는다.

---

## 2. 결함 2: 교차 검증이 실행을 죽인다

### 증상

```
국내만 보유 + 응답이 marketValue.amount.usd 를 null 대신 "0" 으로 줄 때
  amount = {'krw': '7200000', 'usd': '0'}
  결과: TossError: USD 평가금액을 교차 검증할 환율이 없습니다.
```

리더 전체 경로에서도 같다 (`token → accounts → holdings → buying-power ×2` 까지 가서 실패).

### 왜 결함인가

설계 §3.9는 교차 검증을 **경고**로 규정했다. 요약이 공제 전 값이고 반올림 차이가 있을 수
있어서, 매핑 오류를 알려주는 진단 장치이지 실행 조건이 아니기 때문이다.
구현은 이 진단 안에서 `raise`를 한다.

```python
    if amount.get("usd") is not None:
        if usdkrw is None:
            raise TossError("USD 평가금액을 교차 검증할 환율이 없습니다.")
```

이 경로가 열려 있는지 여부는 **설계서 §12 미결 5(실응답이 스펙과 같은가)** 에 달려 있다.
스펙은 "해외 종목이 없으면 `usd`는 null"이라고 하지만, 그것은 아직 실응답으로 확인되지
않은 항목이다. `"0"`이 오면 달러가 한 푼도 없는 계좌의 브리핑 전체가 실패하고,
사용자는 "교차 검증할 환율이 없습니다"라는 자기와 무관한 이유를 읽게 된다.

### 고치는 방법

```python
    amount = result["marketValue"]["amount"]
    theirs = _number(amount["krw"])
    reported_usd = amount.get("usd")
    if reported_usd is not None:
        rate = snapshot.fx_to_krw.get("USD")
        if rate is None:
            # 이 비교는 진단이다. 환율이 없다는 것은 달러 자산이 없다는 뜻이므로,
            # 경고만 남기는 검사 때문에 실행을 실패시키지 않는다.
            LOGGER.info("달러 평가금액 교차 검증을 건너뜁니다 (적용 환율 없음).")
        else:
            theirs += _number(reported_usd) * rate
```

---

## 3. 결함 3: macro 폴백이 IP 차단·인증 실패를 삼킨다

### 증상

`usdkrw_fallback`을 연결한 상태에서 환율 호출이 403(허용 IP 아님)을 받으면:

```
  결과: 정상 종료. fx_source='macro' USD환율=1200.0
  AAPL 2,142,000원  (정상 2,454,375원 — 12.7% 낮게 평가)
```

브리핑은 아무 문제 없다는 듯 끝난다. 사용자는 IP가 막힌 사실을 알 수 없다.

### 왜 결함인가

```python
            except TossError:
                if self.usdkrw_fallback is None:
                    raise
```

`TossIPNotAllowedError`와 `TossAuthError`는 `TossError`의 하위 클래스다. 폴백은
**환율을 못 구한 경우**를 위한 것인데, 자격증명 문제까지 같이 잡아 "시장 환율로 계산했다"로
바꿔버린다.

지금 `build_reader()`가 폴백을 넘기지 않으므로 실사용에서는 다시 던진다. 즉 **잠재
결함**이다. 하지만 설계 §3.11이 이 배선을 남긴 이유가 M2에서 macro 패널을 연결하는
것이므로, 그때 조용히 활성화된다.

### 고치는 방법

```python
            except (TossAuthError, TossIPNotAllowedError):
                raise            # 자격증명 문제는 환율 폴백으로 가릴 문제가 아니다
            except TossError:
                if self.usdkrw_fallback is None:
                    raise
                ...
```

---

## 4. 추가할 회귀 테스트

결함 1이 871개를 통과했다는 사실 자체가 테스트 설계의 문제를 가리킨다.
**스냅샷을 만든 테스트는 그 스냅샷을 써야 한다.** 모양만 보면 쓸 수 없는 스냅샷이 통과한다.

- [ ] `test_a_krw_only_snapshot_can_be_valued`
      `to_snapshot(KRW_ONLY, cash={"KRW": "5000000", "USD": "0"})` →
      `value_krw() == 12_200_000` (7,200,000 + 5,000,000)
- [ ] `test_an_empty_account_snapshot_can_be_valued`
      `EMPTY` + `cash={"KRW": "12", "USD": "0"}` → `value_krw() == 12`
- [ ] `test_a_krw_only_snapshot_renders`
      `render_briefing(snap)` 이 예외 없이 문자열을 낸다 (결함 1의 실제 파급 지점)
- [ ] `test_a_balance_without_a_rate_is_named_not_a_keyerror`
      `cash={"KRW": "1", "USD": "100"}`, `usdkrw=None` → `TossError`, 메시지에 `USD`
- [ ] `test_an_unknown_currency_fails_at_mapping_time`
      `currency = "JPY"` 인 종목 → `to_snapshot` 에서 `TossError` (저장된 뒤가 아니라)
- [ ] `test_the_crosscheck_is_skipped_when_there_is_no_rate`
      `KRW_ONLY` 의 `amount.usd` 를 `"0"` 으로 바꾸고 `usdkrw=None` → 예외 없음
- [ ] `test_an_ip_block_on_the_exchange_rate_is_not_masked_by_the_fallback`
      `usdkrw_fallback` 연결 + 환율 호출 403 → `TossIPNotAllowedError`

기존 `test_a_krw_only_account_needs_no_rate`와 `test_an_empty_account_still_reports_cash`는
`value_krw()` 호출을 덧붙여 강화한다.

---

## 5. 결함은 아니지만 고쳐야 할 것

### 5.1 이제 거짓이 된 안내 문구

`briefing_service.build_account_reader`의 `ImportError` 분기가 아직 이렇게 말한다.

> 토스증권 계좌 읽기 어댑터(src/tradingbot/account/toss.py)가 아직 없습니다.
> M1 계획서 Task 6을 먼저 진행하세요 …

어댑터는 있다. 이 분기는 이제 `toss.py` **안에서** 발생한 `ImportError`
(예: `requests` 미설치) 를 잡는다. 그 경우 사용자는 이미 끝난 작업을 하라는 안내를 읽는다.
"의존성 설치가 필요하다"는 뜻으로 고친다.

### 5.2 이 저장소의 문체와 다르다

| 파일 | 독스트링 줄 | 전체 줄 | 100자 초과 줄 | 최장 |
|---|---|---|---|---|
| `account/toss.py` | **1** | 412 | 4 | 166 |
| `account/base.py` | 28 | 135 | 0 | 88 |
| `briefing_service.py` | 39 | 238 | 0 | 89 |
| `notify/telegram.py` | 11 | 101 | 0 | 89 |
| `tests/test_account_toss.py` | — | 319 | **17** | 181 |

린터 설정이 없으니 실패하지는 않는다. 그래도 이 저장소는 **결정의 이유를 코드 옆에
적는 것**이 일관된 규칙이고(`base.py`가 "왜 `rate()`가 1.0을 채우지 않는가"를 적어둔 것이
바로 결함 1을 진단하게 해준 근거다), 이번 모듈은 412줄에 이유가 한 줄도 없다.

최소한 이 세 곳의 근거는 코드에 옮긴다. 설계서에만 있으면 다음에 고치는 사람이 못 본다.

- `FX_FIELD = "midRate"` 옆에 — 매수 환율을 쓰면 달러 자산이 영구히 0.4% 부풀려진다
- 현금 조회 실패가 예외인 이유 — 스냅샷은 이후 모든 수익률의 유일한 입력이다
- transport가 예외 대신 상태 코드를 돌려주는 이유 — 401·403·429를 구분해야 한다

### 5.3 `_send`의 도달 불가 코드

`_send` 말미의 `assert last is not None; return last`는 도달할 수 없다(모든 분기가
return 하거나 raise 하거나 continue 한다). 해롭지는 않으나 지우는 편이 읽기 쉽다.

---

## 6. 브랜치 상태 — 사용자 확인 필요

검토 중 발견한 저장소 상태다. 코드 문제는 아니지만 알고 계셔야 한다.

- `feat/weekly-briefing-m1`이 **`main`으로 fast-forward 병합되고 로컬 브랜치가 삭제**되었다.
  현재 `HEAD`는 `main`이고 `origin/main`보다 **9개 앞서 있으며 아직 push되지 않았다.**
- `origin/feat/weekly-briefing-m1`은 `ac8967b`(설계서)에 멈춰 있어 구현 커밋이 없다.
  GitHub에서 보이는 브랜치는 낡은 상태다.
- 병합 시점에 **계획서 완료 기준 2개가 아직 열려 있다** — 앱 화면 숫자 대조, 폰으로
  실제 수신. 두 개 모두 사용자만 확인할 수 있다.

되돌릴지, 그대로 두고 push할지는 사용자 결정이다. 이 검토서는 손대지 않았다.

---

## 7. 다음 순서

1. codex가 §1·§2·§3을 고치고 §4의 테스트 7개를 추가한다 (§5도 함께).
2. 전체 테스트 통과 확인 — 871 + 7 = **878개**, 회귀 0.
3. 커밋: `BRIEF(part): Keep an account without dollars from breaking its own snapshot`
4. 설계자가 재검토한다. 통과하면 남은 것은 사용자의 실계좌 대조(설계서 §12)뿐이다.
