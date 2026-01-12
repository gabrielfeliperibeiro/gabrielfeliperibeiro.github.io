"""
Helper utility functions for the Wanchai Arbitrage Bot
"""

import asyncio
import time
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from loguru import logger


T = TypeVar('T')


def format_currency(amount: float, decimals: int = 2) -> str:
    """Format amount as currency string"""
    if amount >= 0:
        return f"${amount:,.{decimals}f}"
    else:
        return f"-${abs(amount):,.{decimals}f}"


def format_percentage(value: float, decimals: int = 2) -> str:
    """Format value as percentage string"""
    return f"{value * 100:.{decimals}f}%"


def calculate_kelly_fraction(
    win_probability: float,
    win_amount: float,
    loss_amount: float
) -> float:
    """
    Calculate Kelly Criterion fraction for optimal bet sizing

    Args:
        win_probability: Probability of winning (0-1)
        win_amount: Amount won per $1 bet
        loss_amount: Amount lost per $1 bet (positive number)

    Returns:
        Optimal fraction of bankroll to bet
    """
    if loss_amount <= 0 or win_amount <= 0:
        return 0

    q = 1 - win_probability
    b = win_amount / loss_amount

    kelly = (win_probability * b - q) / b

    return max(0, kelly)


def half_kelly(
    win_probability: float,
    win_amount: float,
    loss_amount: float
) -> float:
    """Calculate half-Kelly for more conservative sizing"""
    return calculate_kelly_fraction(win_probability, win_amount, loss_amount) * 0.5


def exponential_backoff(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True
) -> float:
    """
    Calculate exponential backoff delay

    Args:
        attempt: Current attempt number (0-indexed)
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
        jitter: Add random jitter to prevent thundering herd

    Returns:
        Delay in seconds
    """
    import random

    delay = min(base_delay * (2 ** attempt), max_delay)

    if jitter:
        delay = delay * (0.5 + random.random())

    return delay


def retry_async(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple = (Exception,)
):
    """
    Decorator for retrying async functions with exponential backoff

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Base delay between retries
        max_delay: Maximum delay between retries
        exceptions: Tuple of exceptions to catch
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt < max_retries:
                        delay = exponential_backoff(attempt, base_delay, max_delay)
                        logger.warning(f"Retry {attempt + 1}/{max_retries} for {func.__name__}: {e}")
                        await asyncio.sleep(delay)

            raise last_exception

        return wrapper
    return decorator


def rate_limit(calls_per_second: float):
    """
    Decorator to rate limit async function calls

    Args:
        calls_per_second: Maximum calls per second
    """
    min_interval = 1.0 / calls_per_second
    last_call = [0.0]

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            elapsed = time.time() - last_call[0]
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)

            last_call[0] = time.time()
            return await func(*args, **kwargs)

        return wrapper
    return decorator


class RateLimiter:
    """Token bucket rate limiter for API calls"""

    def __init__(self, rate: float, burst: int = 1):
        """
        Args:
            rate: Tokens per second
            burst: Maximum burst size
        """
        self.rate = rate
        self.burst = burst
        self._tokens = burst
        self._last_update = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1):
        """Acquire tokens, waiting if necessary"""
        async with self._lock:
            while True:
                now = time.time()
                elapsed = now - self._last_update
                self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
                self._last_update = now

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return

                # Wait for tokens
                wait_time = (tokens - self._tokens) / self.rate
                await asyncio.sleep(wait_time)


def calculate_sharpe_ratio(
    returns: list,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252
) -> float:
    """
    Calculate Sharpe ratio from a list of returns

    Args:
        returns: List of period returns (e.g., daily returns)
        risk_free_rate: Annual risk-free rate
        periods_per_year: Number of periods per year (252 for daily)

    Returns:
        Annualized Sharpe ratio
    """
    if not returns or len(returns) < 2:
        return 0

    import statistics

    mean_return = statistics.mean(returns)
    std_return = statistics.stdev(returns)

    if std_return == 0:
        return 0

    # Annualize
    excess_return = mean_return - (risk_free_rate / periods_per_year)
    sharpe = (excess_return / std_return) * (periods_per_year ** 0.5)

    return sharpe


def calculate_max_drawdown(equity_curve: list) -> tuple:
    """
    Calculate maximum drawdown from equity curve

    Args:
        equity_curve: List of equity values over time

    Returns:
        Tuple of (max_drawdown_pct, peak_index, trough_index)
    """
    if not equity_curve:
        return (0, 0, 0)

    peak = equity_curve[0]
    peak_index = 0
    max_dd = 0
    max_dd_peak = 0
    max_dd_trough = 0

    for i, value in enumerate(equity_curve):
        if value > peak:
            peak = value
            peak_index = i

        drawdown = (peak - value) / peak if peak > 0 else 0

        if drawdown > max_dd:
            max_dd = drawdown
            max_dd_peak = peak_index
            max_dd_trough = i

    return (max_dd, max_dd_peak, max_dd_trough)


def timestamp_to_datetime(timestamp: int) -> datetime:
    """Convert Unix timestamp (ms) to datetime"""
    return datetime.fromtimestamp(timestamp / 1000)


def datetime_to_timestamp(dt: datetime) -> int:
    """Convert datetime to Unix timestamp (ms)"""
    return int(dt.timestamp() * 1000)


class MovingAverage:
    """Simple moving average calculator"""

    def __init__(self, period: int):
        self.period = period
        self._values = []

    def add(self, value: float) -> Optional[float]:
        """Add value and return current average"""
        self._values.append(value)

        if len(self._values) > self.period:
            self._values.pop(0)

        if len(self._values) >= self.period:
            return sum(self._values) / len(self._values)

        return None

    @property
    def value(self) -> Optional[float]:
        """Get current average"""
        if len(self._values) >= self.period:
            return sum(self._values) / len(self._values)
        return None

    def reset(self):
        """Reset the moving average"""
        self._values = []


class ExponentialMovingAverage:
    """Exponential moving average calculator"""

    def __init__(self, period: int):
        self.period = period
        self.multiplier = 2 / (period + 1)
        self._value: Optional[float] = None
        self._count = 0

    def add(self, value: float) -> Optional[float]:
        """Add value and return current EMA"""
        self._count += 1

        if self._value is None:
            self._value = value
        else:
            self._value = (value - self._value) * self.multiplier + self._value

        if self._count >= self.period:
            return self._value

        return None

    @property
    def value(self) -> Optional[float]:
        """Get current EMA"""
        if self._count >= self.period:
            return self._value
        return None

    def reset(self):
        """Reset the EMA"""
        self._value = None
        self._count = 0
