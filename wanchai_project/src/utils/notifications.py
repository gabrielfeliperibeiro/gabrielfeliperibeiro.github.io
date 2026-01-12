"""
Notification Manager - Send alerts via Telegram, Discord, etc.
"""

import asyncio
import os
from datetime import datetime
from typing import Any, Dict, Optional

import aiohttp
from loguru import logger


class NotificationManager:
    """
    Manages notifications across multiple channels
    Supports Telegram, Discord, and console logging
    """

    def __init__(
        self,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        discord_webhook: Optional[str] = None,
        console_enabled: bool = True
    ):
        self.telegram_token = telegram_token or os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = telegram_chat_id or os.getenv('TELEGRAM_CHAT_ID')
        self.discord_webhook = discord_webhook or os.getenv('DISCORD_WEBHOOK_URL')
        self.console_enabled = console_enabled

        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limit_timestamps: Dict[str, float] = {}
        self._rate_limit_seconds = 1  # Minimum seconds between same notification type

    async def connect(self):
        """Initialize HTTP session"""
        if self._session is None:
            self._session = aiohttp.ClientSession()

    async def disconnect(self):
        """Close HTTP session"""
        if self._session:
            await self._session.close()
            self._session = None

    def _should_send(self, notification_type: str) -> bool:
        """Check rate limiting"""
        now = datetime.now().timestamp()
        last_sent = self._rate_limit_timestamps.get(notification_type, 0)

        if now - last_sent < self._rate_limit_seconds:
            return False

        self._rate_limit_timestamps[notification_type] = now
        return True

    async def notify(
        self,
        message: str,
        notification_type: str = "info",
        data: Optional[Dict] = None
    ):
        """
        Send notification to all enabled channels

        Args:
            message: Notification message
            notification_type: Type of notification (info, trade, alert, error)
            data: Additional data to include
        """
        if not self._should_send(f"{notification_type}:{message[:50]}"):
            return

        tasks = []

        # Console
        if self.console_enabled:
            self._log_to_console(message, notification_type)

        # Telegram
        if self.telegram_token and self.telegram_chat_id:
            tasks.append(self._send_telegram(message, notification_type, data))

        # Discord
        if self.discord_webhook:
            tasks.append(self._send_discord(message, notification_type, data))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _log_to_console(self, message: str, notification_type: str):
        """Log to console via loguru"""
        if notification_type == "error":
            logger.error(message)
        elif notification_type == "alert":
            logger.warning(message)
        elif notification_type == "trade":
            logger.info(f"[TRADE] {message}")
        else:
            logger.info(message)

    async def _send_telegram(
        self,
        message: str,
        notification_type: str,
        data: Optional[Dict] = None
    ):
        """Send message via Telegram"""
        if not self._session:
            await self.connect()

        # Format message with emoji based on type
        emoji_map = {
            "info": "ℹ️",
            "trade": "💰",
            "alert": "⚠️",
            "error": "❌",
            "success": "✅"
        }

        emoji = emoji_map.get(notification_type, "📢")
        formatted_message = f"{emoji} *{notification_type.upper()}*\n\n{message}"

        if data:
            formatted_message += "\n\n```\n"
            for key, value in data.items():
                formatted_message += f"{key}: {value}\n"
            formatted_message += "```"

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"

        try:
            async with self._session.post(url, json={
                "chat_id": self.telegram_chat_id,
                "text": formatted_message,
                "parse_mode": "Markdown"
            }) as response:
                if response.status != 200:
                    logger.warning(f"Telegram notification failed: {response.status}")
        except Exception as e:
            logger.error(f"Telegram error: {e}")

    async def _send_discord(
        self,
        message: str,
        notification_type: str,
        data: Optional[Dict] = None
    ):
        """Send message via Discord webhook"""
        if not self._session:
            await self.connect()

        # Color based on type
        color_map = {
            "info": 0x3498db,  # Blue
            "trade": 0x2ecc71,  # Green
            "alert": 0xf39c12,  # Orange
            "error": 0xe74c3c,  # Red
            "success": 0x27ae60  # Dark green
        }

        color = color_map.get(notification_type, 0x95a5a6)

        # Build embed
        embed = {
            "title": f"{notification_type.upper()}",
            "description": message,
            "color": color,
            "timestamp": datetime.utcnow().isoformat()
        }

        if data:
            embed["fields"] = [
                {"name": key, "value": str(value), "inline": True}
                for key, value in data.items()
            ]

        try:
            async with self._session.post(self.discord_webhook, json={
                "embeds": [embed]
            }) as response:
                if response.status not in [200, 204]:
                    logger.warning(f"Discord notification failed: {response.status}")
        except Exception as e:
            logger.error(f"Discord error: {e}")

    # Convenience methods for common notification types

    async def trade_executed(
        self,
        strategy: str,
        side: str,
        market: str,
        size: float,
        price: float,
        expected_profit: float = 0
    ):
        """Notify of trade execution"""
        message = f"Trade executed by {strategy}\n"
        message += f"Market: {market[:50]}...\n" if len(market) > 50 else f"Market: {market}\n"
        message += f"Side: {side}\n"
        message += f"Size: {size:.2f} shares @ ${price:.4f}"

        data = {
            "Strategy": strategy,
            "Side": side,
            "Size": f"{size:.2f}",
            "Price": f"${price:.4f}",
            "Expected Profit": f"${expected_profit:.2f}"
        }

        await self.notify(message, "trade", data)

    async def arbitrage_found(
        self,
        arb_type: str,
        market: str,
        profit_pct: float,
        details: Optional[Dict] = None
    ):
        """Notify of arbitrage opportunity"""
        message = f"Arbitrage opportunity found!\n"
        message += f"Type: {arb_type}\n"
        message += f"Market: {market[:50]}...\n" if len(market) > 50 else f"Market: {market}\n"
        message += f"Profit: {profit_pct:.2%}"

        data = {
            "Type": arb_type,
            "Profit": f"{profit_pct:.2%}",
            **(details or {})
        }

        await self.notify(message, "alert", data)

    async def daily_summary(
        self,
        trades: int,
        profit: float,
        win_rate: float,
        capital: float
    ):
        """Send daily performance summary"""
        message = "📊 Daily Summary\n\n"
        message += f"Trades: {trades}\n"
        message += f"Profit: ${profit:,.2f}\n"
        message += f"Win Rate: {win_rate:.1%}\n"
        message += f"Capital: ${capital:,.2f}"

        data = {
            "Trades": trades,
            "Profit": f"${profit:,.2f}",
            "Win Rate": f"{win_rate:.1%}",
            "Capital": f"${capital:,.2f}"
        }

        await self.notify(message, "info", data)

    async def error_alert(self, error: str, context: Optional[str] = None):
        """Send error alert"""
        message = f"Error occurred: {error}"
        if context:
            message += f"\n\nContext: {context}"

        await self.notify(message, "error", {"Error": error})

    async def position_closed(
        self,
        market: str,
        profit_loss: float,
        hold_time_hours: float
    ):
        """Notify of position closure"""
        result = "Profit" if profit_loss >= 0 else "Loss"
        message = f"Position closed: {result}\n"
        message += f"Market: {market[:50]}...\n" if len(market) > 50 else f"Market: {market}\n"
        message += f"P&L: ${profit_loss:,.2f}\n"
        message += f"Hold time: {hold_time_hours:.1f} hours"

        notification_type = "success" if profit_loss >= 0 else "alert"

        await self.notify(message, notification_type, {
            "P&L": f"${profit_loss:,.2f}",
            "Hold Time": f"{hold_time_hours:.1f}h"
        })
