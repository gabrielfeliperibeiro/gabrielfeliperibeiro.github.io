"""
Wanchai Arbitrage Bot - Main Orchestrator

Coordinates all strategies for Bitcoin market arbitrage on Polymarket.
"""

import asyncio
import os
import signal
from datetime import datetime
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv
from loguru import logger

from .polymarket import PolymarketClient
from .exchanges import BinanceConnector, PriceAggregator
from .strategies import (
    LatencyArbitrageStrategy,
    NearResolvedSnipingStrategy,
    YesNoArbitrageStrategy,
    SpreadTradingStrategy,
    RangeCoverageStrategy,
    CompoundingStrategy
)


class WanchaiBot:
    """
    Main arbitrage bot orchestrator

    Manages:
    - Polymarket API connections
    - Exchange price feeds
    - Multiple arbitrage strategies
    - Risk management
    - Performance reporting
    """

    def __init__(
        self,
        config_path: str = "config/config.yaml",
        dry_run: bool = True
    ):
        self.config_path = config_path
        self.dry_run = dry_run
        self.config = self._load_config()

        # Components
        self.polymarket: Optional[PolymarketClient] = None
        self.price_aggregator: Optional[PriceAggregator] = None
        self.strategies: Dict[str, Any] = {}

        # State
        self._running = False
        self._start_time: Optional[datetime] = None
        self._total_profit = 0.0
        self._trade_count = 0

        # Load environment variables
        load_dotenv()

    def _load_config(self) -> Dict:
        """Load configuration from YAML file"""
        try:
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning(f"Config file not found: {self.config_path}, using defaults")
            return self._default_config()

    def _default_config(self) -> Dict:
        """Return default configuration"""
        return {
            'bot': {
                'name': 'Wanchai Arbitrage Bot',
                'dry_run': True
            },
            'strategies': {
                'latency_arbitrage': {'enabled': True},
                'near_resolved_sniping': {'enabled': True},
                'yes_no_arbitrage': {'enabled': True},
                'spread_trading': {'enabled': True},
                'range_coverage': {'enabled': True}
            },
            'risk': {
                'max_daily_loss': 5000,
                'max_position_size': 50000
            }
        }

    async def initialize(self):
        """Initialize all bot components"""
        logger.info("=" * 60)
        logger.info("Wanchai Arbitrage Bot - Initializing")
        logger.info("=" * 60)

        # Initialize Polymarket client
        self.polymarket = PolymarketClient(
            api_key=os.getenv('POLYMARKET_API_KEY', ''),
            api_secret=os.getenv('POLYMARKET_API_SECRET', ''),
            api_passphrase=os.getenv('POLYMARKET_API_PASSPHRASE', ''),
            dry_run=self.dry_run
        )
        await self.polymarket.connect()
        logger.info("Polymarket client connected")

        # Initialize price aggregator
        self.price_aggregator = PriceAggregator()

        # Add Binance connector
        binance = BinanceConnector(
            api_key=os.getenv('BINANCE_API_KEY', ''),
            api_secret=os.getenv('BINANCE_API_SECRET', '')
        )
        self.price_aggregator.add_exchange(binance)
        await self.price_aggregator.connect_all()
        logger.info("Exchange connectors initialized")

        # Initialize strategies
        await self._initialize_strategies()

        logger.info(f"Initialization complete - {len(self.strategies)} strategies active")
        logger.info(f"Dry run mode: {self.dry_run}")

    async def _initialize_strategies(self):
        """Initialize all trading strategies"""
        strategy_configs = self.config.get('strategies', {})

        # 1. Latency Arbitrage Strategy
        if strategy_configs.get('latency_arbitrage', {}).get('enabled', True):
            self.strategies['latency_arbitrage'] = LatencyArbitrageStrategy(
                polymarket=self.polymarket,
                config=strategy_configs.get('latency_arbitrage', {}),
                dry_run=self.dry_run
            )
            await self.strategies['latency_arbitrage'].initialize()
            logger.info("Strategy enabled: Latency Arbitrage")

        # 2. Near-Resolved Sniping Strategy
        if strategy_configs.get('near_resolved_sniping', {}).get('enabled', True):
            self.strategies['near_resolved'] = NearResolvedSnipingStrategy(
                polymarket=self.polymarket,
                config=strategy_configs.get('near_resolved_sniping', {}),
                dry_run=self.dry_run
            )
            logger.info("Strategy enabled: Near-Resolved Sniping")

        # 3. Yes/No Arbitrage Strategy
        if strategy_configs.get('yes_no_arbitrage', {}).get('enabled', True):
            self.strategies['yes_no_arb'] = YesNoArbitrageStrategy(
                polymarket=self.polymarket,
                config=strategy_configs.get('yes_no_arbitrage', {}),
                dry_run=self.dry_run
            )
            logger.info("Strategy enabled: Yes/No Arbitrage")

        # 4. Spread Trading Strategy
        if strategy_configs.get('spread_trading', {}).get('enabled', True):
            self.strategies['spread_trading'] = SpreadTradingStrategy(
                polymarket=self.polymarket,
                config=strategy_configs.get('spread_trading', {}),
                dry_run=self.dry_run
            )
            logger.info("Strategy enabled: Spread Trading")

        # 5. Range Coverage Strategy
        if strategy_configs.get('range_coverage', {}).get('enabled', True):
            self.strategies['range_coverage'] = RangeCoverageStrategy(
                polymarket=self.polymarket,
                config=strategy_configs.get('range_coverage', {}),
                dry_run=self.dry_run
            )
            logger.info("Strategy enabled: Range Coverage")

        # 6. Compounding Meta-Strategy (combines all others)
        active_strategies = list(self.strategies.values())
        if active_strategies:
            self.strategies['compounding'] = CompoundingStrategy(
                strategies=active_strategies,
                config=strategy_configs.get('compounding_bets', {}),
                dry_run=self.dry_run
            )

    async def shutdown(self):
        """Shutdown all bot components"""
        logger.info("Shutting down Wanchai Bot...")

        # Close positions if configured
        # for name, strategy in self.strategies.items():
        #     await strategy.close_all_positions()

        # Disconnect from exchanges
        if self.price_aggregator:
            await self.price_aggregator.disconnect_all()

        # Disconnect from Polymarket
        if self.polymarket:
            await self.polymarket.close()

        self._running = False
        logger.info("Shutdown complete")

    async def run_once(self) -> Dict:
        """
        Run one iteration of all strategies

        Returns:
            Summary of iteration results
        """
        iteration_start = datetime.now()
        results = {
            'timestamp': iteration_start.isoformat(),
            'strategies': {},
            'total_signals': 0,
            'total_trades': 0,
            'total_profit': 0
        }

        for name, strategy in self.strategies.items():
            if name == 'compounding':
                continue  # Compounding runs separately

            try:
                result = await strategy.run_once()

                results['strategies'][name] = {
                    'success': result.success,
                    'signals': result.signals_generated,
                    'trades': result.trades_executed,
                    'profit': result.profit_loss
                }

                results['total_signals'] += result.signals_generated
                results['total_trades'] += result.trades_executed
                results['total_profit'] += result.profit_loss

            except Exception as e:
                logger.error(f"Strategy {name} error: {e}")
                results['strategies'][name] = {'error': str(e)}

        # Update totals
        self._trade_count += results['total_trades']
        self._total_profit += results['total_profit']

        results['duration_seconds'] = (datetime.now() - iteration_start).total_seconds()

        return results

    async def run(
        self,
        initial_capital: float = 10000,
        duration_hours: float = 24,
        scan_interval: int = 30
    ):
        """
        Run the bot continuously

        Args:
            initial_capital: Starting capital in USD
            duration_hours: How long to run (0 for indefinite)
            scan_interval: Seconds between strategy scans
        """
        await self.initialize()

        self._running = True
        self._start_time = datetime.now()

        # Set up signal handlers
        def signal_handler(sig, frame):
            logger.info("Received shutdown signal")
            self._running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Set initial capital for compounding strategy
        if 'compounding' in self.strategies:
            self.strategies['compounding'].set_initial_capital(initial_capital)

        logger.info("=" * 60)
        logger.info(f"Starting bot with ${initial_capital:,.2f} capital")
        logger.info(f"Duration: {duration_hours}h (0=indefinite)")
        logger.info(f"Scan interval: {scan_interval}s")
        logger.info("=" * 60)

        end_time = None
        if duration_hours > 0:
            end_time = self._start_time.timestamp() + (duration_hours * 3600)

        try:
            while self._running:
                # Check duration
                if end_time and datetime.now().timestamp() >= end_time:
                    logger.info("Duration reached, stopping...")
                    break

                # Run iteration
                result = await self.run_once()

                # Log significant activity
                if result['total_trades'] > 0:
                    logger.info(f"Iteration: {result['total_signals']} signals, "
                               f"{result['total_trades']} trades, "
                               f"P&L: ${result['total_profit']:.2f}")

                # Check risk limits
                if not self._check_risk_limits():
                    logger.warning("Risk limit reached, stopping...")
                    break

                await asyncio.sleep(scan_interval)

        except Exception as e:
            logger.error(f"Bot error: {e}")
        finally:
            await self.shutdown()

        return self.get_session_summary()

    def _check_risk_limits(self) -> bool:
        """Check if risk limits have been breached"""
        risk_config = self.config.get('risk', {})
        max_daily_loss = risk_config.get('max_daily_loss', 5000)

        if self._total_profit < -max_daily_loss:
            logger.warning(f"Max daily loss of ${max_daily_loss} exceeded")
            return False

        return True

    def get_session_summary(self) -> Dict:
        """Get comprehensive session summary"""
        runtime = (datetime.now() - self._start_time).total_seconds() if self._start_time else 0

        summary = {
            'session': {
                'start_time': self._start_time.isoformat() if self._start_time else None,
                'runtime_hours': runtime / 3600,
                'dry_run': self.dry_run
            },
            'performance': {
                'total_trades': self._trade_count,
                'total_profit': self._total_profit,
                'profit_per_hour': self._total_profit / (runtime / 3600) if runtime > 0 else 0,
                'profit_per_trade': self._total_profit / self._trade_count if self._trade_count > 0 else 0
            },
            'strategies': {}
        }

        for name, strategy in self.strategies.items():
            summary['strategies'][name] = strategy.get_status()

        if 'compounding' in self.strategies:
            summary['compounding'] = self.strategies['compounding'].get_performance_summary()

        return summary

    def print_status(self):
        """Print current bot status to console"""
        summary = self.get_session_summary()

        print("\n" + "=" * 60)
        print("WANCHAI ARBITRAGE BOT - STATUS")
        print("=" * 60)

        print(f"\nSession:")
        print(f"  Runtime: {summary['session']['runtime_hours']:.2f} hours")
        print(f"  Mode: {'DRY RUN' if summary['session']['dry_run'] else 'LIVE'}")

        print(f"\nPerformance:")
        print(f"  Total Trades: {summary['performance']['total_trades']}")
        print(f"  Total Profit: ${summary['performance']['total_profit']:,.2f}")
        print(f"  Profit/Hour: ${summary['performance']['profit_per_hour']:,.2f}")

        print(f"\nStrategies:")
        for name, status in summary['strategies'].items():
            enabled = status.get('enabled', False)
            trades = status.get('metrics', {}).get('total_trades', 0)
            print(f"  {name}: {'ON' if enabled else 'OFF'} ({trades} trades)")

        print("=" * 60 + "\n")


async def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Wanchai Arbitrage Bot')
    parser.add_argument('--config', type=str, default='config/config.yaml',
                       help='Path to configuration file')
    parser.add_argument('--capital', type=float, default=10000,
                       help='Initial capital in USD')
    parser.add_argument('--duration', type=float, default=24,
                       help='Duration in hours (0 for indefinite)')
    parser.add_argument('--interval', type=int, default=30,
                       help='Scan interval in seconds')
    parser.add_argument('--live', action='store_true',
                       help='Run in live mode (default is dry run)')

    args = parser.parse_args()

    # Configure logging
    logger.add(
        "logs/bot_{time}.log",
        rotation="1 day",
        retention="7 days",
        level="INFO"
    )

    bot = WanchaiBot(
        config_path=args.config,
        dry_run=not args.live
    )

    summary = await bot.run(
        initial_capital=args.capital,
        duration_hours=args.duration,
        scan_interval=args.interval
    )

    print("\n" + "=" * 60)
    print("SESSION COMPLETE")
    print("=" * 60)
    print(f"Total Trades: {summary['performance']['total_trades']}")
    print(f"Total Profit: ${summary['performance']['total_profit']:,.2f}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
