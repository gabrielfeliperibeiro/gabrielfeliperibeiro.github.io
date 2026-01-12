"""
Binance Exchange Connector
Real-time price feeds for Bitcoin and crypto markets
Critical for latency arbitrage strategy
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, List, Optional, Any

import aiohttp
import websockets
from loguru import logger

from .base import ExchangeBase, PriceUpdate, OHLCV


class BinanceConnector(ExchangeBase):
    """
    Binance exchange connector with WebSocket price feeds
    Optimized for low-latency price detection
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = False
    ):
        super().__init__(api_key, api_secret, testnet)

        self._base_url = (
            "https://testnet.binance.vision" if testnet
            else "https://api.binance.com"
        )
        self._ws_url = (
            "wss://testnet.binance.vision/ws" if testnet
            else "wss://stream.binance.com:9443/ws"
        )
        self._stream_url = (
            "wss://testnet.binance.vision/stream" if testnet
            else "wss://stream.binance.com:9443/stream"
        )

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._subscriptions: set = set()
        self._reconnect_delay = 1

    @property
    def name(self) -> str:
        return "Binance"

    @property
    def supported_symbols(self) -> List[str]:
        return [
            "BTCUSDT", "BTCUSDC", "BTCBUSD",
            "ETHUSDT", "ETHBTC",
            "SOLUSDT", "SOLBTC",
            "BNBUSDT", "BNBBTC"
        ]

    async def connect(self):
        """Initialize HTTP and WebSocket connections"""
        if self._session is None:
            self._session = aiohttp.ClientSession()

        self._running = True
        logger.info(f"Binance connector initialized (testnet={self.testnet})")

    async def disconnect(self):
        """Close all connections"""
        self._running = False

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._session:
            await self._session.close()
            self._session = None

        logger.info("Binance connector disconnected")

    async def _api_request(
        self,
        endpoint: str,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make REST API request"""
        if self._session is None:
            await self.connect()

        url = f"{self._base_url}{endpoint}"

        try:
            async with self._session.get(url, params=params) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as e:
            logger.error(f"Binance API error: {e}")
            raise

    async def get_price(self, symbol: str) -> PriceUpdate:
        """
        Get current price for a symbol

        Args:
            symbol: Trading pair (e.g., BTCUSDT)

        Returns:
            PriceUpdate with current market data
        """
        # Get ticker data
        ticker = await self._api_request("/api/v3/ticker/bookTicker", {"symbol": symbol})

        return PriceUpdate(
            exchange=self.name,
            symbol=symbol,
            price=float(ticker.get('bidPrice', 0)) + float(ticker.get('askPrice', 0)) / 2,
            bid=float(ticker.get('bidPrice', 0)),
            ask=float(ticker.get('askPrice', 0)),
            volume_24h=0,  # Need separate call for volume
            timestamp=datetime.now(),
            raw_data=ticker
        )

    async def get_ticker(self, symbol: str) -> Dict:
        """Get 24h ticker with volume"""
        return await self._api_request("/api/v3/ticker/24hr", {"symbol": symbol})

    async def get_all_tickers(self) -> List[Dict]:
        """Get all tickers"""
        return await self._api_request("/api/v3/ticker/24hr")

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100
    ) -> List[OHLCV]:
        """
        Get historical candlestick data

        Args:
            symbol: Trading pair
            timeframe: Candle interval (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Number of candles to fetch

        Returns:
            List of OHLCV candles
        """
        data = await self._api_request("/api/v3/klines", {
            "symbol": symbol,
            "interval": timeframe,
            "limit": limit
        })

        candles = []
        for candle in data:
            candles.append(OHLCV(
                timestamp=datetime.fromtimestamp(candle[0] / 1000),
                open=float(candle[1]),
                high=float(candle[2]),
                low=float(candle[3]),
                close=float(candle[4]),
                volume=float(candle[5])
            ))

        return candles

    async def subscribe_price(self, symbol: str):
        """Subscribe to real-time price updates via WebSocket"""
        stream_name = f"{symbol.lower()}@bookTicker"
        self._subscriptions.add(stream_name)

        if self._ws is None:
            asyncio.create_task(self._ws_connect())

    async def unsubscribe_price(self, symbol: str):
        """Unsubscribe from price updates"""
        stream_name = f"{symbol.lower()}@bookTicker"
        self._subscriptions.discard(stream_name)

    async def subscribe_trades(self, symbol: str):
        """Subscribe to real-time trade stream"""
        stream_name = f"{symbol.lower()}@trade"
        self._subscriptions.add(stream_name)

        if self._ws is None:
            asyncio.create_task(self._ws_connect())

    async def subscribe_kline(self, symbol: str, interval: str = "1m"):
        """Subscribe to real-time candlestick updates"""
        stream_name = f"{symbol.lower()}@kline_{interval}"
        self._subscriptions.add(stream_name)

        if self._ws is None:
            asyncio.create_task(self._ws_connect())

    async def _ws_connect(self):
        """
        Connect to WebSocket and handle messages
        Implements automatic reconnection
        """
        while self._running and self._subscriptions:
            try:
                # Build combined stream URL
                streams = "/".join(self._subscriptions)
                ws_url = f"{self._stream_url}?streams={streams}"

                logger.info(f"Connecting to Binance WebSocket: {len(self._subscriptions)} streams")

                async with websockets.connect(ws_url) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1  # Reset delay on successful connect

                    async for message in ws:
                        if not self._running:
                            break

                        data = json.loads(message)
                        await self._handle_ws_message(data)

            except websockets.exceptions.ConnectionClosed:
                logger.warning("Binance WebSocket closed, reconnecting...")
            except Exception as e:
                logger.error(f"Binance WebSocket error: {e}")

            if self._running:
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60)

        self._ws = None

    async def _handle_ws_message(self, message: Dict):
        """Process incoming WebSocket message"""
        if 'stream' not in message:
            return

        stream = message['stream']
        data = message['data']

        if '@bookTicker' in stream:
            await self._handle_book_ticker(data)
        elif '@trade' in stream:
            await self._handle_trade(data)
        elif '@kline' in stream:
            await self._handle_kline(data)

    async def _handle_book_ticker(self, data: Dict):
        """Handle book ticker update (best bid/ask)"""
        update = PriceUpdate(
            exchange=self.name,
            symbol=data['s'],
            price=(float(data['b']) + float(data['a'])) / 2,
            bid=float(data['b']),
            ask=float(data['a']),
            volume_24h=0,
            timestamp=datetime.now(),
            raw_data=data
        )

        await self._notify_callbacks(update)

    async def _handle_trade(self, data: Dict):
        """Handle individual trade"""
        update = PriceUpdate(
            exchange=self.name,
            symbol=data['s'],
            price=float(data['p']),
            bid=float(data['p']),
            ask=float(data['p']),
            volume_24h=float(data['q']),
            timestamp=datetime.fromtimestamp(data['T'] / 1000),
            raw_data=data
        )

        await self._notify_callbacks(update)

    async def _handle_kline(self, data: Dict):
        """Handle kline/candlestick update"""
        # Klines are handled separately, notify for price updates
        kline = data['k']
        update = PriceUpdate(
            exchange=self.name,
            symbol=data['s'],
            price=float(kline['c']),  # Close price
            bid=float(kline['c']),
            ask=float(kline['c']),
            volume_24h=float(kline['v']),
            timestamp=datetime.fromtimestamp(kline['t'] / 1000),
            raw_data=data
        )

        await self._notify_callbacks(update)

    # ============ Latency Arbitrage Helpers ============

    async def monitor_btc_impulse(
        self,
        threshold_pct: float = 0.02,
        callback: Optional[callable] = None
    ):
        """
        Monitor BTC for sudden price movements (impulses)
        Critical for latency arbitrage strategy

        Args:
            threshold_pct: Minimum price change to trigger (default 2%)
            callback: Function to call when impulse detected
        """
        symbol = "BTCUSDT"

        async def check_impulse(update: PriceUpdate):
            impulse = self.detect_price_impulse(symbol, threshold_pct)
            if impulse and callback:
                logger.info(f"BTC impulse detected: {impulse['direction']} "
                           f"{impulse['change_pct']:.2%}")
                if asyncio.iscoroutinefunction(callback):
                    await callback(impulse)
                else:
                    callback(impulse)

        self.add_callback(check_impulse)
        await self.subscribe_price(symbol)

        logger.info(f"Monitoring BTC for impulses (threshold: {threshold_pct:.1%})")

    async def get_btc_reference_price(self) -> float:
        """
        Get current BTC price as reference for Polymarket arbitrage

        Returns:
            BTC price in USD
        """
        update = await self.get_price("BTCUSDT")
        return update.mid_price

    def get_btc_price_change(self, window_ms: int = 60000) -> Optional[Dict]:
        """
        Get BTC price change over specified window

        Args:
            window_ms: Time window in milliseconds

        Returns:
            Dict with price change info
        """
        history = self.get_price_history("BTCUSDT", 1000)
        if len(history) < 2:
            return None

        now = datetime.now()
        window_start = None

        # Find price at window start
        for update in reversed(history):
            age_ms = (now - update.timestamp).total_seconds() * 1000
            if age_ms >= window_ms:
                window_start = update
                break

        if not window_start:
            window_start = history[0]

        current = history[-1]
        change = current.price - window_start.price
        change_pct = change / window_start.price

        return {
            'from_price': window_start.price,
            'to_price': current.price,
            'change': change,
            'change_pct': change_pct,
            'direction': 'up' if change > 0 else 'down' if change < 0 else 'flat',
            'window_ms': window_ms
        }
