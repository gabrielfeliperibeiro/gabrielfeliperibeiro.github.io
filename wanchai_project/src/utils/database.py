"""
Trade Database - SQLite storage for trade history and performance tracking
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite
from loguru import logger


class TradeDatabase:
    """
    SQLite database for storing trade history and performance metrics
    """

    def __init__(self, db_path: str = "data/trades.db"):
        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None

        # Ensure data directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def connect(self):
        """Connect to database and create tables"""
        self._connection = await aiosqlite.connect(self.db_path)
        await self._create_tables()
        logger.info(f"Database connected: {self.db_path}")

    async def disconnect(self):
        """Close database connection"""
        if self._connection:
            await self._connection.close()
            self._connection = None

    async def _create_tables(self):
        """Create database tables if they don't exist"""
        await self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                strategy TEXT NOT NULL,
                market_id TEXT,
                token_id TEXT,
                side TEXT,
                price REAL,
                size REAL,
                cost REAL,
                profit_loss REAL,
                status TEXT,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT UNIQUE,
                token_id TEXT,
                strategy TEXT,
                side TEXT,
                size REAL,
                avg_price REAL,
                cost REAL,
                unrealized_pnl REAL,
                opened_at DATETIME,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                strategy TEXT,
                metric_name TEXT,
                metric_value REAL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time DATETIME,
                end_time DATETIME,
                initial_capital REAL,
                final_capital REAL,
                total_trades INTEGER,
                total_profit REAL,
                win_rate REAL,
                config TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
            CREATE INDEX IF NOT EXISTS idx_positions_market ON positions(market_id);
            CREATE INDEX IF NOT EXISTS idx_performance_timestamp ON performance(timestamp);
        """)
        await self._connection.commit()

    async def record_trade(
        self,
        strategy: str,
        market_id: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
        profit_loss: float = 0,
        status: str = "executed",
        metadata: Optional[Dict] = None
    ) -> int:
        """
        Record a trade execution

        Returns:
            Trade ID
        """
        cost = price * size

        cursor = await self._connection.execute("""
            INSERT INTO trades (strategy, market_id, token_id, side, price, size, cost, profit_loss, status, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            strategy,
            market_id,
            token_id,
            side,
            price,
            size,
            cost,
            profit_loss,
            status,
            json.dumps(metadata) if metadata else None
        ))

        await self._connection.commit()

        logger.debug(f"Trade recorded: {strategy} {side} {size} @ {price}")

        return cursor.lastrowid

    async def update_position(
        self,
        market_id: str,
        token_id: str,
        strategy: str,
        side: str,
        size: float,
        avg_price: float,
        cost: float,
        unrealized_pnl: float = 0
    ):
        """Update or create a position record"""
        await self._connection.execute("""
            INSERT INTO positions (market_id, token_id, strategy, side, size, avg_price, cost, unrealized_pnl, opened_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(market_id) DO UPDATE SET
                size = ?,
                avg_price = ?,
                cost = ?,
                unrealized_pnl = ?,
                updated_at = CURRENT_TIMESTAMP
        """, (
            market_id, token_id, strategy, side, size, avg_price, cost, unrealized_pnl,
            size, avg_price, cost, unrealized_pnl
        ))

        await self._connection.commit()

    async def close_position(self, market_id: str, final_pnl: float):
        """Close a position and record final P&L"""
        # Get position details first
        cursor = await self._connection.execute("""
            SELECT * FROM positions WHERE market_id = ?
        """, (market_id,))

        position = await cursor.fetchone()

        if position:
            # Record closing trade
            await self.record_trade(
                strategy=position[3],  # strategy column
                market_id=market_id,
                token_id=position[2],  # token_id column
                side="CLOSE",
                price=0,  # Market order
                size=position[5],  # size column
                profit_loss=final_pnl,
                status="closed"
            )

            # Remove position
            await self._connection.execute("""
                DELETE FROM positions WHERE market_id = ?
            """, (market_id,))

            await self._connection.commit()

    async def record_performance(
        self,
        strategy: str,
        metric_name: str,
        metric_value: float
    ):
        """Record a performance metric"""
        await self._connection.execute("""
            INSERT INTO performance (strategy, metric_name, metric_value)
            VALUES (?, ?, ?)
        """, (strategy, metric_name, metric_value))

        await self._connection.commit()

    async def start_session(
        self,
        initial_capital: float,
        config: Optional[Dict] = None
    ) -> int:
        """Start a new trading session"""
        cursor = await self._connection.execute("""
            INSERT INTO sessions (start_time, initial_capital, config)
            VALUES (CURRENT_TIMESTAMP, ?, ?)
        """, (initial_capital, json.dumps(config) if config else None))

        await self._connection.commit()

        return cursor.lastrowid

    async def end_session(
        self,
        session_id: int,
        final_capital: float,
        total_trades: int,
        total_profit: float,
        win_rate: float
    ):
        """End a trading session"""
        await self._connection.execute("""
            UPDATE sessions SET
                end_time = CURRENT_TIMESTAMP,
                final_capital = ?,
                total_trades = ?,
                total_profit = ?,
                win_rate = ?
            WHERE id = ?
        """, (final_capital, total_trades, total_profit, win_rate, session_id))

        await self._connection.commit()

    async def get_trades(
        self,
        strategy: Optional[str] = None,
        market_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100
    ) -> List[Dict]:
        """Get trade history with optional filters"""
        query = "SELECT * FROM trades WHERE 1=1"
        params = []

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        if market_id:
            query += " AND market_id = ?"
            params.append(market_id)

        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date.isoformat())

        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date.isoformat())

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await self._connection.execute(query, params)
        rows = await cursor.fetchall()

        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    async def get_positions(self, strategy: Optional[str] = None) -> List[Dict]:
        """Get all open positions"""
        query = "SELECT * FROM positions"
        params = []

        if strategy:
            query += " WHERE strategy = ?"
            params.append(strategy)

        cursor = await self._connection.execute(query, params)
        rows = await cursor.fetchall()

        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    async def get_strategy_stats(
        self,
        strategy: str,
        start_date: Optional[datetime] = None
    ) -> Dict:
        """Get statistics for a strategy"""
        query = """
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) as winning_trades,
                SUM(CASE WHEN profit_loss < 0 THEN 1 ELSE 0 END) as losing_trades,
                SUM(profit_loss) as total_profit,
                AVG(profit_loss) as avg_profit,
                MAX(profit_loss) as best_trade,
                MIN(profit_loss) as worst_trade
            FROM trades
            WHERE strategy = ?
        """
        params = [strategy]

        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date.isoformat())

        cursor = await self._connection.execute(query, params)
        row = await cursor.fetchone()

        if row:
            columns = [description[0] for description in cursor.description]
            stats = dict(zip(columns, row))

            # Calculate win rate
            if stats['total_trades'] and stats['total_trades'] > 0:
                stats['win_rate'] = stats['winning_trades'] / stats['total_trades']
            else:
                stats['win_rate'] = 0

            return stats

        return {}

    async def get_daily_pnl(
        self,
        days: int = 30,
        strategy: Optional[str] = None
    ) -> List[Dict]:
        """Get daily P&L summary"""
        query = """
            SELECT
                DATE(timestamp) as date,
                COUNT(*) as trades,
                SUM(profit_loss) as daily_pnl,
                SUM(CASE WHEN profit_loss > 0 THEN profit_loss ELSE 0 END) as gross_profit,
                SUM(CASE WHEN profit_loss < 0 THEN profit_loss ELSE 0 END) as gross_loss
            FROM trades
            WHERE timestamp >= DATE('now', ?)
        """
        params = [f'-{days} days']

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        query += " GROUP BY DATE(timestamp) ORDER BY date DESC"

        cursor = await self._connection.execute(query, params)
        rows = await cursor.fetchall()

        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    async def get_session_history(self, limit: int = 10) -> List[Dict]:
        """Get recent session history"""
        cursor = await self._connection.execute("""
            SELECT * FROM sessions ORDER BY start_time DESC LIMIT ?
        """, (limit,))

        rows = await cursor.fetchall()

        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
