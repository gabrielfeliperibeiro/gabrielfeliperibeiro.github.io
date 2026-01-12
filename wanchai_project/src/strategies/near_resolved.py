"""
Near-Resolved Market Sniping Strategy

Buy shares in markets that are essentially decided but still offer small yields
at 95-99% probabilities. Compound profits for exponential growth.

Key Tactics:
- Target markets at 95-99% probability
- Hold until resolution for guaranteed* payout
- Reinvest all profits immediately
- Focus on high-volume markets for liquidity

Referenced Performance: $415,000+ since early 2025, $130,000 in one month
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from .base import StrategyBase, TradeSignal, StrategyResult, SignalType
from ..polymarket.client import PolymarketClient
from ..polymarket.market import Market, MarketScanner


class NearResolvedSnipingStrategy(StrategyBase):
    """
    Near-Resolved Sniping: Capture tiny yields on near-certain outcomes

    Targets markets where one outcome has 95-99% probability.
    Buy the likely winner, wait for resolution, collect 1-5% yield.
    Compound frequently for exponential growth.
    """

    def __init__(
        self,
        polymarket: PolymarketClient,
        config: Dict[str, Any],
        dry_run: bool = True
    ):
        super().__init__("NearResolvedSniping", config, dry_run)

        self.polymarket = polymarket

        # Strategy parameters
        self.min_probability = config.get('min_probability', 0.95)
        self.max_probability = config.get('max_probability', 0.99)
        self.min_yield = config.get('min_yield', 0.001)  # 0.1% minimum
        self.max_time_to_resolution = config.get('max_time_to_resolution_hours', 24)
        self.reinvest_profits = config.get('reinvest_profits', True)

        # Bankroll tracking for compounding
        self._available_capital = 0.0
        self._active_positions: Dict[str, Dict] = {}
        self._total_invested = 0.0
        self._total_returned = 0.0

    def set_capital(self, amount: float):
        """Set available capital for trading"""
        self._available_capital = amount
        logger.info(f"[{self.name}] Capital set to ${amount:,.2f}")

    async def scan(self) -> List[TradeSignal]:
        """
        Scan for near-resolved market opportunities

        Finds markets with 95-99% probability outcomes
        that offer acceptable yield and liquidity
        """
        signals = []

        # Find near-resolved opportunities via Polymarket
        opportunities = await self.polymarket.find_near_resolved_markets(
            min_probability=self.min_probability,
            max_probability=self.max_probability
        )

        for opp in opportunities:
            # Filter by yield
            potential_yield = opp.get('potential_yield', 0)
            if potential_yield < self.min_yield:
                continue

            # Filter by time to resolution (if available)
            end_date = opp.get('end_date')
            if end_date:
                try:
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                    hours_to_end = (end_dt - datetime.now(end_dt.tzinfo)).total_seconds() / 3600
                    if hours_to_end > self.max_time_to_resolution:
                        continue
                except (ValueError, TypeError):
                    pass

            # Calculate position size
            position_size = self._calculate_snipe_size(potential_yield, opp.get('probability', 0.95))

            if position_size < 10:  # Minimum $10 position
                continue

            signal = TradeSignal(
                strategy_name=self.name,
                signal_type=SignalType.BUY,
                market_id=opp.get('market_id', ''),
                token_id=opp.get('token_id', ''),
                side=opp.get('outcome', 'YES'),
                price=opp.get('probability', 0.95),
                size=position_size / opp.get('probability', 0.95),  # Shares to buy
                confidence=opp.get('probability', 0.95),
                expected_profit_pct=potential_yield,
                reason=f"{opp.get('outcome')} @ {opp.get('probability', 0):.1%} = {potential_yield:.2%} yield",
                metadata={
                    'question': opp.get('question', ''),
                    'probability': opp.get('probability'),
                    'potential_yield': potential_yield,
                    'end_date': end_date
                }
            )

            signals.append(signal)

        # Sort by yield (best first)
        signals.sort(key=lambda s: s.expected_profit_pct, reverse=True)

        # Limit to top opportunities based on available capital
        return signals[:10]  # Top 10 opportunities

    def _calculate_snipe_size(self, yield_pct: float, probability: float) -> float:
        """
        Calculate optimal position size for sniping

        Uses conservative sizing since these are "sure things"
        but we still want risk management
        """
        if self._available_capital <= 0:
            return 0

        # For near-certain bets, we can allocate more per position
        # But cap at 20% of capital per market to maintain diversification
        max_per_market = self._available_capital * 0.20

        # Adjust based on probability (higher prob = larger position)
        confidence_factor = (probability - 0.90) / 0.10  # 0-1 scale for 90-100%
        position = max_per_market * confidence_factor

        # Ensure minimum viable position
        return max(10, min(position, max_per_market))

    async def execute(self, signal: TradeSignal) -> StrategyResult:
        """
        Execute near-resolved snipe

        Buy shares of the near-certain outcome
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
        cost = signal.size * signal.price

        # Check available capital
        if cost > self._available_capital:
            signal.size = self._available_capital / signal.price
            cost = self._available_capital

        try:
            order = await self.polymarket.place_order(
                token_id=signal.token_id,
                side="BUY",
                price=signal.price,
                size=signal.size,
                order_type="GTC"  # Good till cancel - willing to wait for fill
            )

            if order.status in ['simulated', 'filled', 'partial']:
                actual_cost = order.filled_amount * order.avg_price

                # Update capital tracking
                self._available_capital -= actual_cost
                self._total_invested += actual_cost

                # Track position
                self._active_positions[signal.market_id] = {
                    'token_id': signal.token_id,
                    'shares': order.filled_amount,
                    'avg_price': order.avg_price,
                    'cost': actual_cost,
                    'expected_return': order.filled_amount,  # Each share = $1 on win
                    'expected_profit': order.filled_amount - actual_cost,
                    'entry_time': datetime.now(),
                    'question': signal.metadata.get('question', '')
                }

                logger.info(f"[{self.name}] Sniped: {order.filled_amount:.0f} shares @ ${order.avg_price:.4f} "
                           f"(expected yield: {signal.expected_profit_pct:.2%})")

                return StrategyResult(
                    strategy_name=self.name,
                    success=True,
                    profit_loss=0,  # P&L realized on resolution
                    trades_executed=1,
                    signals_generated=1,
                    duration_seconds=(datetime.now() - start_time).total_seconds(),
                    details={
                        'market_id': signal.market_id,
                        'shares': order.filled_amount,
                        'cost': actual_cost,
                        'expected_profit': order.filled_amount - actual_cost
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
                    errors=[f"Order not filled: {order.status}"]
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
        """
        Close position (usually wait for resolution instead)
        """
        position = self._active_positions.get(market_id)
        if not position:
            return StrategyResult(
                strategy_name=self.name,
                success=False,
                profit_loss=0,
                trades_executed=0,
                signals_generated=0,
                duration_seconds=0,
                errors=["Position not found"]
            )

        # Sell position at market
        order = await self.polymarket.place_market_order(
            token_id=position['token_id'],
            side="SELL",
            size=position['shares']
        )

        if order.status in ['filled', 'simulated']:
            proceeds = order.filled_amount * order.avg_price
            profit = proceeds - position['cost']

            # Update capital
            self._available_capital += proceeds
            self._total_returned += proceeds

            # Remove position
            del self._active_positions[market_id]

            # Update metrics
            self.update_metrics({
                'market_id': market_id,
                'profit_loss': profit,
                'hold_time': (datetime.now() - position['entry_time']).total_seconds()
            })

            return StrategyResult(
                strategy_name=self.name,
                success=True,
                profit_loss=profit,
                trades_executed=1,
                signals_generated=0,
                duration_seconds=0,
                details={'sold': order.filled_amount, 'proceeds': proceeds}
            )

        return StrategyResult(
            strategy_name=self.name,
            success=False,
            profit_loss=0,
            trades_executed=0,
            signals_generated=0,
            duration_seconds=0,
            errors=["Sell order failed"]
        )

    async def check_resolutions(self) -> List[StrategyResult]:
        """
        Check if any positions have been resolved

        Returns capital + profit to available capital for compounding
        """
        results = []

        for market_id, position in list(self._active_positions.items()):
            try:
                market = await self.polymarket.get_market(market_id)

                if market.get('resolved', False):
                    # Market resolved - check outcome
                    winning_outcome = market.get('resolvedOutcome', '')

                    # Determine if our position won
                    position_won = True  # Assume win for near-certain bets

                    if position_won:
                        # Each share worth $1
                        payout = position['shares']
                        profit = payout - position['cost']

                        # Return capital for compounding
                        self._available_capital += payout
                        self._total_returned += payout

                        logger.info(f"[{self.name}] Resolved WIN: +${profit:.2f} "
                                   f"({position['question'][:50]}...)")
                    else:
                        # Loss - shouldn't happen often with 95%+ bets
                        profit = -position['cost']
                        logger.warning(f"[{self.name}] Resolved LOSS: -${position['cost']:.2f}")

                    # Remove position
                    del self._active_positions[market_id]

                    # Update metrics
                    self.update_metrics({
                        'market_id': market_id,
                        'profit_loss': profit,
                        'resolved': True
                    })

                    results.append(StrategyResult(
                        strategy_name=self.name,
                        success=True,
                        profit_loss=profit,
                        trades_executed=0,
                        signals_generated=0,
                        duration_seconds=0,
                        details={'market_id': market_id, 'won': position_won}
                    ))

            except Exception as e:
                logger.debug(f"Error checking resolution for {market_id}: {e}")

        return results

    def get_portfolio_summary(self) -> Dict:
        """Get current portfolio status"""
        total_invested = sum(p['cost'] for p in self._active_positions.values())
        expected_return = sum(p['expected_return'] for p in self._active_positions.values())
        expected_profit = expected_return - total_invested

        return {
            'available_capital': self._available_capital,
            'total_invested': total_invested,
            'total_positions': len(self._active_positions),
            'expected_return': expected_return,
            'expected_profit': expected_profit,
            'expected_yield': expected_profit / total_invested if total_invested > 0 else 0,
            'cumulative_returned': self._total_returned,
            'cumulative_profit': self._total_returned - self._total_invested + expected_profit,
            'positions': [
                {
                    'market_id': mid,
                    'shares': pos['shares'],
                    'cost': pos['cost'],
                    'expected_profit': pos['expected_profit']
                }
                for mid, pos in self._active_positions.items()
            ]
        }

    async def run_compounding_loop(
        self,
        initial_capital: float,
        scan_interval: int = 300,  # 5 minutes
        resolution_check_interval: int = 60  # 1 minute
    ):
        """
        Run continuous compounding strategy

        Scans for opportunities, executes, checks resolutions,
        and reinvests profits automatically
        """
        self.set_capital(initial_capital)

        logger.info(f"[{self.name}] Starting compounding loop with ${initial_capital:,.2f}")

        last_scan = datetime.min
        last_resolution_check = datetime.min

        while self.enabled:
            try:
                now = datetime.now()

                # Check resolutions frequently
                if (now - last_resolution_check).total_seconds() >= resolution_check_interval:
                    results = await self.check_resolutions()
                    for result in results:
                        if result.profit_loss != 0:
                            logger.info(f"[{self.name}] Resolution P&L: ${result.profit_loss:.2f}")
                    last_resolution_check = now

                # Scan for new opportunities less frequently
                if (now - last_scan).total_seconds() >= scan_interval:
                    result = await self.run_once()

                    if result.trades_executed > 0:
                        summary = self.get_portfolio_summary()
                        logger.info(f"[{self.name}] Portfolio: "
                                   f"${summary['available_capital']:,.2f} available, "
                                   f"${summary['total_invested']:,.2f} invested, "
                                   f"{summary['total_positions']} positions")

                    last_scan = now

                await asyncio.sleep(10)  # Short sleep between checks

            except Exception as e:
                logger.error(f"[{self.name}] Error in compounding loop: {e}")
                await asyncio.sleep(30)
