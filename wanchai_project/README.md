# Wanchai Arbitrage Bot

A sophisticated Bitcoin market arbitrage bot for Polymarket prediction markets. This bot implements multiple profitable strategies derived from successful traders, including latency arbitrage, near-resolved market sniping, Yes/No arbitrage, spread trading, and range coverage.

## Features

- **Multi-Strategy Architecture**: 6 different arbitrage strategies running in parallel
- **Real-Time Price Feeds**: WebSocket connections to Binance and other exchanges
- **Polymarket Integration**: Full CLOB API support for market data and order execution
- **Automatic Compounding**: Reinvest profits for exponential growth
- **Risk Management**: Position sizing, stop-losses, and daily loss limits
- **Trade Database**: SQLite storage for performance tracking
- **Notifications**: Telegram and Discord alerts

## Implemented Strategies

### 1. Latency Arbitrage ($519k in 30 days)
Exploits price lag between crypto exchanges and Polymarket. When BTC moves significantly on Binance, Polymarket prediction markets may not update immediately, creating arbitrage opportunities.

```
- Monitor exchanges for sudden price movements (impulses)
- Detect lagging Polymarket prices
- Execute large positions during the 15-minute lag window
```

### 2. Near-Resolved Market Sniping ($415k+)
Buy shares in markets that are essentially decided but still offer small yields at 95-99% probabilities.

```
- Target markets at 95-99% probability
- Hold until resolution for guaranteed payout
- Compound profits immediately
```

### 3. Yes/No Arbitrage (3-5% per opportunity)
Exploit when Yes + No prices ≠ $1 due to market inefficiencies.

```
- If Yes + No < $1: Buy both sides for guaranteed profit
- If Yes + No > $1: Mint and sell both sides
- Use bots for 24/7 monitoring
```

### 4. Spread Trading (Market Making)
Provide liquidity by placing competitive bid/ask orders and capturing the spread.

```
- Place highest bid and lowest ask
- Capture spread when both sides fill
- Manage inventory imbalance
```

### 5. Range Coverage (25-28% per trade)
In multi-outcome markets, buy leading ranges keeping total cost under $1 for guaranteed profit.

```
- Cover top outcomes for less than $1 total
- Guaranteed profit when any outcome wins
- Works well on BTC price range markets
```

### 6. Compounding Strategy
Meta-strategy that coordinates all others and automatically reinvests profits.

```
- Kelly Criterion position sizing
- Automatic profit reinvestment
- Performance tracking across strategies
```

## Installation

```bash
# Clone the repository
cd wanchai_project

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env
# Edit .env with your API keys
```

## Configuration

### Environment Variables (.env)

```bash
# Polymarket Configuration
POLYMARKET_API_KEY=your_api_key
POLYMARKET_API_SECRET=your_api_secret
POLYMARKET_API_PASSPHRASE=your_passphrase

# Wallet (for Polygon transactions)
WALLET_PRIVATE_KEY=your_private_key
WALLET_ADDRESS=your_wallet_address

# Exchange APIs (for price feeds)
BINANCE_API_KEY=your_binance_key
BINANCE_API_SECRET=your_binance_secret

# Notifications (optional)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
DISCORD_WEBHOOK_URL=your_webhook_url
```

### Strategy Configuration (config/config.yaml)

```yaml
strategies:
  latency_arbitrage:
    enabled: true
    min_price_deviation: 0.02  # 2% threshold
    max_position_size: 35000   # USD
    execution_window_seconds: 900

  near_resolved_sniping:
    enabled: true
    min_probability: 0.95
    max_probability: 0.99
    min_yield: 0.001

  yes_no_arbitrage:
    enabled: true
    min_spread: 0.005
    target_profit_pct: 0.03
```

## Usage

### Basic Usage

```bash
# Run in dry-run mode (default)
python -m src.bot --capital 10000 --duration 24

# Run in live mode (real trades)
python -m src.bot --capital 10000 --duration 24 --live

# Custom configuration
python -m src.bot --config my_config.yaml --interval 60
```

### Command Line Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--config` | Path to configuration file | `config/config.yaml` |
| `--capital` | Initial capital in USD | `10000` |
| `--duration` | Duration in hours (0 = indefinite) | `24` |
| `--interval` | Scan interval in seconds | `30` |
| `--live` | Enable live trading | `False` (dry run) |

### Programmatic Usage

```python
import asyncio
from src.bot import WanchaiBot

async def main():
    bot = WanchaiBot(
        config_path="config/config.yaml",
        dry_run=True
    )

    # Run for 24 hours with $10k capital
    summary = await bot.run(
        initial_capital=10000,
        duration_hours=24,
        scan_interval=30
    )

    print(f"Total profit: ${summary['performance']['total_profit']:,.2f}")

asyncio.run(main())
```

### Individual Strategy Usage

```python
import asyncio
from src.polymarket import PolymarketClient
from src.strategies import NearResolvedSnipingStrategy

async def snipe_near_resolved():
    client = PolymarketClient(api_key="...", dry_run=True)
    await client.connect()

    strategy = NearResolvedSnipingStrategy(
        polymarket=client,
        config={'min_probability': 0.95},
        dry_run=True
    )

    # Run compounding loop
    await strategy.run_compounding_loop(
        initial_capital=5000,
        scan_interval=300
    )

asyncio.run(snipe_near_resolved())
```

## Project Structure

```
wanchai_project/
├── config/
│   └── config.yaml          # Main configuration
├── src/
│   ├── __init__.py
│   ├── bot.py               # Main orchestrator
│   ├── polymarket/
│   │   ├── __init__.py
│   │   ├── client.py        # Polymarket API client
│   │   ├── orderbook.py     # Orderbook management
│   │   └── market.py        # Market data structures
│   ├── exchanges/
│   │   ├── __init__.py
│   │   ├── base.py          # Exchange interface
│   │   ├── binance.py       # Binance connector
│   │   └── aggregator.py    # Multi-exchange aggregator
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── base.py          # Strategy base class
│   │   ├── latency_arbitrage.py
│   │   ├── near_resolved.py
│   │   ├── yes_no_arbitrage.py
│   │   ├── spread_trading.py
│   │   ├── range_coverage.py
│   │   └── compounding.py
│   └── utils/
│       ├── __init__.py
│       ├── helpers.py       # Utility functions
│       ├── database.py      # Trade storage
│       └── notifications.py # Alert system
├── tests/
├── logs/
├── data/
├── requirements.txt
├── .env.example
└── README.md
```

## Risk Warning

⚠️ **IMPORTANT DISCLAIMERS**:

1. **Financial Risk**: Trading prediction markets involves significant financial risk. You can lose your entire investment.

2. **No Guarantees**: Past performance (referenced from X posts) does not guarantee future results. Market conditions change.

3. **Start Small**: Always start with small amounts in dry-run mode before committing real capital.

4. **Legal Compliance**: Ensure prediction market trading is legal in your jurisdiction.

5. **API Risks**: API keys grant access to your funds. Never share them or commit them to version control.

6. **Market Risk**: Low-probability events do occur. "Near-certain" 99% bets still lose 1% of the time.

## Performance Monitoring

The bot tracks performance in an SQLite database:

```python
from src.utils import TradeDatabase

async def check_performance():
    db = TradeDatabase()
    await db.connect()

    # Get strategy stats
    stats = await db.get_strategy_stats("NearResolvedSniping")
    print(f"Win rate: {stats['win_rate']:.1%}")

    # Get daily P&L
    daily = await db.get_daily_pnl(days=7)
    for day in daily:
        print(f"{day['date']}: ${day['daily_pnl']:.2f}")
```

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

MIT License - see LICENSE file for details.

## Acknowledgments

Strategies inspired by successful Polymarket traders sharing their approaches on X (Twitter):
- Latency arbitrage: @kirillk_web3
- Near-resolved sniping: @igor_mikerin
- Yes/No arbitrage: @CryptoLady_M
- Range coverage: @Nekt_0
- And others from the Polymarket trading community
