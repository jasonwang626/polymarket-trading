from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ScanStatus(StrEnum):
    WATCH = "WATCH"
    POSSIBLE_YES = "POSSIBLE_YES"
    POSSIBLE_NO = "POSSIBLE_NO"
    NO_TRADE = "NO_TRADE"


class PriceLevel(BaseModel):
    price: float = Field(ge=0, le=1)
    size: float = Field(ge=0)


class Market(BaseModel):
    model_config = ConfigDict(extra="ignore")

    market_id: str
    condition_id: str
    slug: str = ""
    question: str
    description: str = ""
    category: str = "crypto"
    yes_token_id: str
    no_token_id: str
    resolution_time: datetime
    liquidity: float = 0.0
    volume_24h: float = 0.0
    active: bool = True
    closed: bool = False
    accepting_orders: bool = True
    strike: float | None = None
    underlying: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)


class OrderBook(BaseModel):
    token_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    bids: list[PriceLevel] = Field(default_factory=list)
    asks: list[PriceLevel] = Field(default_factory=list)
    last_trade_price: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @property
    def best_bid(self) -> float | None:
        return max((level.price for level in self.bids), default=None)

    @property
    def best_ask(self) -> float | None:
        return min((level.price for level in self.asks), default=None)

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return self.last_trade_price
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid


class Trade(BaseModel):
    market_id: str
    timestamp: datetime
    price: float = Field(ge=0, le=1)
    size: float = Field(ge=0)
    side: str = ""
    outcome: str = ""
    transaction_hash: str = ""
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)


class ExternalPrice(BaseModel):
    symbol: str
    venue: str = "coinbase"
    timestamp: datetime = Field(default_factory=utc_now)
    price: float = Field(gt=0)
    volume_24h: float | None = Field(default=None, ge=0)


class MarketSnapshot(BaseModel):
    market_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    yes_bid: float | None = None
    yes_ask: float | None = None
    no_bid: float | None = None
    no_ask: float | None = None
    yes_mid: float | None = None
    no_mid: float | None = None
    spread: float | None = None
    volume_24h: float = 0.0
    liquidity: float = 0.0


class FeatureSnapshot(BaseModel):
    market_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    polymarket_yes_mid: float | None = None
    polymarket_no_mid: float | None = None
    spread: float | None = None
    spread_pct: float | None = None
    price_change_1m: float | None = None
    price_change_5m: float | None = None
    price_change_15m: float | None = None
    momentum_1m: float | None = None
    momentum_5m: float | None = None
    momentum_15m: float | None = None
    volume_1m: float = 0.0
    volume_5m: float = 0.0
    volume_15m: float = 0.0
    volume_velocity: float | None = None
    volume_acceleration: float | None = None
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    buy_sell_ratio: float | None = None
    bid_depth_1pct: float = 0.0
    ask_depth_1pct: float = 0.0
    orderbook_imbalance: float | None = None
    top_level_imbalance: float | None = None
    depth_ratio: float | None = None
    external_symbol: str | None = None
    external_spot: float | None = None
    external_return_1m: float | None = None
    external_return_5m: float | None = None
    external_return_15m: float | None = None
    seconds_to_expiry: float
    minutes_to_expiry: float
    distance_to_strike: float | None = None
    standardized_distance_to_strike: float | None = None
    external_price_stale: bool = False


class ScanResult(BaseModel):
    market: Market
    features: FeatureSnapshot
    status: ScanStatus
    rank_score: float
    reasons: list[str] = Field(default_factory=list)

