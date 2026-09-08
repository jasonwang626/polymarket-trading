from __future__ import annotations

from pathlib import Path

import pytest

from polymarket_agent.config import load_settings
from polymarket_agent.data.storage import Storage
from polymarket_agent.offline import OfflineDiscovery, OfflineMarketFeed, OfflinePriceFeed
from polymarket_agent.scanner.scanner import Scanner


@pytest.mark.asyncio
async def test_offline_scan_persists_complete_cycle(tmp_path):
    settings = load_settings()
    settings.storage.sqlite_path = tmp_path / "scanner.sqlite3"
    fixture_dir = Path(__file__).parent / "fixtures"
    storage = Storage(settings.storage.sqlite_path)
    storage.initialize()
    scanner = Scanner(
        settings,
        storage,
        OfflineDiscovery(fixture_dir),
        OfflineMarketFeed(fixture_dir),
        OfflinePriceFeed(),
    )

    results = await scanner.scan_once()
    await scanner.close()

    assert len(results) == 1
    assert results[0].market.underlying == "BTC-USD"
    counts = storage.counts()
    assert counts["markets"] == 1
    assert counts["market_snapshots"] == 1
    assert counts["orderbook_snapshots"] == 2
    assert counts["external_prices"] == 2
    assert counts["market_trades"] == 0
    assert results[0].market.venue == "polymarket_us"
    assert results[0].features.volume_5m is None
    assert results[0].status.value == "WATCH"
    assert counts["features"] == 1
