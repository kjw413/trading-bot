from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol

from tradingbot.engine.clock import TradingSessionClock
from tradingbot.models import PriceTick


class PriceFetcher(Protocol):
    def __call__(self, market: str, symbols: list[str]) -> dict[str, float]:
        ...


class YFinancePriceFetcher:
    def __call__(self, market: str, symbols: list[str]) -> dict[str, float]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("yfinance is required for polling prices") from exc

        tickers = {symbol.upper(): _to_yfinance_ticker(market, symbol) for symbol in symbols}
        prices: dict[str, float] = {}
        for symbol, ticker in tickers.items():
            fast_info = yf.Ticker(ticker).fast_info
            price = fast_info.get("last_price") or fast_info.get("regular_market_price")
            if price is None:
                history = yf.Ticker(ticker).history(period="1d", interval="1m", auto_adjust=True)
                if history.empty:
                    continue
                price = float(history["Close"].dropna().iloc[-1])
            prices[symbol] = float(price)
        return prices


def _to_yfinance_ticker(market: str, symbol: str) -> str:
    symbol = symbol.upper()
    if market.upper() == "KR" and "." not in symbol:
        return f"{symbol}.KS"
    return symbol


class PollingDataFeed:
    def __init__(
        self,
        market: str,
        symbols: list[str],
        clock: TradingSessionClock,
        price_fetcher: PriceFetcher | None = None,
    ) -> None:
        self.market = market.upper()
        self.symbols = [symbol.upper() for symbol in symbols]
        self.clock = clock
        self.price_fetcher = price_fetcher or YFinancePriceFetcher()
        self.last_poll_at: datetime | None = None

    def fetch_prices(self) -> dict[str, float]:
        prices = self.price_fetcher(self.market, self.symbols)
        return {symbol.upper(): float(price) for symbol, price in prices.items()}

    def poll(self, now: datetime | None = None) -> PriceTick | None:
        current = self.clock.localize(now) if now is not None else self.clock.now()
        if not self.clock.should_poll(self.last_poll_at, current):
            return None
        prices = self.fetch_prices()
        if not prices:
            return None
        self.last_poll_at = current
        return PriceTick(dt=current.date(), prices=prices)
