"""
Orderbook management and analysis for Polymarket
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import deque

import websockets
import json
from loguru import logger


@dataclass
class OrderLevel:
    """Single price level in orderbook"""
    price: float
    size: float
    order_count: int = 1


@dataclass
class OrderBookSnapshot:
    """Point-in-time orderbook snapshot"""
    token_id: str
    timestamp: datetime
    bids: List[OrderLevel]
    asks: List[OrderLevel]

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 1.0

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def spread_pct(self) -> float:
        if self.mid_price == 0:
            return 0
        return self.spread / self.mid_price

    @property
    def bid_depth(self) -> float:
        """Total bid liquidity"""
        return sum(level.size for level in self.bids)

    @property
    def ask_depth(self) -> float:
        """Total ask liquidity"""
        return sum(level.size for level in self.asks)

    @property
    def imbalance(self) -> float:
        """Order book imbalance (-1 to 1)"""
        total = self.bid_depth + self.ask_depth
        if total == 0:
            return 0
        return (self.bid_depth - self.ask_depth) / total


class OrderBook:
    """
    Real-time orderbook with WebSocket updates
    Provides analysis tools for arbitrage detection
    """

    def __init__(
        self,
        token_id: str,
        websocket_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    ):
        self.token_id = token_id
        self.websocket_url = websocket_url
        self._bids: Dict[float, OrderLevel] = {}
        self._asks: Dict[float, OrderLevel] = {}
        self._ws = None
        self._running = False
        self._last_update = datetime.now()
        self._price_history: deque = deque(maxlen=1000)
        self._callbacks: List[callable] = []

    def add_callback(self, callback: callable):
        """Add callback for orderbook updates"""
        self._callbacks.append(callback)

    def remove_callback(self, callback: callable):
        """Remove callback"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def connect(self):
        """Connect to WebSocket and start receiving updates"""
        self._running = True

        while self._running:
            try:
                async with websockets.connect(self.websocket_url) as ws:
                    self._ws = ws

                    # Subscribe to market updates
                    subscribe_msg = {
                        "type": "subscribe",
                        "channel": "market",
                        "assets_ids": [self.token_id]
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info(f"Subscribed to orderbook updates for {self.token_id}")

                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_message(json.loads(message))

            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket connection closed, reconnecting...")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await asyncio.sleep(5)

    async def disconnect(self):
        """Disconnect from WebSocket"""
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _handle_message(self, message: Dict):
        """Process incoming WebSocket message"""
        msg_type = message.get('type', '')

        if msg_type == 'book':
            await self._update_book(message)
        elif msg_type == 'price_change':
            await self._handle_price_change(message)
        elif msg_type == 'trade':
            await self._handle_trade(message)

    async def _update_book(self, data: Dict):
        """Update orderbook from snapshot/delta"""
        bids = data.get('bids', [])
        asks = data.get('asks', [])

        # Update bids
        for bid in bids:
            price = float(bid['price'])
            size = float(bid['size'])
            if size == 0:
                self._bids.pop(price, None)
            else:
                self._bids[price] = OrderLevel(price=price, size=size)

        # Update asks
        for ask in asks:
            price = float(ask['price'])
            size = float(ask['size'])
            if size == 0:
                self._asks.pop(price, None)
            else:
                self._asks[price] = OrderLevel(price=price, size=size)

        self._last_update = datetime.now()

        # Record price history
        snapshot = self.get_snapshot()
        self._price_history.append({
            'timestamp': snapshot.timestamp,
            'mid': snapshot.mid_price,
            'bid': snapshot.best_bid,
            'ask': snapshot.best_ask
        })

        # Trigger callbacks
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(snapshot)
                else:
                    callback(snapshot)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    async def _handle_price_change(self, data: Dict):
        """Handle price change notification"""
        logger.debug(f"Price change: {data}")

    async def _handle_trade(self, data: Dict):
        """Handle trade execution notification"""
        logger.debug(f"Trade executed: {data}")

    def get_snapshot(self) -> OrderBookSnapshot:
        """Get current orderbook snapshot"""
        sorted_bids = sorted(self._bids.values(), key=lambda x: x.price, reverse=True)
        sorted_asks = sorted(self._asks.values(), key=lambda x: x.price)

        return OrderBookSnapshot(
            token_id=self.token_id,
            timestamp=self._last_update,
            bids=sorted_bids,
            asks=sorted_asks
        )

    def get_price_impact(self, side: str, size: float) -> Tuple[float, float]:
        """
        Calculate price impact for a given order size

        Args:
            side: 'BUY' or 'SELL'
            size: Order size in shares

        Returns:
            Tuple of (avg_fill_price, price_impact_pct)
        """
        snapshot = self.get_snapshot()

        if side == 'BUY':
            levels = snapshot.asks
        else:
            levels = snapshot.bids

        if not levels:
            return (0, 0)

        remaining = size
        total_cost = 0

        for level in levels:
            fill_size = min(remaining, level.size)
            total_cost += fill_size * level.price
            remaining -= fill_size

            if remaining <= 0:
                break

        if size - remaining <= 0:
            return (levels[0].price, 0)

        avg_price = total_cost / (size - remaining)
        impact = abs(avg_price - levels[0].price) / levels[0].price

        return (avg_price, impact)

    def detect_spread_opportunity(self, min_spread: float = 0.02) -> Optional[Dict]:
        """
        Detect if spread is wide enough for market making

        Args:
            min_spread: Minimum spread percentage (default 2%)

        Returns:
            Dict with spread details or None
        """
        snapshot = self.get_snapshot()

        if snapshot.spread_pct >= min_spread:
            return {
                'bid': snapshot.best_bid,
                'ask': snapshot.best_ask,
                'spread': snapshot.spread,
                'spread_pct': snapshot.spread_pct,
                'mid': snapshot.mid_price,
                'potential_profit': snapshot.spread * 0.5  # Profit if filled on both sides
            }

        return None

    def get_vwap(self, side: str, depth: float = 1000) -> float:
        """
        Calculate Volume Weighted Average Price for given depth

        Args:
            side: 'BUY' or 'SELL'
            depth: Dollar depth to consider

        Returns:
            VWAP price
        """
        snapshot = self.get_snapshot()
        levels = snapshot.asks if side == 'BUY' else snapshot.bids

        total_volume = 0
        total_value = 0

        for level in levels:
            level_value = level.price * level.size
            if total_value + level_value > depth:
                remaining_value = depth - total_value
                remaining_volume = remaining_value / level.price
                total_volume += remaining_volume
                total_value = depth
                break

            total_volume += level.size
            total_value += level_value

        return total_value / total_volume if total_volume > 0 else 0

    def analyze_momentum(self, lookback: int = 100) -> Dict:
        """
        Analyze price momentum from recent history

        Args:
            lookback: Number of price updates to analyze

        Returns:
            Dict with momentum indicators
        """
        if len(self._price_history) < 2:
            return {'direction': 'neutral', 'strength': 0}

        recent = list(self._price_history)[-lookback:]

        first_mid = recent[0]['mid']
        last_mid = recent[-1]['mid']

        change = last_mid - first_mid
        change_pct = change / first_mid if first_mid > 0 else 0

        # Calculate volatility
        prices = [p['mid'] for p in recent]
        avg_price = sum(prices) / len(prices)
        variance = sum((p - avg_price) ** 2 for p in prices) / len(prices)
        volatility = variance ** 0.5

        direction = 'up' if change > 0 else 'down' if change < 0 else 'neutral'

        return {
            'direction': direction,
            'change': change,
            'change_pct': change_pct,
            'volatility': volatility,
            'strength': abs(change_pct) / (volatility + 0.0001)
        }


class MultiOrderBook:
    """
    Manages multiple orderbooks for cross-market analysis
    Used for Yes/No arbitrage detection
    """

    def __init__(self):
        self._books: Dict[str, OrderBook] = {}
        self._pairs: List[Tuple[str, str]] = []  # (yes_token, no_token) pairs

    def add_market(self, yes_token: str, no_token: str, ws_url: str):
        """Add a Yes/No market pair"""
        self._books[yes_token] = OrderBook(yes_token, ws_url)
        self._books[no_token] = OrderBook(no_token, ws_url)
        self._pairs.append((yes_token, no_token))

    async def connect_all(self):
        """Connect to all orderbooks"""
        tasks = [book.connect() for book in self._books.values()]
        await asyncio.gather(*tasks)

    async def disconnect_all(self):
        """Disconnect from all orderbooks"""
        tasks = [book.disconnect() for book in self._books.values()]
        await asyncio.gather(*tasks)

    def check_arbitrage(self, yes_token: str, no_token: str) -> Optional[Dict]:
        """
        Check for Yes/No arbitrage opportunity

        Returns arbitrage details if:
        - Yes_ask + No_ask < 1 (buy both)
        - Yes_bid + No_bid > 1 (sell both)
        """
        yes_book = self._books.get(yes_token)
        no_book = self._books.get(no_token)

        if not yes_book or not no_book:
            return None

        yes_snap = yes_book.get_snapshot()
        no_snap = no_book.get_snapshot()

        # Check buy arbitrage
        buy_total = yes_snap.best_ask + no_snap.best_ask
        if buy_total < 0.995:
            return {
                'type': 'BUY_BOTH',
                'yes_price': yes_snap.best_ask,
                'no_price': no_snap.best_ask,
                'total': buy_total,
                'profit': 1 - buy_total,
                'profit_pct': (1 - buy_total) / buy_total
            }

        # Check sell arbitrage
        sell_total = yes_snap.best_bid + no_snap.best_bid
        if sell_total > 1.005:
            return {
                'type': 'SELL_BOTH',
                'yes_price': yes_snap.best_bid,
                'no_price': no_snap.best_bid,
                'total': sell_total,
                'profit': sell_total - 1,
                'profit_pct': (sell_total - 1)
            }

        return None

    def scan_all_arbitrage(self) -> List[Dict]:
        """Scan all market pairs for arbitrage opportunities"""
        opportunities = []

        for yes_token, no_token in self._pairs:
            arb = self.check_arbitrage(yes_token, no_token)
            if arb:
                arb['yes_token'] = yes_token
                arb['no_token'] = no_token
                opportunities.append(arb)

        return opportunities
