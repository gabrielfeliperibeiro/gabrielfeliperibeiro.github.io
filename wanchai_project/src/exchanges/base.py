"""
Base exchange connector interface
All exchange connectors should inherit from this class
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Any
from collections import deque
import asyncio

from loguru import logger


@dataclass
class PriceUpdate:
    """Real-time price update from exchange"""
    exchange: str
    symbol: str
    price: float
    bid: float
    ask: float
    volume_24h: float
    timestamp: datetime
    raw_data: Dict = field(default_factory=dict)

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_pct(self) -> float:
        mid = (self.bid + self.ask) / 2
        return self.spread / mid if mid > 0 else 0

    @property
    def mid_price(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass
class OHLCV:
    """OHLCV candlestick data"""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class ExchangeBase(ABC):
    """
    Abstract base class for exchange connectors
    Provides interface for price feeds and market data
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = False
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self._running = False
        self._callbacks: List[Callable] = []
        self._price_cache: Dict[str, PriceUpdate] = {}
        self._price_history: Dict[str, deque] = {}
        self._history_size = 1000

    @property
    @abstractmethod
    def name(self) -> str:
        """Exchange name"""
        pass

    @property
    @abstractmethod
    def supported_symbols(self) -> List[str]:
        """List of supported trading pairs"""
        pass

    @abstractmethod
    async def connect(self):
        """Connect to exchange"""
        pass

    @abstractmethod
    async def disconnect(self):
        """Disconnect from exchange"""
        pass

    @abstractmethod
    async def get_price(self, symbol: str) -> PriceUpdate:
        """Get current price for symbol"""
        pass

    @abstractmethod
    async def subscribe_price(self, symbol: str):
        """Subscribe to real-time price updates"""
        pass

    @abstractmethod
    async def unsubscribe_price(self, symbol: str):
        """Unsubscribe from price updates"""
        pass

    @abstractmethod
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100
    ) -> List[OHLCV]:
        """Get historical OHLCV data"""
        pass

    def add_callback(self, callback: Callable[[PriceUpdate], Any]):
        """Add callback for price updates"""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable):
        """Remove callback"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def _notify_callbacks(self, update: PriceUpdate):
        """Notify all callbacks of price update"""
        # Update cache
        self._price_cache[update.symbol] = update

        # Update history
        if update.symbol not in self._price_history:
            self._price_history[update.symbol] = deque(maxlen=self._history_size)
        self._price_history[update.symbol].append(update)

        # Notify callbacks
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(update)
                else:
                    callback(update)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def get_cached_price(self, symbol: str) -> Optional[PriceUpdate]:
        """Get cached price for symbol"""
        return self._price_cache.get(symbol)

    def get_price_history(self, symbol: str, limit: int = 100) -> List[PriceUpdate]:
        """Get recent price history"""
        history = self._price_history.get(symbol, deque())
        return list(history)[-limit:]

    def calculate_volatility(self, symbol: str, lookback: int = 100) -> float:
        """Calculate price volatility from recent history"""
        history = self.get_price_history(symbol, lookback)
        if len(history) < 2:
            return 0

        prices = [u.price for u in history]
        avg = sum(prices) / len(prices)
        variance = sum((p - avg) ** 2 for p in prices) / len(prices)
        return variance ** 0.5

    def calculate_momentum(self, symbol: str, lookback: int = 100) -> Dict:
        """Calculate price momentum"""
        history = self.get_price_history(symbol, lookback)
        if len(history) < 2:
            return {'direction': 'neutral', 'strength': 0, 'change_pct': 0}

        first_price = history[0].price
        last_price = history[-1].price

        change = last_price - first_price
        change_pct = change / first_price if first_price > 0 else 0

        direction = 'up' if change > 0 else 'down' if change < 0 else 'neutral'
        volatility = self.calculate_volatility(symbol, lookback)
        strength = abs(change_pct) / (volatility + 0.0001)

        return {
            'direction': direction,
            'strength': strength,
            'change': change,
            'change_pct': change_pct,
            'volatility': volatility
        }

    def detect_price_impulse(
        self,
        symbol: str,
        threshold_pct: float = 0.02,
        window: int = 10
    ) -> Optional[Dict]:
        """
        Detect sudden price movements (impulses)
        Used for latency arbitrage strategy

        Args:
            symbol: Trading pair
            threshold_pct: Minimum price change to consider as impulse
            window: Number of recent updates to analyze

        Returns:
            Dict with impulse details or None
        """
        history = self.get_price_history(symbol, window)
        if len(history) < 2:
            return None

        first_price = history[0].price
        last_price = history[-1].price

        change_pct = (last_price - first_price) / first_price

        if abs(change_pct) >= threshold_pct:
            return {
                'symbol': symbol,
                'direction': 'up' if change_pct > 0 else 'down',
                'change_pct': change_pct,
                'from_price': first_price,
                'to_price': last_price,
                'window_ms': (history[-1].timestamp - history[0].timestamp).total_seconds() * 1000,
                'timestamp': history[-1].timestamp
            }

        return None
