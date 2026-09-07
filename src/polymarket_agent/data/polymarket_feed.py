from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from polymarket_agent.config import Settings
from polymarket_agent.models import Market, OrderBook, PriceLevel, Trade


def _epoch_to_datetime(value: Any) -> datetime:
    number = float(value)
    if number > 10_000_000_000:
        number /= 1000
    return datetime.fromtimestamp(number, tz=UTC)


class PolymarketFeed:
    """Read-only adapter for public CLOB and Data API GET endpoints."""

    def __init__(
        self,
        settings: Settings,
        clob_client: httpx.AsyncClient | None = None,
        data_client: httpx.AsyncClient | None = None,
    ):
        headers = {"User-Agent": settings.api.user_agent}
        self.settings = settings
        self._owns_clob = clob_client is None
        self._owns_data = data_client is None
        self.clob_client = clob_client or httpx.AsyncClient(
            base_url=settings.api.clob_base_url,
            timeout=settings.api.timeout_seconds,
            headers=headers,
        )
        self.data_client = data_client or httpx.AsyncClient(
            base_url=settings.api.data_base_url,
            timeout=settings.api.timeout_seconds,
            headers=headers,
        )

    async def close(self) -> None:
        if self._owns_clob:
            await self.clob_client.aclose()
        if self._owns_data:
            await self.data_client.aclose()

    async def get_order_book(self, token_id: str) -> OrderBook:
        response = await self.clob_client.get("/book", params={"token_id": token_id})
        response.raise_for_status()
        payload = response.json()
        bids = [PriceLevel(price=float(x["price"]), size=float(x["size"])) for x in payload.get("bids", [])]
        asks = [PriceLevel(price=float(x["price"]), size=float(x["size"])) for x in payload.get("asks", [])]
        timestamp = payload.get("timestamp")
        parsed_timestamp = _epoch_to_datetime(timestamp) if timestamp else datetime.now(UTC)
        last = payload.get("last_trade_price")
        return OrderBook(
            token_id=str(payload.get("asset_id") or token_id),
            timestamp=parsed_timestamp,
            bids=bids,
            asks=asks,
            last_trade_price=float(last) if last not in (None, "") else None,
            raw=payload,
        )

    async def get_market_books(self, market: Market) -> tuple[OrderBook, OrderBook]:
        yes_book, no_book = await asyncio.gather(
            self.get_order_book(market.yes_token_id),
            self.get_order_book(market.no_token_id),
        )
        return yes_book, no_book

    async def get_recent_trades(self, market: Market) -> list[Trade]:
        response = await self.data_client.get(
            "/trades",
            params={"market": market.condition_id, "limit": self.settings.scanner.recent_trades_limit},
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            return []
        trades: list[Trade] = []
        for row in rows:
            try:
                trades.append(
                    Trade(
                        market_id=market.market_id,
                        timestamp=_epoch_to_datetime(row["timestamp"]),
                        price=float(row["price"]),
                        size=float(row["size"]),
                        side=str(row.get("side") or "").upper(),
                        outcome=str(row.get("outcome") or ""),
                        transaction_hash=str(row.get("transactionHash") or ""),
                        raw=row,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return trades

