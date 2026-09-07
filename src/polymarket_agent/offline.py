from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from polymarket_agent.discovery.polymarket import parse_market
from polymarket_agent.models import ExternalPrice, Market, OrderBook, PriceLevel, Trade


class OfflineDiscovery:
    def __init__(self, fixture_dir: Path):
        self.fixture_dir = fixture_dir

    async def close(self) -> None:
        return None

    async def discover(self, now: datetime | None = None) -> list[Market]:
        now = now or datetime.now(UTC)
        payload = json.loads((self.fixture_dir / "market.json").read_text(encoding="utf-8"))
        payload["endDate"] = (now + timedelta(minutes=30)).isoformat()
        market = parse_market(payload)
        if not market:
            raise ValueError("Offline market fixture is invalid")
        return [market]


class OfflineMarketFeed:
    def __init__(self, fixture_dir: Path):
        self.fixture_dir = fixture_dir

    async def close(self) -> None:
        return None

    def _book(self, filename: str, token_id: str, now: datetime) -> OrderBook:
        payload = json.loads((self.fixture_dir / filename).read_text(encoding="utf-8"))
        return OrderBook(
            token_id=token_id,
            timestamp=now,
            bids=[PriceLevel(**x) for x in payload["bids"]],
            asks=[PriceLevel(**x) for x in payload["asks"]],
            last_trade_price=float(payload["last_trade_price"]),
            raw=payload,
        )

    async def get_market_books(self, market: Market) -> tuple[OrderBook, OrderBook]:
        now = datetime.now(UTC)
        return (
            self._book("yes_book.json", market.yes_token_id, now),
            self._book("no_book.json", market.no_token_id, now),
        )

    async def get_recent_trades(self, market: Market) -> list[Trade]:
        now = datetime.now(UTC)
        rows = json.loads((self.fixture_dir / "trades.json").read_text(encoding="utf-8"))
        return [
            Trade(
                market_id=market.market_id,
                timestamp=now - timedelta(seconds=row["seconds_ago"]),
                price=row["price"],
                size=row["size"],
                side=row["side"],
                outcome="Yes",
                transaction_hash=row["transaction_hash"],
                raw=row,
            )
            for row in rows
        ]


class OfflinePriceFeed:
    async def close(self) -> None:
        return None

    async def get_btc_eth(self) -> dict[str, ExternalPrice]:
        now = datetime.now(UTC)
        return {
            "BTC-USD": ExternalPrice(symbol="BTC-USD", timestamp=now, price=62_500),
            "ETH-USD": ExternalPrice(symbol="ETH-USD", timestamp=now, price=2_450),
        }

