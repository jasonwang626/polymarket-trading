from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from polymarket_agent.config import Settings
from polymarket_agent.data.storage import Storage
from polymarket_agent.models import (
    ExternalPrice,
    FeatureSnapshot,
    Market,
    MarketSnapshot,
    OrderBook,
    Trade,
)


def _return(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return current / previous - 1


def _depth(book: OrderBook) -> tuple[float, float, float | None, float | None, float | None]:
    best_bid, best_ask = book.best_bid, book.best_ask
    bid_depth = (
        sum(level.size for level in book.bids if best_bid and level.price >= best_bid * 0.99)
        if best_bid
        else 0.0
    )
    ask_depth = (
        sum(level.size for level in book.asks if best_ask and level.price <= best_ask * 1.01)
        if best_ask
        else 0.0
    )
    total = bid_depth + ask_depth
    imbalance = bid_depth / total if total else None
    top_bid = sum(level.size for level in book.bids if level.price == best_bid)
    top_ask = sum(level.size for level in book.asks if level.price == best_ask)
    top_total = top_bid + top_ask
    top_imbalance = top_bid / top_total if top_total else None
    depth_ratio = bid_depth / ask_depth if ask_depth else None
    return bid_depth, ask_depth, imbalance, top_imbalance, depth_ratio


def _volume_features(trades: list[Trade], now: datetime) -> dict[str, float | None]:
    def notional(minutes: int, *, side: str | None = None) -> float:
        cutoff = now - timedelta(minutes=minutes)
        return sum(
            trade.price * trade.size
            for trade in trades
            if trade.timestamp >= cutoff and (side is None or trade.side == side)
        )

    volume_1m = notional(1)
    volume_5m = notional(5)
    volume_15m = notional(15)
    older_4m = max(volume_5m - volume_1m, 0.0)
    baseline_1m = volume_5m / 5 if volume_5m else 0.0
    previous_1m = older_4m / 4 if older_4m else 0.0
    buy_volume = notional(15, side="BUY")
    sell_volume = notional(15, side="SELL")
    return {
        "volume_1m": volume_1m,
        "volume_5m": volume_5m,
        "volume_15m": volume_15m,
        "volume_velocity": volume_1m / baseline_1m if baseline_1m else None,
        "volume_acceleration": volume_1m / previous_1m if previous_1m else None,
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "buy_sell_ratio": buy_volume / sell_volume if sell_volume else None,
    }


class FeatureEngine:
    def __init__(self, settings: Settings, storage: Storage):
        self.settings = settings
        self.storage = storage

    def compute(
        self,
        market: Market,
        yes_book: OrderBook,
        no_book: OrderBook,
        trades: list[Trade],
        external: ExternalPrice | None,
        now: datetime | None = None,
    ) -> tuple[MarketSnapshot, FeatureSnapshot]:
        now = now or datetime.now(UTC)
        yes_mid = yes_book.mid
        no_mid = no_book.mid
        spread = yes_book.spread
        snapshot = MarketSnapshot(
            market_id=market.market_id,
            timestamp=now,
            yes_bid=yes_book.best_bid,
            yes_ask=yes_book.best_ask,
            no_bid=no_book.best_bid,
            no_ask=no_book.best_ask,
            yes_mid=yes_mid,
            no_mid=no_mid,
            spread=spread,
            volume_24h=market.volume_24h,
            liquidity=market.liquidity,
        )

        previous_mid = {
            minutes: self.storage.market_mid_at_or_before(
                market.market_id, now - timedelta(minutes=minutes)
            )
            for minutes in (1, 5, 15)
        }
        external_previous = {
            minutes: self.storage.external_price_at_or_before(
                market.underlying, now - timedelta(minutes=minutes)
            )
            if market.underlying
            else None
            for minutes in (1, 5, 15)
        }
        bid_depth, ask_depth, imbalance, top_imbalance, depth_ratio = _depth(yes_book)
        volume = _volume_features(trades, now)
        seconds_to_expiry = max((market.resolution_time - now).total_seconds(), 0.0)
        external_spot = external.price if external else None
        stale = (
            external is None
            or (now - external.timestamp).total_seconds()
            > self.settings.scanner.external_price_stale_seconds
        )
        distance = (
            (external_spot - market.strike) / market.strike
            if external_spot is not None and market.strike
            else None
        )
        vol_scale = abs(_return(external_spot, external_previous[15]) or 0.0)

        features = FeatureSnapshot(
            market_id=market.market_id,
            timestamp=now,
            polymarket_yes_mid=yes_mid,
            polymarket_no_mid=no_mid,
            spread=spread,
            spread_pct=spread / yes_mid if spread is not None and yes_mid else None,
            price_change_1m=_return(yes_mid, previous_mid[1]),
            price_change_5m=_return(yes_mid, previous_mid[5]),
            price_change_15m=_return(yes_mid, previous_mid[15]),
            momentum_1m=_return(yes_mid, previous_mid[1]),
            momentum_5m=_return(yes_mid, previous_mid[5]),
            momentum_15m=_return(yes_mid, previous_mid[15]),
            bid_depth_1pct=bid_depth,
            ask_depth_1pct=ask_depth,
            orderbook_imbalance=imbalance,
            top_level_imbalance=top_imbalance,
            depth_ratio=depth_ratio,
            external_symbol=market.underlying,
            external_spot=external_spot,
            external_return_1m=_return(external_spot, external_previous[1]),
            external_return_5m=_return(external_spot, external_previous[5]),
            external_return_15m=_return(external_spot, external_previous[15]),
            seconds_to_expiry=seconds_to_expiry,
            minutes_to_expiry=seconds_to_expiry / 60,
            distance_to_strike=distance,
            standardized_distance_to_strike=(distance / vol_scale if distance is not None and vol_scale else None),
            external_price_stale=stale,
            **volume,
        )
        return snapshot, features


def rank_score(market: Market, features: FeatureSnapshot) -> float:
    spread_penalty = (features.spread or 1.0) * 25
    volume_bonus = math.log1p(features.volume_15m)
    liquidity_bonus = math.log1p(market.liquidity) * 0.5
    imbalance_bonus = abs((features.orderbook_imbalance or 0.5) - 0.5) * 2
    return liquidity_bonus + volume_bonus + imbalance_bonus - spread_penalty

