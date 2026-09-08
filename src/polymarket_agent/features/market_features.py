from __future__ import annotations

import logging
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

LOGGER = logging.getLogger(__name__)

def _return(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return current / previous - 1


def _depth(book: OrderBook) -> tuple[float, float, float | None, float | None, float | None]:
    best_bid, best_ask = book.best_bid, book.best_ask
    bid_depth = (
        sum(level.size for level in book.bids if level.price >= best_bid * 0.99)
        if best_bid is not None
        else 0.0
    )
    ask_depth = (
        sum(level.size for level in book.asks if level.price <= best_ask * 1.01)
        if best_ask is not None
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
            if cutoff <= trade.timestamp <= now and (side is None or trade.side == side)
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
        trades: list[Trade] | None,
        external: ExternalPrice | None,
        now: datetime | None = None,
    ) -> tuple[MarketSnapshot, FeatureSnapshot]:
        now = now or datetime.now(UTC)
        issues: list[str] = []
        for label, book in (("YES", yes_book), ("NO", no_book)):
            age = (now - book.timestamp).total_seconds()
            limit = self.settings.scanner.orderbook_stale_seconds
            reason = "future_timestamp" if age < 0 else "stale" if age > limit else "accepted"
            LOGGER.info(
                "Book timestamp market=%s outcome=%s synthetic=%s source=%s received=%s "
                "age_seconds=%.6f limit_seconds=%s reason=%s",
                market.market_id, label, book.synthetic, book.timestamp.isoformat(),
                book.received_at.isoformat(), age, limit, reason,
                extra={"market_id": market.market_id, "outcome": label, "synthetic": book.synthetic,
                           "source_timestamp": book.timestamp.isoformat(),
                           "received_at": book.received_at.isoformat(), "checked_at": now.isoformat(),
                           "age_seconds": age, "max_age_seconds": limit, "reason": reason},
            )
            if age < 0:
                issues.append(f"{label} 委託簿時間在未來（超前 {-age:.3f} 秒）")
            elif age > limit:
                issues.append(f"{label} 委託簿時間過期（{age:.1f} 秒；上限 {limit} 秒）")
            if book.mid is None:
                issues.append(f"{label} 委託簿缺少雙邊報價或買賣價交叉")
            if book.state != "OPEN":
                issues.append(f"{label} 市場未開放")
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
        if issues:
            snapshot.yes_mid = None
            snapshot.no_mid = None

        previous_mid = {
            minutes: self.storage.market_mid_at_or_before(
                market.market_id, now - timedelta(minutes=minutes),
                self.settings.scanner.history_tolerance_seconds,
            )
            for minutes in (1, 5, 15)
        }
        external_previous = {
            minutes: self.storage.external_price_at_or_before(
                market.underlying, now - timedelta(minutes=minutes),
                self.settings.scanner.history_tolerance_seconds,
                venue=external.venue if external else None,
            )
            if market.underlying
            else None
            for minutes in (1, 5, 15)
        }
        bid_depth, ask_depth, imbalance, top_imbalance, depth_ratio = _depth(yes_book)
        valid_trades = [
            t for t in trades or []
            if t.market_id == market.market_id and now - timedelta(minutes=15) <= t.timestamp <= now
        ]
        volume = _volume_features(valid_trades, now) if trades is not None else {}
        seconds_to_expiry = max((market.resolution_time - now).total_seconds(), 0.0)
        external_spot = external.price if external else None
        stale = (
            external is None
            or external.symbol != market.underlying
            or not 0 <= (now - external.timestamp).total_seconds()
            <= self.settings.scanner.external_price_stale_seconds
        )
        distance = (
            (external_spot - market.strike) / market.strike
            if external_spot is not None and market.strike
            and market.contract_type != "terminal_range" and not stale
            else None
        )
        if stale:
            external_previous = dict.fromkeys((1, 5, 15))

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
            # A single absolute return is not a volatility estimate.
            standardized_distance_to_strike=None,
            external_price_stale=stale,
            data_issues=issues,
            recent_trades_available=trades is not None,
            recent_trade_count=len(valid_trades),
            bid_depth_usd=sum(x.price * x.size for x in yes_book.bids
                              if yes_book.best_bid is not None
                              and x.price >= yes_book.best_bid * 0.99),
            ask_depth_usd=sum(x.price * x.size for x in yes_book.asks
                              if yes_book.best_ask is not None
                              and x.price <= yes_book.best_ask * 1.01),
            **volume,
        )
        return snapshot, features


def rank_score(market: Market, features: FeatureSnapshot) -> float:
    spread_penalty = (features.spread if features.spread is not None else 1.0) * 25
    volume_bonus = math.log1p(features.volume_15m or 0)
    liquidity_bonus = math.log1p(market.liquidity) * 0.5
    imbalance_bonus = abs(
        (features.orderbook_imbalance if features.orderbook_imbalance is not None else 0.5) - 0.5
    ) * 2
    return liquidity_bonus + volume_bonus + imbalance_bonus - spread_penalty
