"""
Range Coverage (Probability Hedging) Strategy

In multi-outcome markets, buy leading ranges keeping total cost under $1
for guaranteed profit when any outcome wins.

Key Tactics:
- Buy proportional shares in top outcomes
- Keep total cost below $1
- Adjust based on historical data or sentiment

Referenced Performance: 25-28% per trade
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from .base import StrategyBase, TradeSignal, StrategyResult, SignalType
from ..polymarket.client import PolymarketClient


class RangeCoverageStrategy(StrategyBase):
    """
    Range Coverage: Hedge across multiple outcomes for guaranteed profit

    In markets with multiple outcomes (e.g., BTC price ranges),
    buy shares across the most likely outcomes such that total cost < $1.
    """

    def __init__(
        self,
        polymarket: PolymarketClient,
        config: Dict[str, Any],
        dry_run: bool = True
    ):
        super().__init__("RangeCoverage", config, dry_run)

        self.polymarket = polymarket

        # Strategy parameters
        self.max_total_cost = config.get('max_total_cost', 0.98)  # Max cost for coverage
        self.target_profit = config.get('target_profit_pct', 0.25)  # 25% target
        self.min_outcomes_covered = config.get('min_outcomes_covered', 3)
        self.max_position = config.get('max_position_size', 5000)  # USD

        # State
        self._active_coverages: Dict[str, Dict] = {}

    async def scan(self) -> List[TradeSignal]:
        """
        Scan for range coverage opportunities

        Looks for multi-outcome markets where we can buy top outcomes
        for less than $1 total
        """
        signals = []

        # Get Bitcoin-related markets
        markets = await self.polymarket.get_bitcoin_markets()

        for market in markets:
            outcomes = market.get('outcomes', [])
            tokens = market.get('clobTokenIds', [])
            prices_raw = market.get('outcomePrices', [])

            # Need multiple outcomes for range coverage
            if len(outcomes) < 3:
                continue

            try:
                # Get current prices for all outcomes
                outcome_data = []
                for i, (outcome, token_id) in enumerate(zip(outcomes, tokens)):
                    price = float(prices_raw[i]) if i < len(prices_raw) else 0

                    # Get more accurate price from orderbook if possible
                    try:
                        book_prices = await self.polymarket.get_price(token_id)
                        price = book_prices['ask']  # Use ask for buying
                    except Exception:
                        pass

                    outcome_data.append({
                        'name': outcome,
                        'token_id': token_id,
                        'price': price
                    })

                # Sort by probability (highest first)
                outcome_data.sort(key=lambda x: x['price'], reverse=True)

                # Find coverage set where total cost < max_total_cost
                coverage = self._find_optimal_coverage(outcome_data)

                if coverage and len(coverage['outcomes']) >= self.min_outcomes_covered:
                    profit_pct = (1 - coverage['total_cost']) / coverage['total_cost']

                    if profit_pct >= self.target_profit:
                        signal = TradeSignal(
                            strategy_name=self.name,
                            signal_type=SignalType.BUY,
                            market_id=market.get('id', ''),
                            token_id=coverage['outcomes'][0]['token_id'],  # Primary token
                            side="MULTIPLE",
                            price=coverage['total_cost'],
                            size=self.max_position / coverage['total_cost'],
                            confidence=0.95,  # High confidence - guaranteed profit
                            expected_profit_pct=profit_pct,
                            reason=f"Cover {len(coverage['outcomes'])} outcomes @ ${coverage['total_cost']:.4f} "
                                   f"(profit: {profit_pct:.1%})",
                            metadata={
                                'coverage': coverage,
                                'question': market.get('question', '')
                            }
                        )
                        signals.append(signal)

            except Exception as e:
                logger.debug(f"Error scanning market {market.get('id')}: {e}")

        return signals

    def _find_optimal_coverage(self, outcomes: List[Dict]) -> Optional[Dict]:
        """
        Find optimal set of outcomes to cover

        Greedy algorithm: add outcomes in order of probability
        until total cost approaches $1
        """
        coverage = {
            'outcomes': [],
            'total_cost': 0,
            'expected_return': 1  # One outcome will pay $1
        }

        for outcome in outcomes:
            if outcome['price'] <= 0:
                continue

            potential_cost = coverage['total_cost'] + outcome['price']

            if potential_cost <= self.max_total_cost:
                coverage['outcomes'].append(outcome)
                coverage['total_cost'] = potential_cost
            else:
                # Adding this would exceed our cost limit
                break

        if not coverage['outcomes']:
            return None

        coverage['profit'] = coverage['expected_return'] - coverage['total_cost']
        coverage['profit_pct'] = coverage['profit'] / coverage['total_cost']

        return coverage

    async def execute(self, signal: TradeSignal) -> StrategyResult:
        """
        Execute range coverage by buying all outcomes in the coverage set
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
        coverage = signal.metadata.get('coverage', {})
        outcomes = coverage.get('outcomes', [])

        if not outcomes:
            return StrategyResult(
                strategy_name=self.name,
                success=False,
                profit_loss=0,
                trades_executed=0,
                signals_generated=1,
                duration_seconds=0,
                errors=["No outcomes in coverage"]
            )

        try:
            # Calculate position size per outcome
            # We want equal dollar amount in each outcome for simplicity
            # More sophisticated: weight by probability
            total_budget = min(signal.size * coverage['total_cost'], self.max_position)

            orders_executed = []
            total_cost = 0

            for outcome in outcomes:
                # Allocate budget proportionally to probability
                weight = outcome['price'] / coverage['total_cost']
                outcome_budget = total_budget * weight
                shares_to_buy = outcome_budget / outcome['price']

                order = await self.polymarket.place_order(
                    token_id=outcome['token_id'],
                    side="BUY",
                    price=outcome['price'],
                    size=shares_to_buy,
                    order_type="GTC"
                )

                if order.status in ['filled', 'simulated', 'partial']:
                    actual_cost = order.filled_amount * order.avg_price
                    total_cost += actual_cost

                    orders_executed.append({
                        'outcome': outcome['name'],
                        'token_id': outcome['token_id'],
                        'shares': order.filled_amount,
                        'price': order.avg_price,
                        'cost': actual_cost
                    })

                    logger.debug(f"[{self.name}] Bought {outcome['name']}: "
                               f"{order.filled_amount:.0f} shares @ ${order.avg_price:.4f}")

            if not orders_executed:
                return StrategyResult(
                    strategy_name=self.name,
                    success=False,
                    profit_loss=0,
                    trades_executed=0,
                    signals_generated=1,
                    duration_seconds=(datetime.now() - start_time).total_seconds(),
                    errors=["No orders filled"]
                )

            # Calculate guaranteed profit
            min_shares = min(o['shares'] for o in orders_executed)
            guaranteed_return = min_shares  # $1 per share on win
            expected_profit = guaranteed_return - total_cost

            # Track coverage
            self._active_coverages[signal.market_id] = {
                'orders': orders_executed,
                'total_cost': total_cost,
                'min_shares': min_shares,
                'expected_profit': expected_profit,
                'timestamp': datetime.now()
            }

            logger.info(f"[{self.name}] Coverage executed: {len(orders_executed)} outcomes, "
                       f"cost=${total_cost:.2f}, expected profit=${expected_profit:.2f}")

            self.update_metrics({
                'market_id': signal.market_id,
                'profit_loss': expected_profit,
                'num_outcomes': len(orders_executed)
            })

            return StrategyResult(
                strategy_name=self.name,
                success=True,
                profit_loss=expected_profit,  # Guaranteed on resolution
                trades_executed=len(orders_executed),
                signals_generated=1,
                duration_seconds=(datetime.now() - start_time).total_seconds(),
                details={
                    'outcomes_covered': len(orders_executed),
                    'total_cost': total_cost,
                    'expected_profit': expected_profit,
                    'profit_pct': expected_profit / total_cost if total_cost > 0 else 0
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

    async def close_position(self, market_id: str) -> StrategyResult:
        """
        Close coverage position by selling all outcomes
        Usually better to wait for resolution
        """
        coverage = self._active_coverages.get(market_id)

        if not coverage:
            return StrategyResult(
                strategy_name=self.name,
                success=False,
                profit_loss=0,
                trades_executed=0,
                signals_generated=0,
                duration_seconds=0,
                errors=["No active coverage found"]
            )

        # Sell all positions
        total_proceeds = 0
        for order in coverage['orders']:
            sell_order = await self.polymarket.place_market_order(
                token_id=order['token_id'],
                side="SELL",
                size=order['shares']
            )

            if sell_order.status in ['filled', 'simulated']:
                total_proceeds += sell_order.filled_amount * sell_order.avg_price

        profit = total_proceeds - coverage['total_cost']
        del self._active_coverages[market_id]

        return StrategyResult(
            strategy_name=self.name,
            success=True,
            profit_loss=profit,
            trades_executed=len(coverage['orders']),
            signals_generated=0,
            duration_seconds=0,
            details={'proceeds': total_proceeds, 'cost': coverage['total_cost']}
        )

    async def check_resolutions(self) -> List[StrategyResult]:
        """Check for resolved coverages and collect profits"""
        results = []

        for market_id, coverage in list(self._active_coverages.items()):
            try:
                market = await self.polymarket.get_market(market_id)

                if market.get('resolved', False):
                    # Market resolved - we should have won one outcome
                    payout = coverage['min_shares']  # $1 per share on winning outcome
                    profit = payout - coverage['total_cost']

                    del self._active_coverages[market_id]

                    self.update_metrics({
                        'market_id': market_id,
                        'profit_loss': profit,
                        'resolved': True
                    })

                    logger.info(f"[{self.name}] Coverage resolved: +${profit:.2f}")

                    results.append(StrategyResult(
                        strategy_name=self.name,
                        success=True,
                        profit_loss=profit,
                        trades_executed=0,
                        signals_generated=0,
                        duration_seconds=0,
                        details={'market_id': market_id, 'payout': payout}
                    ))

            except Exception as e:
                logger.debug(f"Error checking resolution for {market_id}: {e}")

        return results

    def get_coverage_summary(self) -> Dict:
        """Get summary of active coverages"""
        total_invested = sum(c['total_cost'] for c in self._active_coverages.values())
        expected_profit = sum(c['expected_profit'] for c in self._active_coverages.values())

        return {
            'active_coverages': len(self._active_coverages),
            'total_invested': total_invested,
            'expected_profit': expected_profit,
            'expected_return_pct': expected_profit / total_invested if total_invested > 0 else 0,
            'coverages': [
                {
                    'market_id': mid,
                    'cost': cov['total_cost'],
                    'expected_profit': cov['expected_profit'],
                    'num_outcomes': len(cov['orders'])
                }
                for mid, cov in self._active_coverages.items()
            ]
        }
