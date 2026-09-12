"""Explicitly synthetic, network-free US examples; use a separate database."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from polymarket_agent.data.polymarket_us import parse_book
from polymarket_agent.discovery.polymarket_us import DEADLINE, parse_us_market
from polymarket_agent.models import ExternalPrice, Market, OrderBook


class OfflineDiscovery:
    def __init__(self, fixture_dir: Path):
        self.fixture_dir = fixture_dir

    async def close(self) -> None:
        return None

    async def discover(self, now: datetime | None = None) -> list[Market]:
        now = now or datetime.now(UTC)
        payload = json.loads((self.fixture_dir / "us_markets.json").read_text())
        row = payload["events"][0]["markets"][0]
        row["slug"] = "cpc-btc-offline-fixture"
        row["question"] += " [OFFLINE 合成範例]"
        for side in row["marketSides"]:
            side["identifier"] = row["slug"]
        future = (now + timedelta(days=30)).astimezone(ZoneInfo("America/New_York"))
        date = future.strftime("12:00 AM ET on %B %d, %Y")
        row["description"] = DEADLINE.sub(date, row["description"])
        row["endDate"] = (now + timedelta(days=31)).isoformat()
        market = parse_us_market(row)
        if market is None:
            raise ValueError("Invalid US offline fixture")
        return [market]


class OfflineMarketFeed:
    def __init__(self, fixture_dir: Path):
        self.fixture_dir = fixture_dir

    async def close(self) -> None:
        return None

    async def get_market_books(self, market: Market) -> tuple[OrderBook, OrderBook]:
        return parse_book({"marketData": {
            "marketSlug": market.slug, "state": "MARKET_STATE_OPEN",
            "transactTime": datetime.now(UTC).isoformat(),
            "bids": [{"px": {"value": "0.55", "currency": "USD"}, "qty": "3000"}],
            "offers": [{"px": {"value": "0.57", "currency": "USD"}, "qty": "1000"}],
            "offline": True,
        }}, market)

    async def get_recent_trades(self, market: Market) -> None:
        return None


class OfflinePriceFeed:
    async def close(self) -> None:
        return None

    async def get_btc_eth(self) -> dict[str, ExternalPrice]:
        now = datetime.now(UTC)
        return {
            "BTC-USD": ExternalPrice(symbol="BTC-USD", venue="offline", timestamp=now, price=62_500),
            "ETH-USD": ExternalPrice(symbol="ETH-USD", venue="offline", timestamp=now, price=2_450),
        }
