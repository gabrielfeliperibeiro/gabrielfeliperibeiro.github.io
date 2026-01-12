"""
Compounding Strategy

Reinvest all profits immediately for exponential growth.
Combines multiple sub-strategies and compounds returns.

Referenced Performance: $14,437 gains with 99.6% win rate
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from .base import StrategyBase, TradeSignal, StrategyResult, SignalType


class CompoundingStrategy(StrategyBase):
    """
    Compounding: Automatic profit reinvestment for exponential growth

    Manages capital across multiple strategies, automatically
    reinvesting profits into new opportunities.
    """

    def __init__(
        self,
        strategies: List[StrategyBase],
        config: Dict[str, Any],
        dry_run: bool = True
    ):
        super().__init__("Compounding", config, dry_run)

        self.strategies = strategies

        # Compounding parameters
        self.target_win_rate = config.get('target_win_rate', 0.996)  # 99.6%
        self.min_certainty = config.get('min_certainty', 0.95)
        self.max_position_pct = config.get('max_position_pct', 0.1)  # 10% max per bet
        self.compound_frequency = config.get('compound_frequency', 'immediate')

        # Capital management
        self._initial_capital = 0.0
        self._current_capital = 0.0
        self._peak_capital = 0.0
        self._total_profit = 0.0
        self._trade_count = 0
        self._win_count = 0

        # Performance tracking
        self._capital_history: List[Dict] = []

    def set_initial_capital(self, amount: float):
        """Set starting capital"""
        self._initial_capital = amount
        self._current_capital = amount
        self._peak_capital = amount

        self._capital_history.append({
            'timestamp': datetime.now(),
            'capital': amount,
            'event': 'initial'
        })

        logger.info(f"[{self.name}] Initial capital: ${amount:,.2f}")

    async def scan(self) -> List[TradeSignal]:
        """
        Aggregate signals from all sub-strategies

        Filter for high-confidence opportunities suitable for compounding
        """
        all_signals = []

        for strategy in self.strategies:
            if strategy.enabled:
                try:
                    signals = await strategy.scan()

                    # Filter for high-confidence signals
                    for signal in signals:
                        if signal.confidence >= self.min_certainty:
                            # Adjust position size based on available capital
                            signal.size = self.calculate_compound_size(
                                signal.confidence,
                                signal.expected_profit_pct
                            )

                            if signal.size > 0:
                                all_signals.append(signal)

                except Exception as e:
                    logger.error(f"[{self.name}] Error scanning {strategy.name}: {e}")

        # Sort by expected value (confidence * profit)
        all_signals.sort(
            key=lambda s: s.confidence * s.expected_profit_pct,
            reverse=True
        )

        return all_signals

    def calculate_compound_size(self, confidence: float, expected_profit: float) -> float:
        """
        Calculate optimal position size for compounding

        Uses fractional Kelly criterion for risk management
        """
        if self._current_capital <= 0:
            return 0

        # Kelly fraction: f = (p * b - q) / b
        # where p = win probability, q = 1-p, b = profit/loss ratio
        p = confidence
        q = 1 - p

        # Assume loss is full position (conservative)
        b = expected_profit / 1 if expected_profit > 0 else 0.01

        kelly = (p * b - q) / b if b > 0 else 0

        # Use half-Kelly for safety
        kelly_fraction = max(0, kelly * 0.5)

        # Cap at max position percentage
        position_pct = min(kelly_fraction, self.max_position_pct)

        return self._current_capital * position_pct

    async def execute(self, signal: TradeSignal) -> StrategyResult:
        """
        Execute signal using appropriate sub-strategy
        """
        # Find the strategy that generated this signal
        target_strategy = None
        for strategy in self.strategies:
            if strategy.name == signal.strategy_name:
                target_strategy = strategy
                break

        if not target_strategy:
            return StrategyResult(
                strategy_name=self.name,
                success=False,
                profit_loss=0,
                trades_executed=0,
                signals_generated=1,
                duration_seconds=0,
                errors=[f"Strategy not found: {signal.strategy_name}"]
            )

        # Execute through sub-strategy
        result = await target_strategy.execute(signal)

        # Update capital based on result
        if result.success:
            self._process_result(result, signal)

        return result

    def _process_result(self, result: StrategyResult, signal: TradeSignal):
        """Process execution result and update capital"""
        self._trade_count += 1

        if result.profit_loss >= 0:
            self._win_count += 1
            self._total_profit += result.profit_loss
            self._current_capital += result.profit_loss

            # Compound immediately
            if self.compound_frequency == 'immediate':
                logger.debug(f"[{self.name}] Compounded: +${result.profit_loss:.2f} "
                           f"-> ${self._current_capital:,.2f}")
        else:
            self._current_capital += result.profit_loss  # Negative

        # Track peak
        self._peak_capital = max(self._peak_capital, self._current_capital)

        # Record history
        self._capital_history.append({
            'timestamp': datetime.now(),
            'capital': self._current_capital,
            'profit_loss': result.profit_loss,
            'strategy': signal.strategy_name,
            'event': 'trade'
        })

        # Update metrics
        self.update_metrics({
            'profit_loss': result.profit_loss,
            'strategy': signal.strategy_name
        })

    async def close_position(self, market_id: str) -> StrategyResult:
        """Close position across all strategies"""
        results = []

        for strategy in self.strategies:
            try:
                result = await strategy.close_position(market_id)
                if result.success:
                    results.append(result)
            except Exception:
                pass

        total_pnl = sum(r.profit_loss for r in results)

        return StrategyResult(
            strategy_name=self.name,
            success=bool(results),
            profit_loss=total_pnl,
            trades_executed=sum(r.trades_executed for r in results),
            signals_generated=0,
            duration_seconds=0
        )

    @property
    def win_rate(self) -> float:
        """Current win rate"""
        if self._trade_count == 0:
            return 0
        return self._win_count / self._trade_count

    @property
    def total_return(self) -> float:
        """Total return percentage"""
        if self._initial_capital == 0:
            return 0
        return (self._current_capital - self._initial_capital) / self._initial_capital

    @property
    def max_drawdown(self) -> float:
        """Maximum drawdown from peak"""
        if self._peak_capital == 0:
            return 0
        return (self._peak_capital - self._current_capital) / self._peak_capital

    def get_performance_summary(self) -> Dict:
        """Get comprehensive performance summary"""
        return {
            'initial_capital': self._initial_capital,
            'current_capital': self._current_capital,
            'peak_capital': self._peak_capital,
            'total_profit': self._total_profit,
            'total_return': f"{self.total_return:.1%}",
            'trade_count': self._trade_count,
            'win_count': self._win_count,
            'win_rate': f"{self.win_rate:.1%}",
            'max_drawdown': f"{self.max_drawdown:.1%}",
            'strategies': {
                s.name: {
                    'enabled': s.enabled,
                    'trades': s.metrics.total_trades,
                    'win_rate': f"{s.metrics.win_rate:.1%}",
                    'net_profit': s.metrics.net_profit
                }
                for s in self.strategies
            }
        }

    def get_growth_projection(self, daily_trades: int = 10, days: int = 30) -> Dict:
        """
        Project future growth based on current performance

        Args:
            daily_trades: Expected trades per day
            days: Projection period

        Returns:
            Projected capital and returns
        """
        if self._trade_count == 0:
            return {'error': 'No trade history'}

        avg_profit_per_trade = self._total_profit / self._trade_count if self._trade_count > 0 else 0
        avg_return_per_trade = avg_profit_per_trade / self._current_capital if self._current_capital > 0 else 0

        projected_capital = self._current_capital
        projections = []

        for day in range(1, days + 1):
            daily_return = (1 + avg_return_per_trade * self.win_rate) ** daily_trades
            projected_capital *= daily_return

            projections.append({
                'day': day,
                'capital': projected_capital,
                'total_return': (projected_capital - self._initial_capital) / self._initial_capital
            })

        return {
            'current_capital': self._current_capital,
            'avg_profit_per_trade': avg_profit_per_trade,
            'win_rate': self.win_rate,
            'daily_trades': daily_trades,
            'projections': projections,
            'final_projected_capital': projected_capital,
            'projected_return': (projected_capital - self._initial_capital) / self._initial_capital
        }

    async def run_compounding_session(
        self,
        initial_capital: float,
        duration_hours: float = 24,
        scan_interval: int = 60
    ):
        """
        Run a continuous compounding session

        Args:
            initial_capital: Starting capital
            duration_hours: How long to run
            scan_interval: Seconds between scans
        """
        self.set_initial_capital(initial_capital)

        start_time = datetime.now()
        end_time = start_time.timestamp() + (duration_hours * 3600)

        logger.info(f"[{self.name}] Starting compounding session for {duration_hours}h")
        logger.info(f"[{self.name}] Sub-strategies: {[s.name for s in self.strategies]}")

        while datetime.now().timestamp() < end_time and self.enabled:
            try:
                result = await self.run_once()

                if result.trades_executed > 0:
                    summary = self.get_performance_summary()
                    logger.info(f"[{self.name}] Session update: "
                               f"Capital=${summary['current_capital']:,.2f} "
                               f"({summary['total_return']} return), "
                               f"Win rate={summary['win_rate']}")

                await asyncio.sleep(scan_interval)

            except Exception as e:
                logger.error(f"[{self.name}] Session error: {e}")
                await asyncio.sleep(30)

        # Final summary
        final_summary = self.get_performance_summary()
        logger.info(f"[{self.name}] Session complete!")
        logger.info(f"[{self.name}] Final capital: ${final_summary['current_capital']:,.2f}")
        logger.info(f"[{self.name}] Total return: {final_summary['total_return']}")
        logger.info(f"[{self.name}] Win rate: {final_summary['win_rate']} ({self._win_count}/{self._trade_count})")

        return final_summary
