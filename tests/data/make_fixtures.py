"""회귀 테스트용 고정 fixture 생성기.

시드 고정 랜덤워크로 KR 스타일 일봉 2종목(TESTA, TESTB) × 2년(2020~2021)을 만들어
tests/data/KR/*.parquet 으로 저장한다. 이 파일들은 git에 커밋되며, 테스트는 네트워크
없이 이 캐시만 읽는다. 재생성하면 test_smoke_backtest.py의 고정 자산값도 갱신해야 한다.

실행: python tests/data/make_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DATA_ROOT = Path(__file__).resolve().parent
SPECS = {
    "TESTA": {"seed": 20200101, "start_price": 60_000.0, "mu": 0.0004, "sigma": 0.018},
    "TESTB": {"seed": 20200102, "start_price": 15_000.0, "mu": -0.0001, "sigma": 0.025},
}


def make_symbol_frame(seed: int, start_price: float, mu: float, sigma: float) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", "2021-12-31")
    n = len(dates)

    log_returns = rng.normal(mu, sigma, size=n)
    closes = start_price * np.exp(np.cumsum(log_returns))
    gaps = rng.normal(0.0, 0.004, size=n)
    opens = np.concatenate([[start_price], closes[:-1]]) * (1 + gaps)
    spans = np.abs(rng.normal(0.0, 0.008, size=n))
    highs = np.maximum(opens, closes) * (1 + spans)
    lows = np.minimum(opens, closes) * (1 - spans)
    volumes = rng.integers(50_000, 500_000, size=n).astype(float)

    return pd.DataFrame(
        {
            "open": np.round(opens).astype(float),
            "high": np.round(highs).astype(float),
            "low": np.round(lows).astype(float),
            "close": np.round(closes).astype(float),
            "volume": volumes,
        },
        index=dates,
    )


def main() -> None:
    out_dir = DATA_ROOT / "KR"
    out_dir.mkdir(parents=True, exist_ok=True)
    for symbol, spec in SPECS.items():
        df = make_symbol_frame(**spec)
        path = out_dir / f"{symbol}.parquet"
        df.to_parquet(path)
        print(f"{symbol}: {len(df)} rows, close {df['close'].iloc[0]:,.0f} -> {df['close'].iloc[-1]:,.0f} -> {path}")


if __name__ == "__main__":
    main()
