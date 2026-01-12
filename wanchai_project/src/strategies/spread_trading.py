"""
Spread Trading (Market Making) Strategy

Provide liquidity by placing the highest bid and lowest ask,
profiting from the spread when both sides fill.

Key Tactics:
- Set bids/asks with even volume
- Monitor and rebalance positions
- Focus on active, liquid markets

Referenced Performance: Popular profit method, spread capture
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from .base import StrategyBase, TradeSignal, StrategyResult, SignalType
from ..polymarket.client import PolymarketClient
from ..polymarket.orderbook import OrderBook


class SpreadTradingStrategy(StrategyBase):
    """
    Spread Trading: Market making on Polymarket

    Place limit orders on both sides of the market,
    capturing the spread when orders fill.
    """

    def __init__(
        self,
        polymarket: PolymarketClient,
        config: Dict[str, Any],
        dry_run: bool = True
    ):
        super().__init__("SpreadTrading", config, dry_run)

        self.polymarket = polymarket

        # Strategy parameters
        self.min_spread = config.get('min_spread', 0.02)  # 2% minimum spread
        self.order_refresh_seconds = config.get('order_refresh_seconds', 30)
        self.max_inventory_imbalance = config.get('max_inventory_imbalance', 0.3)
        self.order_size = config.get('order_size', 100)  # Shares per order

        # State tracking
        self._active_orders: Dict[str, Dict] = {}  # market_id -> orders
        self._inventory: Dict[str, Dict] = {}  # token_id -> position
        self._orderbooks: Dict[str, OrderBook] = {}

    async def add_market(self, market_id: str):
        """Add a market for market making"""
        market = await self.polymarket.get_market(market_id)
        tokens = market.get('clobTokenIds', [])

        if tokens:
            for token_id in tokens:
                self._orderbooks[token_id] = OrderBook(token_id)
            logger.info(f"[{self.name}] Added market for spread trading: {market_id}")

    async def scan(self) -> List[TradeSignal]:
        """
        Scan for spread trading opportunities

        Finds markets with wide spreads suitable for market making
        """
        signals = []

        # Get Bitcoin markets
        markets = await self.polymarket.get_bitcoin_markets()

        for market in markets:
            tokens = market.get('clobTokenIds', [])

            for token_id in tokens:
                try:
                    prices = await self.polymarket.get_price(token_id)
                    spread_pct = prices.get('spread_pct', 0)

                    if spread_pct >= self.min_spread:
                        # Calculate our bid/ask prices
                        current_bid = prices['bid']
                        current_ask = prices['ask']
                        mid = prices['mid']

                        # Place orders inside the current spread
                        our_bid = current_bid + 0.001  # Improve bid by $0.001
                        our_ask = current_ask - 0.001  # Improve ask by $0.001

                        # Ensure we still capture meaningful spread
                        our_spread = our_ask - our_bid
                        if our_spread >= self.min_spread * mid:
                            signal = TradeSignal(
                                strategy_name=self.name,
                                signal_type=SignalType.BUY,  # Will place both buy and sell
                                market_id=market.get('id', ''),
                                token_id=token_id,
                                side="BOTH",
                                price=mid,
                                size=self.order_size,
                                confidence=min(0.8, spread_pct / self.min_spread / 2),
                                expected_profit_pct=our_spread / mid,
                                reason=f"Spread {spread_pct:.2%} > {self.min_spread:.2%} threshold",
                                metadata={
                                    'current_bid': current_bid,
                                    'current_ask': current_ask,
                                    'our_bid': our_bid,
                                    'our_ask': our_ask,
                                    'our_spread': our_spread,
                                    'question': market.get('question', '')
                                }
                            )
                            signals.append(signal)

                except Exception as e:
                    logger.debug(f"Error scanning token {token_id}: {e}")

        return signals

    async def execute(self, signal: TradeSignal) -> StrategyResult:
        """
        Execute spread trading by placing bid and ask orders
        """
        if not self.validate_signal(signal):
            return StrategyResult(
                strategy_name=self.name,
                success=False,
                profit_loss=0,
                trades_executed=0,
                signals_generated=1,
                duration_seconds=0,
                errors=["Invalid signal"]
            )

        start_time = datetime.now()
        our_bid = signal.metadata.get('our_bid')
        our_ask = signal.metadata.get('our_ask')

        try:
            # Check inventory imbalance before placing orders
            imbalance = self._get_inventory_imbalance(signal.token_id)

            # Adjust order sizes based on inventory
            bid_size = signal.size
            ask_size = signal.size

            if imbalance > self.max_inventory_imbalance:
                # Too long - reduce bid, increase ask
                bid_size *= (1 - imbalance)
                ask_size *= (1 + imbalance)
            elif imbalance < -self.max_inventory_imbalance:
                # Too short - increase bid, reduce ask
                bid_size *= (1 + abs(imbalance))
                ask_size *= (1 - abs(imbalance))

            # Place bid order
            bid_order = await self.polymarket.place_order(
                token_id=signal.token_id,
                side="BUY",
                price=our_bid,
                size=bid_size,
                order_type="GTC"
            )

            # Place ask order
            ask_order = await self.polymarket.place_order(
                token_id=signal.token_id,
                side="SELL",
                price=our_ask,
                size=ask_size,
                order_type="GTC"
            )

            # Track orders
            self._active_orders[signal.market_id] = {
                'token_id': signal.token_id,
                'bid_order': bid_order.order_id,
                'ask_order': ask_order.order_id,
                'bid_price': our_bid,
                'ask_price': our_ask,
                'bid_size': bid_size,
                'ask_size': ask_size,
                'timestamp': datetime.now()
            }

            logger.info(f"[{self.name}] Orders placed: Bid ${our_bid:.4f} x {bid_size:.0f}, "
                       f"Ask ${our_ask:.4f} x {ask_size:.0f}")

            return StrategyResult(
                strategy_name=self.name,
                success=True,
                profit_loss=0,  # P&L realized on fills
                trades_executed=2,  # Two orders placed
                signals_generated=1,
                duration_seconds=(datetime.now() - start_time).total_seconds(),
                details={
                    'bid_order': bid_order.order_id,
                    'ask_order': ask_order.order_id,
                    'spread': our_ask - our_bid
                }
            )

        except Exception as e:
            logger.error(f"[{self.name}] Execution error: {e}")
            return StrategyResult(
                strategy_name=self.name,
                success=False,
                profit_loss=0,
                trades_executed=0,
                signals_generated=1,
                duration_seconds=(datetime.now() - start_time).total_seconds(),
                errors=[str(e)]
            )

    def _get_inventory_imbalance(self, token_id: str) -> float:
        """
        Calculate inventory imbalance for a token

        Returns value from -1 (all short) to +1 (all long)
        """
        position = self._inventory.get(token_id, {'size': 0})
        if position['size'] == 0:
            return 0

        # Normalize by typical position size
        return position['size'] / (self.order_size * 10)  # Assume 10x typical as max

    async def update_inventory(self, token_id: str, fill_side: str, fill_size: float, fill_price: float):
        """Update inventory after order fill"""
        if token_id not in self._inventory:
            self._inventory[token_id] = {'size': 0, 'avg_price': 0, 'realized_pnl': 0}

        inv = self._inventory[token_id]

        if fill_side == 'BUY':
            # Bought shares - add to inventory
            total_cost = inv['size'] * inv['avg_price'] + fill_size * fill_price
            inv['size'] += fill_size
            inv['avg_price'] = total_cost / inv['size'] if inv['size'] > 0 else 0
        else:
            # Sold shares - reduce inventory and realize P&L
            if inv['size'] > 0:
                pnl = fill_size * (fill_price - inv['avg_price'])
                inv['realized_pnl'] += pnl
                inv['size'] -= fill_size

                self.update_metrics({
                    'token_id': token_id,
                    'fill_side': fill_side,
                    'profit_loss': pnl
                })

                logger.info(f"[{self.name}] Fill: {fill_side} {fill_size} @ ${fill_price:.4f}, "
                           f"P&L: ${pnl:.2f}")

    async def refresh_orders(self, market_id: str):
        """Cancel and replace orders with updated prices"""
        orders = self._active_orders.get(market_id)
        if not orders:
            return

        # Cancel existing orders
        if orders.get('bid_order'):
            await self.polymarket.cancel_order(orders['bid_order'])
        if orders.get('ask_order'):
            await self.polymarket.cancel_order(orders['ask_order'])

        # Get fresh prices
        token_id = orders['token_id']
        prices = await self.polymarket.get_price(token_id)

        if prices['spread_pct'] >= self.min_spread:
            # Place new orders
            new_bid = prices['bid'] + 0.001
            new_ask = prices['ask'] - 0.001

            bid_order = await self.polymarket.place_order(
                token_id=token_id,
                side="BUY",
                price=new_bid,
                size=self.order_size,
                order_type="GTC"
            )

            ask_order = await self.polymarket.place_order(
                token_id=token_id,
                side="SELL",
                price=new_ask,
                size=self.order_size,
                order_type="GTC"
            )

            # Update tracking
            self._active_orders[market_id] = {
                'token_id': token_id,
                'bid_order': bid_order.order_id,
                'ask_order': ask_order.order_id,
                'bid_price': new_bid,
                'ask_price': new_ask,
                'timestamp': datetime.now()
            }

            logger.debug(f"[{self.name}] Orders refreshed for {market_id}")

    async def close_position(self, market_id: str) -> StrategyResult:
        """Cancel all orders and close inventory position"""
        orders = self._active_orders.get(market_id)

        if orders:
            # Cancel orders
            if orders.get('bid_order'):
                await self.polymarket.cancel_order(orders['bid_order'])
            if orders.get('ask_order'):
                await self.polymarket.cancel_order(orders['ask_order'])

            del self._active_orders[market_id]

        # Close inventory position
        token_id = orders['token_id'] if orders else None
        if token_id and token_id in self._inventory:
            inv = self._inventory[token_id]
            if inv['size'] != 0:
                # Market sell remaining inventory
                order = await self.polymarket.place_market_order(
                    token_id=token_id,
                    side="SELL" if inv['size'] > 0 else "BUY",
                    size=abs(inv['size'])
                )

                pnl = inv['realized_pnl']
                del self._inventory[token_id]

                return StrategyResult(
                    strategy_name=self.name,
                    success=True,
                    profit_loss=pnl,
                    trades_executed=1,
                    signals_generated=0,
                    duration_seconds=0,
                    details={'closed_inventory': inv['size'], 'realized_pnl': pnl}
                )

        return StrategyResult(
            strategy_name=self.name,
            success=True,
            profit_loss=0,
            trades_executed=0,
            signals_generated=0,
            duration_seconds=0
        )

    async def run_market_making(self, market_ids: List[str], duration_seconds: int = 3600):
        """
        Run continuous market making on specified markets

        Args:
            market_ids: Markets to provide liquidity
            duration_seconds: How long to run (default 1 hour)
        """
        start_time = datetime.now()

        for market_id in market_ids:
            await self.add_market(market_id)

        logger.info(f"[{self.name}] Starting market making on {len(market_ids)} markets")

        while self.enabled:
            elapsed = (datetime.now() - start_time).total_seconds()
            if elapsed >= duration_seconds:
                break

            try:
                # Scan and execute
                result = await self.run_once()

                # Refresh orders periodically
                for market_id in list(self._active_orders.keys()):
                    order_info = self._active_orders[market_id]
                    order_age = (datetime.now() - order_info['timestamp']).total_seconds()

                    if order_age >= self.order_refresh_seconds:
                        await self.refresh_orders(market_id)

                await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"[{self.name}] Market making error: {e}")
                await asyncio.sleep(10)

        # Clean up
        for market_id in list(self._active_orders.keys()):
            await self.close_position(market_id)

        logger.info(f"[{self.name}] Market making session ended")

    def get_mm_stats(self) -> Dict:
        """Get market making statistics"""
        total_pnl = sum(inv.get('realized_pnl', 0) for inv in self._inventory.values())
        total_inventory = sum(abs(inv.get('size', 0)) for inv in self._inventory.values())

        return {
            'active_markets': len(self._active_orders),
            'total_inventory': total_inventory,
            'realized_pnl': total_pnl,
            'metrics': {
                'total_trades': self._metrics.total_trades,
                'win_rate': self._metrics.win_rate,
                'profit_factor': self._metrics.profit_factor
            }
        }
