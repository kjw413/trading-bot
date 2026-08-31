# IC IR 0.30 게이트 도달 가능성 검토 (2026-08-31)

## 결론: 현재 게이트는 도달 불가능하며, 애초에 판정할 수도 없다

`config/research.toml`의 팩터 게이트는 IC IR 0.30을 요구한다. P1·P2·P3에서
유니버스·팩터·포트폴리오 구성을 차례로 바꿨으나 IR은 0.13~0.21 범위를 벗어난
적이 없다. 그래서 "무엇을 더 바꿔야 0.30에 닿는가"가 아니라 **"0.30에 닿을 수
있는가"**를 먼저 물었다.

답은 두 가지 독립적인 이유로 아니오다.

1. **천장이 게이트보다 낮다.** 표본 노이즈를 완전히 제거해도(= 무한히 큰
   독립 횡단면) 최선의 조합이 IR 0.273이다. 0.30은 그 위에 있다.
2. **0.30인지 아닌지를 판정할 정밀도가 없다.** IR 추정치의 표준오차가 0.084라
   95% 신뢰구간이 게이트를 포함한다. "0.18 < 0.30이므로 탈락"은 0.30과 구분되지
   않는 수치에 내린 판정이다.

## 방법

각 날짜 t의 IC는 횡단면 순위상관이다. 관측된 IC의 시계열 분산은 두 성분으로
나뉜다.

    Var(IC_관측) = Var(IC_참값의 시간변동) + Var(표본추출 오차)

n개 종목의 Spearman IC 표본추출 분산은 대략 1/(n-1)이다. 날짜별 실제 사용
종목 수로 이 값을 평균해 빼면 신호 자체의 변동성이 남는다.

**천장**은 표본추출 오차가 0일 때의 IR, 즉 `mean(IC) / sd(IC_참값)`이다.
횡단면을 아무리 키워도 이 값을 넘을 수 없다.

**정밀도**는 `se(IR) ≈ sqrt((1 + IR²/2) / 관측수)`로 계산했다.

구간은 `config/research.toml`의 in_sample(2007-01-01~2018-12-31), 월말 144개
시점, horizon 20거래일. 재현 코드는 이 문서 말미에 있다.

## 결과

| 레이어 | 팩터 | mean IC | IR | 노이즈 비중 | **천장** | IR 95% 신뢰구간 |
|---|---|---|---|---|---|---|
| 참조 11 | momentum_3m | 0.1068 | 0.212 | 40% | **0.273** | [0.047, 0.377] |
| 참조 11 | momentum_6m | 0.0904 | 0.182 | 40% | **0.236** | [0.017, 0.347] |
| 판별 16 | momentum_3m | 0.0637 | 0.133 | 29% | 0.158 | [-0.031, 0.297] |
| 판별 16 | momentum_6m | 0.0839 | 0.182 | 32% | 0.221 | [0.018, 0.347] |
| 후보 25 | momentum_3m | 0.0589 | 0.136 | 22% | 0.155 | [-0.028, 0.300] |
| 후보 25 | momentum_6m | 0.0626 | 0.146 | 23% | 0.165 | [0.019, 0.310] |

## 읽는 법

**P1은 절반만 옳았다.** 종목을 11개에서 25개로 늘리자 날짜별 표본추출 표준편차는
0.316에서 0.204로 실제로 줄었다 — 사전등록이 예측한 그대로다. 그런데 mean IC가
0.090에서 0.063으로 더 빠르게 떨어져서 IR은 오히려 내려갔다. 노이즈는 줄었지만
신호가 더 많이 희석됐다. 천장도 0.236에서 0.165로 함께 내려갔으므로, 확장은
측정을 개선하면서 측정 대상을 악화시켰다.

**진짜 제약은 신호의 불안정성이다.** 어느 레이어에서든 참값 IC의 시간변동
표준편차가 0.38~0.40이다. mean IC는 0.06~0.11에 불과하다. 예측력이 평균적으로
존재하지만 달마다 그 4배 크기로 출렁인다. 이 비율이 IR의 천장을 결정하며,
유니버스 크기로는 건드릴 수 없다.

**게이트는 판정 도구로 기능하지 못한다.** se(IR)=0.084는 144개 월말 관측에서
나온다. IR을 ±0.05로 재려면 약 1,600개 관측, 즉 130년치 월말 데이터가 필요하다.
현재 표본으로는 0.18과 0.30을 통계적으로 구분할 수 없다.

## 함의

바꿔야 할 것은 유니버스도 팩터도 아니다.

- **게이트 값**: 0.30은 500종목 이상 주식 횡단면 연구의 관례적 수치로 보인다.
  11~25개 자산 ETF 유니버스에 그대로 적용할 근거가 없다. 이 자산군에서 달성
  가능한 범위를 기준으로 다시 정해야 한다.
- **horizon**: 20거래일 예측의 IC 변동이 지나치게 크다. 더 긴 horizon은 통상
  IC가 안정적이므로 천장 자체를 올릴 수 있다. 이건 측정해봐야 안다.
- **판정 방식**: 점추정치를 임계값과 비교하는 대신 신뢰구간을 보고해야 한다.
  현재 방식은 노이즈에 대고 합격/불합격을 선언한다.

어느 쪽도 이 문서가 단독으로 결정하지 않는다. 셋 다 사전등록 후 측정 대상이다.

## 재현

```python
# PYTHONIOENCODING=utf-8 python this.py
import numpy as np, pandas as pd
from tradingbot.research.dates import month_end_trading_days, research_period
from tradingbot.research.gate import load_gate_thresholds, load_research_config
from tradingbot.research.report import MembershipAwareFactor
from tradingbot.research.labels import forward_returns
from tradingbot.data.universe import get_theme, members as theme_members
from tradingbot.data.store import ParquetDataStore
from tradingbot.data.cache import ParquetCache
from tradingbot.config import resolve_project_path
from tradingbot.factors.registry import get_factor

research = load_research_config(None); th = load_gate_thresholds(research)
start, end = research_period(research, "in_sample")
for layer in ["us_asset_rotation_reference", "us_asset_rotation_distinct", "us_asset_rotation"]:
    theme = get_theme(layer)
    universe = theme_members(theme, end)
    store = ParquetDataStore(ParquetCache(resolve_project_path("data/cache")), theme.market,
                             processed_root=resolve_project_path("data/processed"))
    dates = month_end_trading_days(theme.market, start, end)
    print(f"\n===== {layer}  (N={len(universe)}) =====")
    for fname in ["momentum_3m", "momentum_6m"]:
        f = MembershipAwareFactor(get_factor(fname), lambda d, t=theme: theme_members(t, d))
        ics, sizes = [], []
        for dt in dates:
            fr = pd.concat([f.compute(dt, universe, store),
                            forward_returns(store, universe, dt, th.horizon_days)],
                           axis=1, join="inner").dropna()
            if len(fr) < 3 or fr.iloc[:, 0].nunique() < 2 or fr.iloc[:, 1].nunique() < 2:
                continue
            ics.append(float(fr.corr(method="spearman").iloc[0, 1])); sizes.append(len(fr))
        ics = pd.Series(ics).dropna()
        mean, sd, n = ics.mean(), ics.std(ddof=1), len(ics)
        ir = mean / sd
        var_samp = float(np.mean([1.0 / (m - 1) for m in sizes]))
        true_sd = np.sqrt(max(0.0, sd**2 - var_samp))
        se_ir = np.sqrt((1 + ir**2 / 2) / n)
        print(f"  {fname}: mean_IC={mean:.4f} IR={ir:.4f} noise_share={var_samp/sd**2:.0%}"
              f" ceiling={mean/true_sd:.3f} CI=[{ir-1.96*se_ir:.3f}, {ir+1.96*se_ir:.3f}]")
```

## 한계

표본추출 분산 근사 1/(n-1)은 참 상관이 0에 가까울 때 정확하다. 여기서는 mean IC가
0.06~0.11로 작아 근사가 무난하지만, 천장 값은 대략적인 수치로 읽어야 한다.
0.273과 0.30의 간격은 이 근사 오차보다 크다고 보기 어렵다 — 즉 "천장이 게이트보다
확실히 낮다"보다는 **"천장과 게이트가 구분되지 않는다"**가 더 정확한 진술이며,
어느 쪽이든 0.30을 목표로 삼는 근거가 되지는 못한다.
