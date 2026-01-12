"""
Yes/No Arbitrage Strategy

Exploit when Yes + No prices != $1 due to market inefficiencies.
Buy both cheap sides and merge, or mint and sell when overpriced.

Key Tactics:
- Monitor for price totals > $1 or < $1
- Execute quickly during volatile events
- Use bots for 24/7 monitoring

Referenced Performance: 3-5% per opportunity, $3,000 casually
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from .base import StrategyBase, TradeSignal, StrategyResult, SignalType
from ..polymarket.client import PolymarketClient
from ..polymarket.orderbook import MultiOrderBook


class YesNoArbitrageStrategy(StrategyBase):
    """
    Yes/No Arbitrage: Profit from mispriced binary markets

    In binary markets, Yes + No should always equal $1.
    When they don't, arbitrage exists:
    - If Yes + No < $1: Buy both, guaranteed profit on resolution
    - If Yes + No > $1: Mint shares and sell both sides
    """

    def __init__(
        self,
        polymarket: PolymarketClient,
        config: Dict[str, Any],
        dry_run: bool = True
    ):
        super().__init__("YesNoArbitrage", config, dry_run)

        self.polymarket = polymarket
        self.multi_book = MultiOrderBook()

        # Strategy parameters
        self.min_spread = config.get('min_spread', 0.005)  # 0.5% minimum arb
        self.max_slippage = config.get('max_slippage', 0.01)  # 1% max slippage
        self.target_profit = config.get('target_profit_pct', 0.03)  # 3% target
        self.max_position = config.get('max_position_size', 10000)  # USD

        # Tracking
        self._monitored_markets: Dict[str, Dict] = {}
        self._active_arbs: List[Dict] = []

    async def add_market_to_monitor(self, market_id: str):
        """Add a market to continuous monitoring"""
        market = await self.polymarket.get_market(market_id)
        tokens = market.get('clobTokenIds', [])

        if len(tokens) >= 2:
            yes_token, no_token = tokens[0], tokens[1]
            self.multi_book.add_market(
                yes_token,
                no_token,
                "wss://ws-subscriptions-clob.polymarket.com/ws/market"
            )
            self._monitored_markets[market_id] = {
                'yes_token': yes_token,
                'no_token': no_token,
                'question': market.get('question', '')
            }
            logger.info(f"[{self.name}] Monitoring market: {market_id}")

    async def scan(self) -> List[TradeSignal]:
        """
        Scan all binary markets for Yes/No arbitrage opportunities
        """
        signals = []

        # Get Bitcoin-related markets
        markets = await self.polymarket.get_bitcoin_markets()

        for market in markets:
            market_id = market.get('id', '')
            tokens = market.get('clobTokenIds', [])

            if len(tokens) < 2:
                continue

            # Check for arbitrage
            arb = await self.polymarket.check_yes_no_arbitrage(market_id)

            if arb and arb.get('profit_pct', 0) >= self.min_spread:
                signal = self._create_arb_signal(market, arb)
                if signal:
                    signals.append(signal)

        # Also check real-time monitored markets
        for arb in self.multi_book.scan_all_arbitrage():
            if arb.get('profit_pct', 0) >= self.min_spread:
                market_info = self._find_market_for_tokens(arb['yes_token'], arb['no_token'])
                if market_info:
                    signal = self._create_arb_signal(market_info, arb)
                    if signal:
                        signals.append(signal)

        # Sort by profit (best first)
        signals.sort(key=lambda s: s.expected_profit_pct, reverse=True)

        return signals

    def _find_market_for_tokens(self, yes_token: str, no_token: str) -> Optional[Dict]:
        """Find market info for token pair"""
        for market_id, info in self._monitored_markets.items():
            if info['yes_token'] == yes_token and info['no_token'] == no_token:
                return {'id': market_id, **info}
        return None

    def _create_arb_signal(self, market: Dict, arb: Dict) -> Optional[TradeSignal]:
        """Create arbitrage signal from opportunity"""
        arb_type = arb.get('type', '')
        profit_pct = arb.get('profit_pct', 0)

        if arb_type == 'BUY_BOTH':
            # Buy Yes and No for less than $1 total
            total_cost = arb.get('total_cost', 1)
            yes_price = arb.get('yes_price', 0)
            no_price = arb.get('no_price', 0)

            # Calculate how many pairs we can buy
            max_pairs = self.max_position / total_cost

            return TradeSignal(
                strategy_name=self.name,
                signal_type=SignalType.BUY,
                market_id=market.get('id', ''),
                token_id=arb.get('yes_token', ''),  # Primary token
                side="BOTH",
                price=total_cost,
                size=max_pairs,
                confidence=min(0.95, profit_pct / self.target_profit),
                expected_profit_pct=profit_pct,
                reason=f"Yes+No=${total_cost:.4f} < $1 (profit: {profit_pct:.2%})",
                metadata={
                    'arb_type': 'BUY_BOTH',
                    'yes_token': arb.get('yes_token'),
                    'no_token': arb.get('no_token'),
                    'yes_price': yes_price,
                    'no_price': no_price,
                    'total_cost': total_cost,
                    'question': market.get('question', '')
                }
            )

        elif arb_type == 'SELL_BOTH':
            # Sell Yes and No for more than $1 total (requires existing inventory or minting)
            total_value = arb.get('total_value', 1)
            yes_price = arb.get('yes_price', 0)
            no_price = arb.get('no_price', 0)

            return TradeSignal(
                strategy_name=self.name,
                signal_type=SignalType.SELL,
                market_id=market.get('id', ''),
                token_id=arb.get('yes_token', ''),
                side="BOTH",
                price=total_value,
                size=self.max_position,
                confidence=min(0.95, profit_pct / self.target_profit),
                expected_profit_pct=profit_pct,
                reason=f"Yes+No=${total_value:.4f} > $1 (profit: {profit_pct:.2%})",
                metadata={
                    'arb_type': 'SELL_BOTH',
                    'yes_token': arb.get('yes_token'),
                    'no_token': arb.get('no_token'),
                    'yes_price': yes_price,
                    'no_price': no_price,
                    'total_value': total_value,
                    'question': market.get('question', '')
                }
            )

        return None

    async def execute(self, signal: TradeSignal) -> StrategyResult:
        """
        Execute Yes/No arbitrage

        For BUY_BOTH: Buy equal amounts of Yes and No
        For SELL_BOTH: Sell existing positions or mint and sell
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
        arb_type = signal.metadata.get('arb_type')

        try:
            if arb_type == 'BUY_BOTH':
                result = await self._execute_buy_both(signal)
            elif arb_type == 'SELL_BOTH':
                result = await self._execute_sell_both(signal)
            else:
                return StrategyResult(
                    strategy_name=self.name,
                    success=False,
                    profit_loss=0,
                    trades_executed=0,
                    signals_generated=1,
                    duration_seconds=0,
                    errors=[f"Unknown arb type: {arb_type}"]
                )

            result.duration_seconds = (datetime.now() - start_time).total_seconds()
            return result

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

    async def _execute_buy_both(self, signal: TradeSignal) -> StrategyResult:
        """
        Execute BUY_BOTH arbitrage

        Buy equal amounts of Yes and No tokens for less than $1 total
        """
        yes_token = signal.metadata.get('yes_token')
        no_token = signal.metadata.get('no_token')
        yes_price = signal.metadata.get('yes_price')
        no_price = signal.metadata.get('no_price')

        # Calculate position size (equal dollar amounts on each side)
        # Total cost per pair = yes_price + no_price
        pairs_to_buy = min(signal.size, self.max_position / (yes_price + no_price))

        # Execute both orders (should be simultaneous for true arbitrage)
        yes_order = await self.polymarket.place_order(
            token_id=yes_token,
            side="BUY",
            price=yes_price,
            size=pairs_to_buy,
            order_type="FOK"
        )

        no_order = await self.polymarket.place_order(
            token_id=no_token,
            side="BUY",
            price=no_price,
            size=pairs_to_buy,
            order_type="FOK"
        )

        # Check results
        if yes_order.status in ['filled', 'simulated'] and no_order.status in ['filled', 'simulated']:
            # Calculate actual P&L
            yes_cost = yes_order.filled_amount * yes_order.avg_price
            no_cost = no_order.filled_amount * no_order.avg_price
            total_cost = yes_cost + no_cost

            # Guaranteed return = min(filled amounts) since one will pay $1
            min_filled = min(yes_order.filled_amount, no_order.filled_amount)
            guaranteed_return = min_filled
            profit = guaranteed_return - total_cost

            # Track arb
            self._active_arbs.append({
                'market_id': signal.market_id,
                'yes_shares': yes_order.filled_amount,
                'no_shares': no_order.filled_amount,
                'total_cost': total_cost,
                'expected_return': guaranteed_return,
                'expected_profit': profit,
                'timestamp': datetime.now()
            })

            logger.info(f"[{self.name}] Arb executed: ${total_cost:.2f} invested, "
                       f"${guaranteed_return:.2f} guaranteed (profit: ${profit:.2f})")

            self.update_metrics({
                'arb_type': 'BUY_BOTH',
                'profit_loss': profit,
                'invested': total_cost
            })

            return StrategyResult(
                strategy_name=self.name,
                success=True,
                profit_loss=profit,
                trades_executed=2,
                signals_generated=1,
                duration_seconds=0,
                details={
                    'yes_filled': yes_order.filled_amount,
                    'no_filled': no_order.filled_amount,
                    'total_cost': total_cost,
                    'profit': profit
                }
            )
        else:
            # One or both orders failed
            errors = []
            if yes_order.status not in ['filled', 'simulated']:
                errors.append(f"Yes order failed: {yes_order.status}")
            if no_order.status not in ['filled', 'simulated']:
                errors.append(f"No order failed: {no_order.status}")

            return StrategyResult(
                strategy_name=self.name,
                success=False,
                profit_loss=0,
                trades_executed=0,
                signals_generated=1,
                duration_seconds=0,
                errors=errors
            )

    async def _execute_sell_both(self, signal: TradeSignal) -> StrategyResult:
        """
        Execute SELL_BOTH arbitrage

        This requires either:
        1. Existing positions to sell, or
        2. Minting new shares (requires USDC) and selling
        """
        # For now, just attempt to sell existing positions
        # Full implementation would include minting

        yes_token = signal.metadata.get('yes_token')
        no_token = signal.metadata.get('no_token')

        # Check existing positions
        positions = await self.polymarket.get_positions()

        yes_pos = None
        no_pos = None

        for pos in positions:
            if pos.token_id == yes_token:
                yes_pos = pos
            elif pos.token_id == no_token:
                no_pos = pos

        if not yes_pos or not no_pos:
            return StrategyResult(
                strategy_name=self.name,
                success=False,
                profit_loss=0,
                trades_executed=0,
                signals_generated=1,
                duration_seconds=0,
                errors=["No existing positions to sell for SELL_BOTH arb"]
            )

        # Sell both positions
        sell_amount = min(yes_pos.size, no_pos.size)

        yes_order = await self.polymarket.place_order(
            token_id=yes_token,
            side="SELL",
            price=signal.metadata.get('yes_price'),
            size=sell_amount,
            order_type="FOK"
        )

        no_order = await self.polymarket.place_order(
            token_id=no_token,
            side="SELL",
            price=signal.metadata.get('no_price'),
            size=sell_amount,
            order_type="FOK"
        )

        if yes_order.status in ['filled', 'simulated'] and no_order.status in ['filled', 'simulated']:
            proceeds = (yes_order.filled_amount * yes_order.avg_price +
                       no_order.filled_amount * no_order.avg_price)
            # Cost basis would have been $1 per pair (if minted)
            profit = proceeds - min(yes_order.filled_amount, no_order.filled_amount)

            return StrategyResult(
                strategy_name=self.name,
                success=True,
                profit_loss=profit,
                trades_executed=2,
                signals_generated=1,
                duration_seconds=0,
                details={'proceeds': proceeds, 'profit': profit}
            )

        return StrategyResult(
            strategy_name=self.name,
            success=False,
            profit_loss=0,
            trades_executed=0,
            signals_generated=1,
            duration_seconds=0,
            errors=["Sell orders failed"]
        )

    async def close_position(self, market_id: str) -> StrategyResult:
        """Close arbitrage position by selling both sides"""
        # Find active arb for this market
        arb = None
        for a in self._active_arbs:
            if a.get('market_id') == market_id:
                arb = a
                break

        if not arb:
            return StrategyResult(
                strategy_name=self.name,
                success=False,
                profit_loss=0,
                trades_executed=0,
                signals_generated=0,
                duration_seconds=0,
                errors=["No active arb found for market"]
            )

        # Wait for resolution is usually better than selling early
        # But if requested, sell both sides

        logger.info(f"[{self.name}] Arb positions typically held to resolution")

        return StrategyResult(
            strategy_name=self.name,
            success=True,
            profit_loss=arb.get('expected_profit', 0),
            trades_executed=0,
            signals_generated=0,
            duration_seconds=0,
            details={'note': 'Hold to resolution for guaranteed profit'}
        )

    async def monitor_realtime(self, market_ids: List[str]):
        """
        Monitor markets in real-time for arbitrage opportunities

        Uses WebSocket orderbook feeds for instant detection
        """
        # Add markets to monitor
        for market_id in market_ids:
            await self.add_market_to_monitor(market_id)

        # Connect orderbooks
        await self.multi_book.connect_all()

        logger.info(f"[{self.name}] Real-time monitoring started for {len(market_ids)} markets")

        # Continuous monitoring loop
        while self.enabled:
            # Scan for opportunities
            arbs = self.multi_book.scan_all_arbitrage()

            for arb in arbs:
                if arb.get('profit_pct', 0) >= self.min_spread:
                    logger.info(f"[{self.name}] Real-time arb detected: {arb['type']} "
                               f"profit={arb['profit_pct']:.2%}")

                    # Create and execute signal
                    market_info = self._find_market_for_tokens(
                        arb.get('yes_token'),
                        arb.get('no_token')
                    )

                    if market_info:
                        signal = self._create_arb_signal(market_info, arb)
                        if signal and signal.is_actionable:
                            await self.execute(signal)

            await asyncio.sleep(0.1)  # 100ms between scans

        await self.multi_book.disconnect_all()
