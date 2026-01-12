"""
Market data structures and analysis for Polymarket
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
from enum import Enum


class MarketStatus(Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    RESOLVED = "resolved"
    DISPUTED = "disputed"


class Resolution(Enum):
    YES = "Yes"
    NO = "No"
    UNKNOWN = "Unknown"
    INVALID = "Invalid"


@dataclass
class MarketOutcome:
    """Single outcome in a market"""
    name: str
    token_id: str
    price: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0

    @property
    def implied_probability(self) -> float:
        """Price represents implied probability"""
        return self.price

    @property
    def potential_return(self) -> float:
        """Return if this outcome wins"""
        if self.price == 0:
            return float('inf')
        return (1 - self.price) / self.price


@dataclass
class Market:
    """
    Polymarket prediction market
    """
    id: str
    question: str
    description: str = ""
    outcomes: List[MarketOutcome] = field(default_factory=list)
    status: MarketStatus = MarketStatus.ACTIVE
    resolution: Resolution = Resolution.UNKNOWN
    end_date: Optional[datetime] = None
    created_at: Optional[datetime] = None
    volume: float = 0.0
    liquidity: float = 0.0
    tags: List[str] = field(default_factory=list)

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'Market':
        """Create Market from API response"""
        outcomes = []
        outcome_names = data.get('outcomes', [])
        token_ids = data.get('clobTokenIds', [])
        outcome_prices = data.get('outcomePrices', [])

        for i, name in enumerate(outcome_names):
            token_id = token_ids[i] if i < len(token_ids) else ""
            price = float(outcome_prices[i]) if i < len(outcome_prices) else 0.0

            outcomes.append(MarketOutcome(
                name=name,
                token_id=token_id,
                price=price
            ))

        # Parse dates
        end_date = None
        if data.get('endDate'):
            try:
                end_date = datetime.fromisoformat(data['endDate'].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass

        created_at = None
        if data.get('createdAt'):
            try:
                created_at = datetime.fromisoformat(data['createdAt'].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass

        # Determine status
        status = MarketStatus.ACTIVE
        if data.get('closed'):
            status = MarketStatus.CLOSED
        if data.get('resolved'):
            status = MarketStatus.RESOLVED

        return cls(
            id=data.get('id', ''),
            question=data.get('question', ''),
            description=data.get('description', ''),
            outcomes=outcomes,
            status=status,
            end_date=end_date,
            created_at=created_at,
            volume=float(data.get('volume', 0)),
            liquidity=float(data.get('liquidity', 0)),
            tags=data.get('tags', [])
        )

    @property
    def is_binary(self) -> bool:
        """Check if this is a simple Yes/No market"""
        return len(self.outcomes) == 2

    @property
    def is_active(self) -> bool:
        """Check if market is still active"""
        return self.status == MarketStatus.ACTIVE

    @property
    def is_resolved(self) -> bool:
        """Check if market has been resolved"""
        return self.status == MarketStatus.RESOLVED

    @property
    def time_to_end(self) -> Optional[float]:
        """Hours until market ends"""
        if not self.end_date:
            return None
        delta = self.end_date - datetime.now(self.end_date.tzinfo)
        return delta.total_seconds() / 3600

    @property
    def yes_outcome(self) -> Optional[MarketOutcome]:
        """Get Yes outcome for binary markets"""
        for outcome in self.outcomes:
            if outcome.name.lower() in ['yes', 'true']:
                return outcome
        return self.outcomes[0] if self.outcomes else None

    @property
    def no_outcome(self) -> Optional[MarketOutcome]:
        """Get No outcome for binary markets"""
        for outcome in self.outcomes:
            if outcome.name.lower() in ['no', 'false']:
                return outcome
        return self.outcomes[1] if len(self.outcomes) > 1 else None

    @property
    def total_price(self) -> float:
        """Sum of all outcome prices (should be ~1)"""
        return sum(o.price for o in self.outcomes)

    @property
    def price_deviation(self) -> float:
        """How far total price deviates from 1 (arbitrage indicator)"""
        return abs(1 - self.total_price)

    @property
    def has_arbitrage(self) -> bool:
        """Quick check for obvious arbitrage opportunity"""
        return self.price_deviation > 0.005  # 0.5% deviation

    def get_leading_outcome(self) -> Optional[MarketOutcome]:
        """Get the outcome with highest probability"""
        if not self.outcomes:
            return None
        return max(self.outcomes, key=lambda o: o.price)

    def get_near_certain_outcomes(
        self,
        min_prob: float = 0.95,
        max_prob: float = 0.99
    ) -> List[MarketOutcome]:
        """Get outcomes with near-certain probability (for sniping strategy)"""
        return [o for o in self.outcomes if min_prob <= o.price <= max_prob]

    def get_underpriced_outcomes(
        self,
        threshold: float = 0.05
    ) -> List[MarketOutcome]:
        """Get outcomes priced below threshold (for long-shot strategy)"""
        return [o for o in self.outcomes if o.price < threshold]

    def analyze_spread_opportunity(self) -> Optional[Dict]:
        """
        Analyze market for spread trading opportunity
        Returns potential profit from market making
        """
        if not self.is_binary:
            return None

        yes = self.yes_outcome
        no = self.no_outcome

        if not yes or not no:
            return None

        # Check if prices allow profitable spread
        # Buy yes + buy no should cost less than $1 for guaranteed profit
        total_buy = yes.price + no.price

        return {
            'yes_price': yes.price,
            'no_price': no.price,
            'total': total_buy,
            'spread': 1 - total_buy,
            'profitable': total_buy < 0.995,
            'profit_pct': (1 - total_buy) / total_buy if total_buy > 0 else 0
        }

    def is_bitcoin_related(self) -> bool:
        """Check if market is related to Bitcoin"""
        keywords = ['bitcoin', 'btc', 'crypto', 'cryptocurrency', 'satoshi']

        text = f"{self.question} {self.description}".lower()
        tags_lower = [t.lower() for t in self.tags]

        return any(
            kw in text or kw in tags_lower
            for kw in keywords
        )


@dataclass
class MarketAnalysis:
    """
    Analysis results for a market
    Used for strategy decision making
    """
    market: Market
    timestamp: datetime = field(default_factory=datetime.now)

    # Price analysis
    yes_price: float = 0.0
    no_price: float = 0.0
    spread: float = 0.0
    liquidity_depth: float = 0.0

    # Opportunity flags
    has_yes_no_arb: bool = False
    has_near_resolved: bool = False
    has_spread_opportunity: bool = False
    has_range_opportunity: bool = False

    # Risk metrics
    time_to_resolution_hours: Optional[float] = None
    volume_24h: float = 0.0
    price_volatility: float = 0.0

    # Recommended action
    recommended_strategy: str = ""
    expected_profit: float = 0.0
    risk_score: float = 0.0  # 0-1, higher = riskier

    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            'market_id': self.market.id,
            'question': self.market.question,
            'timestamp': self.timestamp.isoformat(),
            'yes_price': self.yes_price,
            'no_price': self.no_price,
            'spread': self.spread,
            'has_yes_no_arb': self.has_yes_no_arb,
            'has_near_resolved': self.has_near_resolved,
            'has_spread_opportunity': self.has_spread_opportunity,
            'recommended_strategy': self.recommended_strategy,
            'expected_profit': self.expected_profit,
            'risk_score': self.risk_score
        }


class MarketScanner:
    """
    Scans markets for arbitrage and trading opportunities
    """

    def __init__(self, markets: List[Market]):
        self.markets = markets

    def find_arbitrage_opportunities(
        self,
        min_profit_pct: float = 0.005
    ) -> List[MarketAnalysis]:
        """Find all Yes/No arbitrage opportunities"""
        opportunities = []

        for market in self.markets:
            if not market.is_binary or not market.is_active:
                continue

            analysis = market.analyze_spread_opportunity()
            if analysis and analysis['profitable']:
                if analysis['profit_pct'] >= min_profit_pct:
                    ma = MarketAnalysis(
                        market=market,
                        yes_price=analysis['yes_price'],
                        no_price=analysis['no_price'],
                        spread=analysis['spread'],
                        has_yes_no_arb=True,
                        recommended_strategy='YES_NO_ARBITRAGE',
                        expected_profit=analysis['profit_pct'],
                        risk_score=0.1  # Low risk for arbitrage
                    )
                    opportunities.append(ma)

        return sorted(opportunities, key=lambda x: x.expected_profit, reverse=True)

    def find_near_resolved_markets(
        self,
        min_prob: float = 0.95,
        max_prob: float = 0.99
    ) -> List[MarketAnalysis]:
        """Find markets ready for sniping strategy"""
        opportunities = []

        for market in self.markets:
            if not market.is_active:
                continue

            near_certain = market.get_near_certain_outcomes(min_prob, max_prob)

            for outcome in near_certain:
                expected_yield = 1 - outcome.price

                ma = MarketAnalysis(
                    market=market,
                    yes_price=outcome.price,
                    has_near_resolved=True,
                    time_to_resolution_hours=market.time_to_end,
                    recommended_strategy='NEAR_RESOLVED_SNIPING',
                    expected_profit=expected_yield,
                    risk_score=1 - outcome.price  # Risk = probability of loss
                )
                opportunities.append(ma)

        return sorted(opportunities, key=lambda x: x.expected_profit, reverse=True)

    def find_spread_opportunities(
        self,
        min_spread_pct: float = 0.02
    ) -> List[MarketAnalysis]:
        """Find markets suitable for spread trading / market making"""
        opportunities = []

        for market in self.markets:
            if not market.is_active:
                continue

            # Calculate spread (simplistic - would need orderbook for accuracy)
            if market.is_binary:
                yes = market.yes_outcome
                no = market.no_outcome

                if yes and no:
                    # Estimated spread based on price proximity to 0.5
                    mid_distance = abs(yes.price - 0.5)
                    estimated_spread = 0.02 + (0.05 * mid_distance)

                    if estimated_spread >= min_spread_pct:
                        ma = MarketAnalysis(
                            market=market,
                            yes_price=yes.price,
                            no_price=no.price,
                            spread=estimated_spread,
                            has_spread_opportunity=True,
                            recommended_strategy='SPREAD_TRADING',
                            expected_profit=estimated_spread / 2,
                            risk_score=0.3
                        )
                        opportunities.append(ma)

        return sorted(opportunities, key=lambda x: x.expected_profit, reverse=True)

    def find_bitcoin_opportunities(self) -> List[MarketAnalysis]:
        """Find all opportunities in Bitcoin-related markets"""
        all_opportunities = []

        # Filter to Bitcoin markets
        btc_markets = [m for m in self.markets if m.is_bitcoin_related()]
        scanner = MarketScanner(btc_markets)

        # Find all opportunity types
        all_opportunities.extend(scanner.find_arbitrage_opportunities())
        all_opportunities.extend(scanner.find_near_resolved_markets())
        all_opportunities.extend(scanner.find_spread_opportunities())

        # Remove duplicates and sort by profit
        seen = set()
        unique = []
        for opp in all_opportunities:
            if opp.market.id not in seen:
                seen.add(opp.market.id)
                unique.append(opp)

        return sorted(unique, key=lambda x: x.expected_profit, reverse=True)
