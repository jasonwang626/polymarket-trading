from datetime import UTC, datetime, timedelta

from polymarket_agent.config import load_settings
from polymarket_agent.models import FeatureSnapshot, Market, ScanStatus
from polymarket_agent.scanner.scanner import classify


def test_classifier_possible_yes():
    settings = load_settings()
    now = datetime.now(UTC)
    market = Market(
        market_id="m1",
        condition_id="c1",
        question="BTC up?",
        yes_token_id="yes",
        no_token_id="no",
        resolution_time=now + timedelta(hours=1),
        liquidity=10_000,
    )
    features = FeatureSnapshot(
        market_id="m1",
        timestamp=now,
        spread=0.02,
        orderbook_imbalance=0.7,
        momentum_5m=0.01,
        external_return_5m=0.002,
        seconds_to_expiry=3600,
        minutes_to_expiry=60,
        external_price_stale=False,
        recent_trades_available=True,
        recent_trade_count=1,
    )
    status, reasons = classify(features, market, settings)
    assert status == ScanStatus.POSSIBLE_YES
    assert len(reasons) == 3


def test_classifier_blocks_stale_data():
    settings = load_settings()
    now = datetime.now(UTC)
    market = Market(
        market_id="m1",
        condition_id="c1",
        question="BTC up?",
        yes_token_id="yes",
        no_token_id="no",
        resolution_time=now + timedelta(hours=1),
        liquidity=10_000,
    )
    features = FeatureSnapshot(
        market_id="m1",
        seconds_to_expiry=3600,
        minutes_to_expiry=60,
        external_price_stale=True,
    )
    assert classify(features, market, settings)[0] == ScanStatus.NO_TRADE
