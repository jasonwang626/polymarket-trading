from datetime import UTC, datetime, timedelta

import pytest

from polymarket_agent.config import load_settings
from polymarket_agent.data.storage import Storage
from polymarket_agent.features.market_features import FeatureEngine
from polymarket_agent.models import ExternalPrice, Market, OrderBook, PriceLevel, Trade


def test_feature_engine_computes_spread_depth_volume_and_distance(tmp_path):
    settings = load_settings()
    settings.storage.sqlite_path = tmp_path / "test.sqlite3"
    storage = Storage(settings.storage.sqlite_path)
    storage.initialize()
    now = datetime.now(UTC)
    market = Market(
        market_id="m1",
        condition_id="c1",
        question="Will Bitcoin be above $60,000?",
        yes_token_id="yes",
        no_token_id="no",
        resolution_time=now + timedelta(hours=1),
        liquidity=10_000,
        volume_24h=50_000,
        strike=60_000,
        underlying="BTC-USD",
    )
    storage.upsert_market(market, now)
    old_external = ExternalPrice(
        symbol="BTC-USD", timestamp=now - timedelta(minutes=6),
        received_at=now - timedelta(minutes=6), price=60_000
    )
    storage.insert_external_price(old_external)
    yes = OrderBook(
        token_id="yes",
        timestamp=now,
        bids=[PriceLevel(price=0.55, size=300)],
        asks=[PriceLevel(price=0.57, size=100)],
    )
    no = OrderBook(
        token_id="no",
        timestamp=now,
        bids=[PriceLevel(price=0.43, size=100)],
        asks=[PriceLevel(price=0.45, size=300)],
    )
    trades = [
        Trade(
            market_id="m1",
            timestamp=now - timedelta(seconds=30),
            price=0.56,
            size=100,
            side="BUY",
        )
    ]
    external = ExternalPrice(symbol="BTC-USD", timestamp=now, price=61_200)

    _, features = FeatureEngine(settings, storage).compute(
        market, yes, no, trades, external, now
    )
    assert features.polymarket_yes_mid == 0.56
    assert round(features.spread or 0, 6) == 0.02
    assert features.orderbook_imbalance == 0.75
    assert features.volume_1m == pytest.approx(56)
    assert round(features.external_return_5m or 0, 6) == 0.02
    assert round(features.distance_to_strike or 0, 6) == 0.02
    assert features.external_price_stale is False
