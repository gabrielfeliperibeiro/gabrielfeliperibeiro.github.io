"""
Latency Arbitrage Strategy

Exploits the price lag between crypto exchanges and Polymarket prediction markets.
When BTC moves significantly on exchanges (e.g., Binance), Polymarket Bitcoin
prediction markets may not update immediately, creating arbitrage opportunities.

Key Tactics:
- Monitor exchanges for sudden price movements (impulses)
- Detect lagging Polymarket prices
- Execute large positions during the lag window
- Target 15-minute execution windows

Referenced Performance: $519,000 in 30 days from 10,985 trades
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from .base import StrategyBase, TradeSignal, StrategyResult, SignalType
from ..polymarket.client import PolymarketClient
from ..exchanges.aggregator import PriceAggregator, PriceImpulse
from ..exchanges.binance import BinanceConnector


class LatencyArbitrageStrategy(StrategyBase):
    """
    Latency Arbitrage: Exploit Polymarket price lag during crypto volatility

    This strategy monitors real-time crypto prices and compares them to
    Polymarket Bitcoin prediction market prices. When exchanges show
    significant movement, we bet on the direction before Polymarket catches up.
    """

    def __init__(
        self,
        polymarket: PolymarketClient,
        config: Dict[str, Any],
        dry_run: bool = True
    ):
        super().__init__("LatencyArbitrage", config, dry_run)

        self.polymarket = polymarket
        self.aggregator = PriceAggregator()
        self._binance = BinanceConnector()

        # Strategy parameters
        self.min_price_deviation = config.get('min_price_deviation', 0.02)  # 2%
        self.max_position_size = config.get('max_position_size', 35000)  # USD
        self.execution_window = config.get('execution_window_seconds', 900)  # 15 min
        self.target_keywords = config.get('target_markets', ['bitcoin', 'btc', 'crypto'])

        # State tracking
        self._pending_impulses: List[PriceImpulse] = []
        self._bitcoin_markets: List[Dict] = []
        self._last_btc_price: float = 0
        self._last_scan: Optional[datetime] = None

    async def initialize(self):
        """Initialize exchange connections and market data"""
        # Add Binance to price aggregator
        self.aggregator.add_exchange(self._binance)

        # Set up impulse detection
        self.aggregator.set_impulse_threshold(self.min_price_deviation)
        self.aggregator.add_impulse_callback(self._on_impulse_detected)

        # Connect to exchanges
        await self.aggregator.connect_all()
        await self.aggregator.subscribe_symbol("BTCUSDT")

        # Fetch Bitcoin-related markets from Polymarket
        self._bitcoin_markets = await self.polymarket.get_bitcoin_markets()

        logger.info(f"[{self.name}] Initialized with {len(self._bitcoin_markets)} Bitcoin markets")

    async def shutdown(self):
        """Clean up connections"""
        await self.aggregator.disconnect_all()

    async def _on_impulse_detected(self, impulse: PriceImpulse):
        """Handle detected price impulse from exchanges"""
        logger.info(f"[{self.name}] BTC impulse: {impulse.direction} {impulse.change_pct:.2%}")

        # Store impulse for processing
        self._pending_impulses.append(impulse)

        # Update reference price
        self._last_btc_price = impulse.to_price

    async def scan(self) -> List[TradeSignal]:
        """
        Scan for latency arbitrage opportunities

        Compares exchange BTC price movement to Polymarket prediction prices
        """
        signals = []

        if not self._pending_impulses:
            # No recent impulses to exploit
            return signals

        # Get current BTC reference price from exchanges
        btc_price = await self._binance.get_btc_reference_price()

        # Check each Bitcoin market on Polymarket
        for market in self._bitcoin_markets:
            try:
                signal = await self._analyze_market_lag(market, btc_price)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.debug(f"Error analyzing market {market.get('id')}: {e}")

        # Clear processed impulses older than execution window
        cutoff = datetime.now() - timedelta(seconds=self.execution_window)
        self._pending_impulses = [
            imp for imp in self._pending_impulses
            if imp.timestamp > cutoff
        ]

        return signals

    async def _analyze_market_lag(
        self,
        market: Dict,
        btc_reference_price: float
    ) -> Optional[TradeSignal]:
        """
        Analyze if a Polymarket market is lagging behind exchange prices

        Args:
            market: Polymarket market data
            btc_reference_price: Current BTC price from exchanges

        Returns:
            TradeSignal if opportunity found
        """
        market_id = market.get('id', '')
        question = market.get('question', '').lower()
        tokens = market.get('clobTokenIds', [])

        if not tokens:
            return None

        # Determine if this is a "BTC above X" or "BTC below X" market
        is_above_market = 'above' in question or 'over' in question or 'reach' in question
        is_below_market = 'below' in question or 'under' in question

        if not (is_above_market or is_below_market):
            return None

        # Extract price target from question (e.g., "BTC above $100,000")
        price_target = self._extract_price_target(question)
        if not price_target:
            return None

        # Get recent impulse direction
        if not self._pending_impulses:
            return None

        recent_impulse = self._pending_impulses[-1]
        impulse_direction = recent_impulse.direction

        # Determine expected outcome based on BTC movement
        # If BTC is rising and market is "above X", YES should increase
        # If BTC is falling and market is "below X", YES should increase

        yes_token = tokens[0]
        polymarket_prices = await self.polymarket.get_price(yes_token)
        current_poly_price = polymarket_prices['mid']

        # Calculate what the price "should" be based on exchange movement
        btc_to_target_ratio = btc_reference_price / price_target

        if is_above_market:
            # If BTC is above target, YES probability should be high
            implied_probability = min(0.95, max(0.05, btc_to_target_ratio))

            if impulse_direction == 'up':
                # BTC going up, YES should increase
                expected_direction = 'up'
                should_buy_yes = current_poly_price < implied_probability - 0.02
            else:
                # BTC going down, YES should decrease
                expected_direction = 'down'
                should_buy_yes = False
        else:
            # Below market - inverse logic
            implied_probability = min(0.95, max(0.05, 1 - btc_to_target_ratio))

            if impulse_direction == 'down':
                expected_direction = 'up'
                should_buy_yes = current_poly_price < implied_probability - 0.02
            else:
                expected_direction = 'down'
                should_buy_yes = False

        # Check for significant lag
        lag = abs(implied_probability - current_poly_price)

        if lag < self.min_price_deviation:
            return None

        if not should_buy_yes:
            # Could implement NO buying here, but focus on YES for simplicity
            return None

        # Calculate confidence based on impulse confirmation
        confidence = min(0.95, recent_impulse.confidence * (lag / self.min_price_deviation))

        # Calculate expected profit
        expected_profit = (implied_probability - current_poly_price) / current_poly_price

        # Generate signal
        signal = TradeSignal(
            strategy_name=self.name,
            signal_type=SignalType.BUY,
            market_id=market_id,
            token_id=yes_token,
            side="YES",
            price=current_poly_price,
            size=self.calculate_position_size(
                self.max_position_size,
                confidence,
                max_position_pct=1.0  # Can use full allocation for high-confidence arb
            ),
            confidence=confidence,
            expected_profit_pct=expected_profit,
            reason=f"BTC {impulse_direction} {recent_impulse.change_pct:.2%}, "
                   f"Polymarket lagging by {lag:.2%}",
            metadata={
                'btc_price': btc_reference_price,
                'price_target': price_target,
                'implied_probability': implied_probability,
                'lag': lag,
                'impulse': {
                    'direction': impulse_direction,
                    'change_pct': recent_impulse.change_pct,
                    'exchange': recent_impulse.exchange
                }
            }
        )

        logger.info(f"[{self.name}] Signal: {signal.reason}")
        return signal

    def _extract_price_target(self, question: str) -> Optional[float]:
        """Extract BTC price target from market question"""
        import re

        # Match patterns like "$100,000", "100k", "$100K"
        patterns = [
            r'\$?([\d,]+)(?:k|K)?',  # $100,000 or 100k
            r'(\d+)\s*(?:thousand|k)',  # 100 thousand or 100k
        ]

        for pattern in patterns:
            match = re.search(pattern, question)
            if match:
                value = match.group(1).replace(',', '')
                try:
                    price = float(value)
                    # Handle 'k' suffix
                    if 'k' in question.lower() and price < 1000:
                        price *= 1000
                    return price
                except ValueError:
                    continue

        return None

    async def execute(self, signal: TradeSignal) -> StrategyResult:
        """
        Execute latency arbitrage trade

        Args:
            signal: Trade signal to execute
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

        try:
            # Execute order on Polymarket
            order = await self.polymarket.place_order(
                token_id=signal.token_id,
                side="BUY",
                price=signal.price,
                size=signal.size,
                order_type="FOK"  # Fill or Kill for speed
            )

            if order.status in ['simulated', 'filled', 'partial']:
                # Calculate actual P&L
                trade_cost = order.filled_amount * order.avg_price
                expected_value = order.filled_amount  # Each share worth $1 if correct

                logger.info(f"[{self.name}] Executed: {order.filled_amount} shares @ ${order.avg_price:.4f}")

                # Update metrics
                self.update_metrics({
                    'market_id': signal.market_id,
                    'side': signal.side,
                    'size': order.filled_amount,
                    'price': order.avg_price,
                    'profit_loss': expected_value - trade_cost,  # Estimated
                    'signal': signal.metadata
                })

                return StrategyResult(
                    strategy_name=self.name,
                    success=True,
                    profit_loss=signal.expected_profit_pct * trade_cost,
                    trades_executed=1,
                    signals_generated=1,
                    duration_seconds=(datetime.now() - start_time).total_seconds(),
                    details={
                        'order_id': order.order_id,
                        'filled': order.filled_amount,
                        'avg_price': order.avg_price
                    }
                )
            else:
                return StrategyResult(
                    strategy_name=self.name,
                    success=False,
                    profit_loss=0,
                    trades_executed=0,
                    signals_generated=1,
                    duration_seconds=(datetime.now() - start_time).total_seconds(),
                    errors=[f"Order failed: {order.status}"]
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

    async def close_position(self, market_id: str) -> StrategyResult:
        """Close an existing position"""
        # Get current position
        positions = await self.polymarket.get_positions()

        for pos in positions:
            if pos.market_id == market_id:
                # Sell position
                order = await self.polymarket.place_market_order(
                    token_id=pos.token_id,
                    side="SELL",
                    size=pos.size
                )

                return StrategyResult(
                    strategy_name=self.name,
                    success=order.status in ['filled', 'simulated'],
                    profit_loss=pos.unrealized_pnl,
                    trades_executed=1,
                    signals_generated=0,
                    duration_seconds=0,
                    details={'closed_position': pos.token_id}
                )

        return StrategyResult(
            strategy_name=self.name,
            success=False,
            profit_loss=0,
            trades_executed=0,
            signals_generated=0,
            duration_seconds=0,
            errors=["Position not found"]
        )

    async def run_continuous(self, interval_seconds: float = 1.0):
        """
        Run strategy continuously

        Args:
            interval_seconds: Time between scans
        """
        await self.initialize()

        logger.info(f"[{self.name}] Starting continuous monitoring...")

        while self.enabled:
            try:
                result = await self.run_once()

                if result.trades_executed > 0:
                    logger.info(f"[{self.name}] Cycle: {result.trades_executed} trades, "
                               f"P&L: ${result.profit_loss:.2f}")

                await asyncio.sleep(interval_seconds)

            except Exception as e:
                logger.error(f"[{self.name}] Error in continuous run: {e}")
                await asyncio.sleep(5)

        await self.shutdown()
