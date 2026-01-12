"""
Polymarket API Client for Bitcoin Market Arbitrage
Handles CLOB API, market data, and order execution
"""

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

import aiohttp
from loguru import logger


@dataclass
class OrderResponse:
    """Response from order placement"""
    order_id: str
    status: str
    filled_amount: float
    avg_price: float
    timestamp: datetime


@dataclass
class Position:
    """Current position in a market"""
    market_id: str
    token_id: str
    side: str  # YES or NO
    size: float
    avg_entry_price: float
    current_price: float
    unrealized_pnl: float


class PolymarketClient:
    """
    Async client for Polymarket CLOB API
    Supports market data, order execution, and position management
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        api_passphrase: str = "",
        base_url: str = "https://clob.polymarket.com",
        gamma_url: str = "https://gamma-api.polymarket.com",
        dry_run: bool = True
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.base_url = base_url
        self.gamma_url = gamma_url
        self.dry_run = dry_run
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def connect(self):
        """Initialize HTTP session"""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        logger.info("Polymarket client connected")

    async def close(self):
        """Close HTTP session"""
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Polymarket client disconnected")

    def _generate_signature(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """Generate HMAC signature for authenticated requests"""
        message = f"{timestamp}{method}{path}{body}"
        signature = hmac.new(
            self.api_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _get_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        """Generate headers for authenticated requests"""
        timestamp = str(int(time.time() * 1000))
        signature = self._generate_signature(timestamp, method, path, body)

        return {
            "POLY_API_KEY": self.api_key,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": timestamp,
            "POLY_PASSPHRASE": self.api_passphrase,
            "Content-Type": "application/json"
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        authenticated: bool = False,
        use_gamma: bool = False
    ) -> Dict[str, Any]:
        """Make HTTP request to Polymarket API"""
        if self._session is None:
            await self.connect()

        base = self.gamma_url if use_gamma else self.base_url
        url = f"{base}{endpoint}"

        body = json.dumps(data) if data else ""
        headers = self._get_headers(method, endpoint, body) if authenticated else {
            "Content-Type": "application/json"
        }

        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=data,
                headers=headers
            ) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as e:
            logger.error(f"API request failed: {e}")
            raise

    # ============ Market Data Methods ============

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
        tag: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch available markets from Polymarket

        Args:
            limit: Maximum number of markets to return
            offset: Pagination offset
            active: Include active markets
            closed: Include closed markets
            tag: Filter by tag (e.g., 'crypto', 'bitcoin')

        Returns:
            List of market data dictionaries
        """
        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower()
        }
        if tag:
            params["tag"] = tag

        response = await self._request("GET", "/markets", params=params, use_gamma=True)
        return response if isinstance(response, list) else response.get("data", [])

    async def get_market(self, market_id: str) -> Dict[str, Any]:
        """Get detailed information about a specific market"""
        return await self._request("GET", f"/markets/{market_id}", use_gamma=True)

    async def get_bitcoin_markets(self) -> List[Dict[str, Any]]:
        """
        Get all markets related to Bitcoin/BTC
        Searches for markets with bitcoin-related keywords
        """
        all_markets = await self.get_markets(limit=500, active=True)

        bitcoin_keywords = ['bitcoin', 'btc', 'crypto', 'cryptocurrency']
        bitcoin_markets = []

        for market in all_markets:
            title = market.get('question', '').lower()
            description = market.get('description', '').lower()
            tags = [t.lower() for t in market.get('tags', [])]

            if any(kw in title or kw in description or kw in tags for kw in bitcoin_keywords):
                bitcoin_markets.append(market)

        logger.info(f"Found {len(bitcoin_markets)} Bitcoin-related markets")
        return bitcoin_markets

    async def get_orderbook(self, token_id: str) -> Dict[str, Any]:
        """
        Get orderbook for a specific token

        Args:
            token_id: The conditional token ID

        Returns:
            Orderbook with bids and asks
        """
        return await self._request("GET", f"/book", params={"token_id": token_id})

    async def get_price(self, token_id: str) -> Dict[str, float]:
        """
        Get current best bid/ask prices for a token

        Returns:
            Dict with 'bid', 'ask', 'mid', 'spread' prices
        """
        orderbook = await self.get_orderbook(token_id)

        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])

        best_bid = float(bids[0]['price']) if bids else 0.0
        best_ask = float(asks[0]['price']) if asks else 1.0
        mid_price = (best_bid + best_ask) / 2
        spread = best_ask - best_bid

        return {
            'bid': best_bid,
            'ask': best_ask,
            'mid': mid_price,
            'spread': spread,
            'spread_pct': spread / mid_price if mid_price > 0 else 0
        }

    async def get_market_prices(self, market_id: str) -> Dict[str, Dict[str, float]]:
        """
        Get prices for all outcomes in a market

        Returns:
            Dict mapping outcome names to their prices
        """
        market = await self.get_market(market_id)
        outcomes = market.get('outcomes', [])
        tokens = market.get('clobTokenIds', [])

        prices = {}
        for i, (outcome, token_id) in enumerate(zip(outcomes, tokens)):
            prices[outcome] = await self.get_price(token_id)

        return prices

    # ============ Order Execution Methods ============

    async def place_order(
        self,
        token_id: str,
        side: str,  # "BUY" or "SELL"
        price: float,
        size: float,
        order_type: str = "GTC"  # GTC, FOK, IOC
    ) -> OrderResponse:
        """
        Place an order on Polymarket

        Args:
            token_id: Conditional token ID
            side: BUY or SELL
            price: Limit price (0-1)
            size: Order size in shares
            order_type: GTC (Good Till Cancel), FOK (Fill or Kill), IOC (Immediate or Cancel)

        Returns:
            OrderResponse with order details
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Would place {side} order: {size} shares @ ${price:.4f}")
            return OrderResponse(
                order_id=f"dry_run_{int(time.time())}",
                status="simulated",
                filled_amount=size,
                avg_price=price,
                timestamp=datetime.now()
            )

        order_data = {
            "tokenID": token_id,
            "side": side,
            "price": str(price),
            "size": str(size),
            "type": order_type
        }

        response = await self._request(
            "POST",
            "/order",
            data=order_data,
            authenticated=True
        )

        return OrderResponse(
            order_id=response.get("orderID", ""),
            status=response.get("status", "unknown"),
            filled_amount=float(response.get("filledSize", 0)),
            avg_price=float(response.get("avgPrice", price)),
            timestamp=datetime.now()
        )

    async def place_market_order(
        self,
        token_id: str,
        side: str,
        size: float
    ) -> OrderResponse:
        """Place a market order (uses FOK with best price)"""
        prices = await self.get_price(token_id)

        # Use best ask for buys, best bid for sells
        price = prices['ask'] if side == "BUY" else prices['bid']

        return await self.place_order(
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            order_type="FOK"
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order"""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would cancel order: {order_id}")
            return True

        try:
            await self._request(
                "DELETE",
                f"/order/{order_id}",
                authenticated=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def cancel_all_orders(self, market_id: Optional[str] = None) -> int:
        """Cancel all orders, optionally filtered by market"""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would cancel all orders")
            return 0

        params = {"market": market_id} if market_id else None
        response = await self._request(
            "DELETE",
            "/orders",
            params=params,
            authenticated=True
        )
        return response.get("canceled", 0)

    # ============ Position Management ============

    async def get_positions(self) -> List[Position]:
        """Get all current positions"""
        if self.dry_run:
            return []

        response = await self._request(
            "GET",
            "/positions",
            authenticated=True
        )

        positions = []
        for pos in response.get("positions", []):
            prices = await self.get_price(pos['tokenId'])
            current_price = prices['mid']
            entry_price = float(pos.get('avgPrice', 0))
            size = float(pos.get('size', 0))

            pnl = (current_price - entry_price) * size if pos['side'] == 'YES' else \
                  (entry_price - current_price) * size

            positions.append(Position(
                market_id=pos.get('marketId', ''),
                token_id=pos['tokenId'],
                side=pos.get('side', 'YES'),
                size=size,
                avg_entry_price=entry_price,
                current_price=current_price,
                unrealized_pnl=pnl
            ))

        return positions

    async def get_position(self, token_id: str) -> Optional[Position]:
        """Get position for a specific token"""
        positions = await self.get_positions()
        for pos in positions:
            if pos.token_id == token_id:
                return pos
        return None

    # ============ Trade History ============

    async def get_trades(
        self,
        market_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get recent trades"""
        params = {"limit": limit}
        if market_id:
            params["market"] = market_id

        return await self._request(
            "GET",
            "/trades",
            params=params,
            authenticated=True
        )

    # ============ Arbitrage-Specific Methods ============

    async def check_yes_no_arbitrage(self, market_id: str) -> Optional[Dict[str, Any]]:
        """
        Check if Yes + No prices != $1 (arbitrage opportunity)

        Returns:
            Dict with arbitrage details if opportunity exists, None otherwise
        """
        market = await self.get_market(market_id)
        tokens = market.get('clobTokenIds', [])

        if len(tokens) < 2:
            return None

        yes_token, no_token = tokens[0], tokens[1]

        yes_prices = await self.get_price(yes_token)
        no_prices = await self.get_price(no_token)

        # Check if we can buy both sides for less than $1
        total_buy = yes_prices['ask'] + no_prices['ask']

        # Check if we can sell both sides for more than $1
        total_sell = yes_prices['bid'] + no_prices['bid']

        arb_opportunity = None

        if total_buy < 0.995:  # Buy both sides for < $1
            profit_pct = (1 - total_buy) / total_buy
            arb_opportunity = {
                'type': 'BUY_BOTH',
                'yes_price': yes_prices['ask'],
                'no_price': no_prices['ask'],
                'total_cost': total_buy,
                'profit_pct': profit_pct,
                'yes_token': yes_token,
                'no_token': no_token,
                'market_id': market_id
            }
        elif total_sell > 1.005:  # Sell both sides for > $1
            profit_pct = (total_sell - 1) / 1
            arb_opportunity = {
                'type': 'SELL_BOTH',
                'yes_price': yes_prices['bid'],
                'no_price': no_prices['bid'],
                'total_value': total_sell,
                'profit_pct': profit_pct,
                'yes_token': yes_token,
                'no_token': no_token,
                'market_id': market_id
            }

        if arb_opportunity:
            logger.info(f"Found arbitrage opportunity in {market_id}: {arb_opportunity['type']} "
                       f"with {arb_opportunity['profit_pct']:.2%} profit")

        return arb_opportunity

    async def find_near_resolved_markets(
        self,
        min_probability: float = 0.95,
        max_probability: float = 0.99
    ) -> List[Dict[str, Any]]:
        """
        Find markets where one outcome is near certain (95-99%)
        Perfect for the near-resolved sniping strategy
        """
        markets = await self.get_bitcoin_markets()
        near_resolved = []

        for market in markets:
            tokens = market.get('clobTokenIds', [])
            outcomes = market.get('outcomes', [])

            for i, token_id in enumerate(tokens):
                try:
                    prices = await self.get_price(token_id)
                    mid_price = prices['mid']

                    if min_probability <= mid_price <= max_probability:
                        near_resolved.append({
                            'market_id': market.get('id', ''),
                            'question': market.get('question', ''),
                            'outcome': outcomes[i] if i < len(outcomes) else 'Unknown',
                            'token_id': token_id,
                            'probability': mid_price,
                            'potential_yield': 1 - mid_price,
                            'end_date': market.get('endDate', '')
                        })
                except Exception as e:
                    logger.debug(f"Error getting price for token {token_id}: {e}")
                    continue

        # Sort by yield (highest first)
        near_resolved.sort(key=lambda x: x['potential_yield'], reverse=True)

        logger.info(f"Found {len(near_resolved)} near-resolved opportunities")
        return near_resolved
