# 미국 ETF 로테이션 — 재측정 실행서 (2026-08-01)

이 문서는 **한 번에 실행할 수 있는 절차서**다. 이번 세션에서 코드는 바꿨지만
실데이터로는 아무것도 재지 못했다(아래 "무엇을 재지 못했는가" 참조). 여기 있는
명령을 순서대로 돌리면 판정이 나온다.

## 요약: 이번에 바뀐 것 네 가지

| # | 변경 | 성격 | 기대 방향 |
|---|---|---|---|
| 1 | 주문 사이징이 현금 버퍼를 미리 뗀다 | 버그 수정 | 거부율 17~19% → 0, 전략·벤치마크 양쪽 잡음 제거 |
| 2 | 모든 종목이 추세 아래면 청산한다 | 버그 수정 | MDD 개선. 필터가 작동하지 않던 구간에서 작동 |
| 3 | `safe_asset = "IEF"` 주차 | 신규 (opt-in) | 방어 구간 현금 드래그 감소 → CAGR |
| 4 | `momentum_blend` 팩터 | 신규 (미채택) | IC IR 개선 기대. **게이트 통과 전에는 쓰지 않는다** |

1·2는 버그 수정이라 되돌릴 선택지가 없다. 3·4는 선택이므로 **재고 나서**
쓸지 정한다.

## 순서

### 0. 데이터

```powershell
.\.venv\Scripts\python.exe -m tradingbot data update --market US `
  --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ --start 2007-01-01
```

### 1. 팩터 게이트부터 — 통과 못한 팩터는 쓰지 않는다

기존 두 팩터는 IC IR 0.09~0.14로 기준(0.30)에 크게 못 미쳤다. 새 블렌드가
나아졌는지 **먼저** 잰다.

```powershell
.\.venv\Scripts\python.exe -m tradingbot research report `
  --factors momentum_6m momentum_12m_ex1m momentum_blend momentum_blend_ex1m `
  --start 2010-01-01 --end 2018-12-31
```

- 블렌드가 게이트를 통과하면 `config/research.toml`의 `[factor_weights]`에
  올리고, `config/us_etf_rotation.toml`의 `factors`를 블렌드로 바꾼다.
- **통과하지 못하면 쓰지 않는다.** 그때는 팩터가 아니라 가설이 문제라는 뜻이고,
  이 유니버스에서 모멘텀으로는 안 된다는 결론이 후보에 오른다.

`--end`를 in-sample 끝(2018-12-31)으로 둔 것은 의도적이다. 팩터 선택도
파라미터 선택이다.

### 2. 파라미터 민감도 — In-sample에서만

```powershell
.\.venv\Scripts\python.exe -m tradingbot --config config\us_etf_rotation.toml `
  research sweep --strategy theme_multifactor --market US `
  --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ `
  --start 2010-01-01 --end 2018-12-31 `
  --benchmark-config config\us_etf_benchmark.toml `
  --param bear_exposure=0.5,0.75,1.0 `
  --param abs_momentum_ma_days=0,100,200 `
  --param safe_asset=IEF,none
```

리포트에서 봐야 할 것은 **최고 칸이 아니라 파라미터별 한계표**다. 다른
파라미터를 어떻게 두든 꾸준히 좋은 값을 고른다. 18개 조합을 시도해 최고를
고르는 것은 운이 좋을 기회를 18번 갖는 것이다.

`safe_asset=IEF,none`이 3번 변경의 효과를 격리해 준다.

도구는 in-sample을 벗어나는 창을 **거부한다.** 이건 실수 방지 장치다.

### 3. 판정 — 검증 구간

2단계에서 고른 값을 설정에 반영한 뒤:

```powershell
.\.venv\Scripts\python.exe -m tradingbot --config config\us_etf_rotation.toml `
  research evaluate --strategy theme_multifactor --market US `
  --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ `
  --start 2019-01-01 --end 2021-12-31 `
  --benchmark-config config\us_etf_benchmark.toml
```

### 4. 마지막에 한 번만 — Out-of-sample

```powershell
.\.venv\Scripts\python.exe -m tradingbot --config config\us_etf_rotation.toml `
  research evaluate --strategy theme_multifactor --market US `
  --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ `
  --start 2022-01-01 `
  --benchmark-config config\us_etf_benchmark.toml
```

**여기서 실패하면 파라미터를 다시 고르는 것이 아니라 가설을 버린다.** OOS를
두 번 보는 순간 그것은 더 이상 OOS가 아니다.

### 참고: 전 구간 재측정

기존 판정표(2026-07-31)와 직접 비교하려면 같은 창으로:

```powershell
.\.venv\Scripts\python.exe -m tradingbot --config config\us_etf_rotation.toml `
  research evaluate --strategy theme_multifactor --market US `
  --symbols SPY QQQ IWM EFA EEM TLT IEF LQD GLD DBC VNQ `
  --start 2007-01-01 --benchmark-config config\us_etf_benchmark.toml
```

이 수치는 "전 구간에 걸쳐 지금 파라미터를 알고 있었다면"이므로 승격 근거로는
3·4단계보다 약하다. 비교용으로만 읽는다.

## 무엇을 재지 못했는가

이 세션의 환경에서는 **시세 데이터를 받을 수 없었다.** 네트워크 정책이
yahoo·stooq·FRED 등 모든 시세 소스를 차단하고 GitHub과 패키지 저장소만
허용한다(`fc.yahoo.com:443` CONNECT 403 확인). `data/cache`는 gitignore라
저장소에도 없다.

따라서 위 네 가지 변경의 **실데이터 효과는 전부 미측정**이다. 확인한 것은
fixture 기반 pytest 699개와, 합성 데이터로 잰 메커니즘 두 가지뿐이다:

| 검증 | 방법 | 결과 |
|---|---|---|
| 현금 버퍼 수정 | 합성 로테이션 | 거부 12/65(18.5%) → 0/68 |
| 방어 전환 수정 | 합성 하락장 | MDD −35.36% → −12.13% |

두 합성 수치 모두 **크기는 fixture의 산물**이다. 실데이터에서 같은 크기가
나오리라는 근거가 아니라, 메커니즘이 작동하게 됐다는 근거로만 읽어야 한다.
18.5%가 실측 17~19%와 맞아떨어진 것은 재현이 실제 메커니즘을 잡았다는
방증이지만, 그 이상은 아니다.

## 승격 가능성에 대한 정직한 전망

기존 판정표는 6개 중 3개 미달이었다(초과수익 −2.31%p, walk-forward 승률 0.29,
비용 2배 −2.66%p). 이번 변경이 어디를 건드리는지:

- **초과수익**: 1·3번이 도움이 되는 방향이다. 다만 1번은 벤치마크(196건 거부)를
  전략(130건)보다 더 많이 고쳐 주므로, **격차가 오히려 벌어질 수도 있다.**
  방향을 예단할 수 없다.
- **walk-forward 승률 0.29**: 17년 중 12년을 졌다. 주문 거부 잡음이 이걸
  전부 설명하기는 어렵다. 가장 회의적으로 봐야 할 항목이다.
- **비용 2배**: 회전율이 2.74로 낮아 비용 민감도 자체는 크지 않다. 초과수익이
  개선되면 따라온다.

솔직한 평가: **이번 변경만으로 승격될 가능성은 낮다.** 1·2번은 "측정이
믿을 만해졌다"는 것이지 "전략이 좋아졌다"가 아니다. 진짜 관문은 1단계다 —
팩터가 게이트를 통과하지 못하면 그 위에 무엇을 얹어도 이유가 없다.
