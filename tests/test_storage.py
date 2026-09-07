from datetime import UTC, datetime, timedelta

from polymarket_agent.data.storage import Storage
from polymarket_agent.models import ExternalPrice, Market, MarketSnapshot


def test_storage_is_append_only_for_snapshots_and_supports_history(tmp_path):
    storage = Storage(tmp_path / "scanner.sqlite3")
    storage.initialize()
    now = datetime.now(UTC)
    market = Market(
        market_id="m1",
        condition_id="c1",
        question="BTC test",
        yes_token_id="yes",
        no_token_id="no",
        resolution_time=now + timedelta(hours=1),
    )
    storage.upsert_market(market, now)
    storage.insert_market_snapshot(MarketSnapshot(market_id="m1", timestamp=now, yes_mid=0.5))
    storage.insert_market_snapshot(
        MarketSnapshot(market_id="m1", timestamp=now + timedelta(seconds=1), yes_mid=0.51)
    )
    storage.insert_external_price(ExternalPrice(symbol="BTC-USD", timestamp=now, price=60_000))

    assert storage.counts()["market_snapshots"] == 2
    assert storage.market_mid_at_or_before("m1", now) == 0.5
    assert storage.external_price_at_or_before("BTC-USD", now) == 60_000

