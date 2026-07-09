from __future__ import annotations

from datetime import datetime
from typing import Protocol

from tradingbot.engine.clock import TradingSessionClock
from tradingbot.models import PriceTick
from tradingbot.utils.log import get_logger

LOGGER = get_logger(__name__)


class PriceFetcher(Protocol):
    def __call__(self, market: str, symbols: list[str]) -> dict[str, float]:
        ...


class YFinancePriceFetcher:
    def __init__(self) -> None:
        self._ticker_cache: dict[tuple[str, str], str] = {}

    def __call__(self, market: str, symbols: list[str]) -> dict[str, float]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("yfinance is required for polling prices") from exc

        market = market.upper()
        prices: dict[str, float] = {}
        for raw_symbol in symbols:
            symbol = raw_symbol.upper()
            for ticker in self._ticker_candidates(market, symbol):
                price = self._fetch_ticker_price(yf, ticker)
                if price is None:
                    continue
                self._ticker_cache[(market, symbol)] = ticker
                prices[symbol] = float(price)
                break
            if symbol not in prices:
                LOGGER.warning("No polling price found for %s %s", market, symbol)
        return prices

    def _ticker_candidates(self, market: str, symbol: str) -> list[str]:
        candidates: list[str] = []
        cached = self._ticker_cache.get((market, symbol))
        if cached:
            candidates.append(cached)
        candidates.extend(_yfinance_ticker_candidates(market, symbol))
        return list(dict.fromkeys(candidates))

    def _fetch_ticker_price(self, yf, ticker: str) -> float | None:
        try:
            ticker_obj = yf.Ticker(ticker)
        except Exception:
            LOGGER.debug("Failed to create yfinance ticker %s", ticker, exc_info=True)
            return None

        try:
            price = _fast_info_price(ticker_obj.fast_info)
            if price is not None:
                return price
        except Exception:
            LOGGER.debug("Failed to read yfinance fast_info for %s", ticker, exc_info=True)

        try:
            history = ticker_obj.history(period="1d", interval="1m", auto_adjust=True)
            if history.empty:
                return None
            closes = history["Close"].dropna()
            if closes.empty:
                return None
            return float(closes.iloc[-1])
        except Exception:
            LOGGER.debug("Failed to read yfinance intraday history for %s", ticker, exc_info=True)
            return None


def _fast_info_price(fast_info) -> float | None:
    for key in ("last_price", "regular_market_price"):
        value = None
        if hasattr(fast_info, "get"):
            value = fast_info.get(key)
        else:
            try:
                value = fast_info[key]
            except (KeyError, TypeError):
                value = None
        if value is None:
            continue
        price = float(value)
        if price == price:
            return price
    return None


def _to_yfinance_ticker(market: str, symbol: str) -> str:
    return _yfinance_ticker_candidates(market, symbol)[0]


def _yfinance_ticker_candidates(market: str, symbol: str) -> list[str]:
    symbol = symbol.upper()
    if market.upper() == "KR" and "." not in symbol:
        return [f"{symbol}.KS", f"{symbol}.KQ"]
    return [symbol]


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
