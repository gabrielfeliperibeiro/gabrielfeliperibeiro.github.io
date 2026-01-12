"""
Price Aggregator - Combines feeds from multiple exchanges
Detects price discrepancies for latency arbitrage
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Any
from collections import deque

from loguru import logger

from .base import ExchangeBase, PriceUpdate


@dataclass
class AggregatedPrice:
    """Price aggregated from multiple exchanges"""
    symbol: str
    timestamp: datetime
    exchanges: Dict[str, PriceUpdate] = field(default_factory=dict)

    @property
    def avg_price(self) -> float:
        """Volume-weighted average price across exchanges"""
        if not self.exchanges:
            return 0
        return sum(p.price for p in self.exchanges.values()) / len(self.exchanges)

    @property
    def best_bid(self) -> tuple:
        """Best bid price and exchange"""
        if not self.exchanges:
            return (0, None)
        best = max(self.exchanges.items(), key=lambda x: x[1].bid)
        return (best[1].bid, best[0])

    @property
    def best_ask(self) -> tuple:
        """Best ask price and exchange"""
        if not self.exchanges:
            return (float('inf'), None)
        best = min(self.exchanges.items(), key=lambda x: x[1].ask)
        return (best[1].ask, best[0])

    @property
    def price_spread(self) -> float:
        """Max price difference between exchanges"""
        if len(self.exchanges) < 2:
            return 0
        prices = [p.price for p in self.exchanges.values()]
        return max(prices) - min(prices)

    @property
    def spread_pct(self) -> float:
        """Price spread as percentage"""
        avg = self.avg_price
        if avg == 0:
            return 0
        return self.price_spread / avg


@dataclass
class PriceImpulse:
    """Detected price impulse/movement"""
    symbol: str
    exchange: str
    direction: str  # 'up' or 'down'
    change_pct: float
    from_price: float
    to_price: float
    duration_ms: float
    timestamp: datetime
    confidence: float = 1.0  # Higher if multiple exchanges confirm


class PriceAggregator:
    """
    Aggregates price feeds from multiple exchanges
    Detects arbitrage opportunities and price impulses
    """

    def __init__(self):
        self._exchanges: Dict[str, ExchangeBase] = {}
        self._prices: Dict[str, AggregatedPrice] = {}
        self._impulse_callbacks: List[Callable] = []
        self._arb_callbacks: List[Callable] = []
        self._running = False

        # Impulse detection settings
        self._impulse_threshold = 0.02  # 2% default
        self._impulse_window_ms = 60000  # 1 minute
        self._price_history: Dict[str, deque] = {}
        self._history_size = 1000

    def add_exchange(self, exchange: ExchangeBase):
        """Add exchange to aggregator"""
        self._exchanges[exchange.name] = exchange
        exchange.add_callback(self._on_price_update)
        logger.info(f"Added exchange: {exchange.name}")

    def remove_exchange(self, name: str):
        """Remove exchange from aggregator"""
        if name in self._exchanges:
            exchange = self._exchanges.pop(name)
            exchange.remove_callback(self._on_price_update)

    def add_impulse_callback(self, callback: Callable[[PriceImpulse], Any]):
        """Add callback for price impulse detection"""
        self._impulse_callbacks.append(callback)

    def add_arbitrage_callback(self, callback: Callable[[Dict], Any]):
        """Add callback for arbitrage opportunities"""
        self._arb_callbacks.append(callback)

    def set_impulse_threshold(self, threshold_pct: float, window_ms: int = 60000):
        """Configure impulse detection parameters"""
        self._impulse_threshold = threshold_pct
        self._impulse_window_ms = window_ms

    async def connect_all(self):
        """Connect to all exchanges"""
        self._running = True
        tasks = [ex.connect() for ex in self._exchanges.values()]
        await asyncio.gather(*tasks)
        logger.info(f"Connected to {len(self._exchanges)} exchanges")

    async def disconnect_all(self):
        """Disconnect from all exchanges"""
        self._running = False
        tasks = [ex.disconnect() for ex in self._exchanges.values()]
        await asyncio.gather(*tasks)

    async def subscribe_symbol(self, symbol: str):
        """Subscribe to price updates for symbol on all exchanges"""
        for exchange in self._exchanges.values():
            if symbol in exchange.supported_symbols:
                await exchange.subscribe_price(symbol)
                logger.debug(f"Subscribed to {symbol} on {exchange.name}")

    async def unsubscribe_symbol(self, symbol: str):
        """Unsubscribe from symbol on all exchanges"""
        for exchange in self._exchanges.values():
            await exchange.unsubscribe_price(symbol)

    async def _on_price_update(self, update: PriceUpdate):
        """Handle price update from any exchange"""
        symbol = update.symbol

        # Update aggregated price
        if symbol not in self._prices:
            self._prices[symbol] = AggregatedPrice(
                symbol=symbol,
                timestamp=update.timestamp
            )

        self._prices[symbol].exchanges[update.exchange] = update
        self._prices[symbol].timestamp = update.timestamp

        # Update history
        if symbol not in self._price_history:
            self._price_history[symbol] = deque(maxlen=self._history_size)
        self._price_history[symbol].append({
            'timestamp': update.timestamp,
            'exchange': update.exchange,
            'price': update.price
        })

        # Check for impulses
        await self._check_impulse(symbol, update)

        # Check for cross-exchange arbitrage
        await self._check_exchange_arbitrage(symbol)

    async def _check_impulse(self, symbol: str, update: PriceUpdate):
        """Check if price update represents an impulse"""
        history = list(self._price_history.get(symbol, []))
        if len(history) < 2:
            return

        # Get price from window_ms ago
        window_start = update.timestamp - timedelta(milliseconds=self._impulse_window_ms)

        old_price = None
        for entry in history:
            if entry['timestamp'] <= window_start:
                old_price = entry['price']
                break

        if old_price is None:
            old_price = history[0]['price']

        change_pct = (update.price - old_price) / old_price

        if abs(change_pct) >= self._impulse_threshold:
            impulse = PriceImpulse(
                symbol=symbol,
                exchange=update.exchange,
                direction='up' if change_pct > 0 else 'down',
                change_pct=change_pct,
                from_price=old_price,
                to_price=update.price,
                duration_ms=self._impulse_window_ms,
                timestamp=update.timestamp
            )

            # Check if other exchanges confirm
            confirmed = self._check_impulse_confirmation(symbol, change_pct)
            impulse.confidence = confirmed / len(self._exchanges) if self._exchanges else 1

            logger.info(f"Impulse detected: {symbol} {impulse.direction} {change_pct:.2%} "
                       f"(confidence: {impulse.confidence:.0%})")

            # Notify callbacks
            for callback in self._impulse_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(impulse)
                    else:
                        callback(impulse)
                except Exception as e:
                    logger.error(f"Impulse callback error: {e}")

    def _check_impulse_confirmation(self, symbol: str, direction_pct: float) -> int:
        """Count how many exchanges confirm the impulse direction"""
        confirmed = 0
        direction = 1 if direction_pct > 0 else -1

        for ex_name, exchange in self._exchanges.items():
            momentum = exchange.calculate_momentum(symbol, 50)
            if momentum['direction'] == ('up' if direction > 0 else 'down'):
                confirmed += 1

        return confirmed

    async def _check_exchange_arbitrage(self, symbol: str):
        """Check for arbitrage between exchanges"""
        agg = self._prices.get(symbol)
        if not agg or len(agg.exchanges) < 2:
            return

        best_bid, bid_exchange = agg.best_bid
        best_ask, ask_exchange = agg.best_ask

        # Arbitrage exists if we can buy on one exchange and sell on another for profit
        if best_bid > best_ask and bid_exchange != ask_exchange:
            profit_pct = (best_bid - best_ask) / best_ask

            if profit_pct > 0.001:  # 0.1% minimum
                opportunity = {
                    'type': 'EXCHANGE_ARBITRAGE',
                    'symbol': symbol,
                    'buy_exchange': ask_exchange,
                    'buy_price': best_ask,
                    'sell_exchange': bid_exchange,
                    'sell_price': best_bid,
                    'profit_pct': profit_pct,
                    'timestamp': agg.timestamp
                }

                logger.info(f"Exchange arbitrage: {symbol} buy@{ask_exchange} "
                           f"sell@{bid_exchange} profit={profit_pct:.2%}")

                for callback in self._arb_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(opportunity)
                        else:
                            callback(opportunity)
                    except Exception as e:
                        logger.error(f"Arbitrage callback error: {e}")

    def get_aggregated_price(self, symbol: str) -> Optional[AggregatedPrice]:
        """Get current aggregated price for symbol"""
        return self._prices.get(symbol)

    def get_best_price(self, symbol: str, side: str) -> Optional[tuple]:
        """
        Get best price across all exchanges

        Args:
            symbol: Trading pair
            side: 'BUY' or 'SELL'

        Returns:
            (price, exchange_name) tuple
        """
        agg = self._prices.get(symbol)
        if not agg:
            return None

        if side == 'BUY':
            return agg.best_ask
        else:
            return agg.best_bid

    def get_price_deviation(self, symbol: str) -> Dict:
        """
        Get price deviation statistics across exchanges

        Returns:
            Dict with min, max, avg, spread for the symbol
        """
        agg = self._prices.get(symbol)
        if not agg or not agg.exchanges:
            return {'min': 0, 'max': 0, 'avg': 0, 'spread': 0, 'spread_pct': 0}

        prices = [p.price for p in agg.exchanges.values()]

        return {
            'min': min(prices),
            'max': max(prices),
            'avg': sum(prices) / len(prices),
            'spread': max(prices) - min(prices),
            'spread_pct': agg.spread_pct,
            'exchanges': {name: p.price for name, p in agg.exchanges.items()}
        }

    async def monitor_btc_for_polymarket(
        self,
        impulse_callback: Callable[[PriceImpulse], Any],
        threshold_pct: float = 0.02
    ):
        """
        Start monitoring BTC price for Polymarket latency arbitrage

        When BTC moves significantly on exchanges, Polymarket prediction
        markets may lag behind, creating arbitrage opportunities.

        Args:
            impulse_callback: Called when impulse detected
            threshold_pct: Minimum price change threshold
        """
        self.set_impulse_threshold(threshold_pct)
        self.add_impulse_callback(impulse_callback)

        await self.connect_all()
        await self.subscribe_symbol("BTCUSDT")

        logger.info(f"Monitoring BTC for Polymarket arbitrage (threshold: {threshold_pct:.1%})")

        # Keep running
        while self._running:
            await asyncio.sleep(1)
