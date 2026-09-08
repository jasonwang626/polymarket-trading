from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from polymarket_agent.config import Settings
from polymarket_agent.data.public_http import PublicAPI
from polymarket_agent.models import Market, OrderBook, PriceLevel, QuoteHistoryPoint


def _time(text: str) -> datetime:
    stamp = datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        raise ValueError("Book timestamp must include timezone")
    return stamp.astimezone(UTC)


def parse_book(payload: dict[str, Any], market: Market) -> tuple[OrderBook, OrderBook]:
    row = payload["marketData"]
    if row.get("marketSlug") != market.slug:
        raise ValueError("Order book belongs to a different market")

    def levels(key: str) -> list[PriceLevel]:
        output = []
        for level in row.get(key, []):
            if level["px"].get("currency") != "USD":
                raise ValueError("Unexpected quote currency")
            value = PriceLevel(price=level["px"]["value"], size=level["qty"])
            if value.size > 0:
                output.append(value)
        return output

    stamp, received = _time(row["transactTime"]), datetime.now(UTC)
    yes = OrderBook(
        instrument_id=market.instrument_id,
        timestamp=stamp,
        received_at=received,
        bids=levels("bids"),
        asks=levels("offers"),
        state={"MARKET_STATE_OPEN": "OPEN", "MARKET_STATE_HALTED": "HALTED",
               "MARKET_STATE_CLOSED": "CLOSED"}.get(row.get("state"), "UNKNOWN"),
        raw=payload,
    )

    def complement(items: list[PriceLevel]) -> list[PriceLevel]:
        return [
            PriceLevel(price=float(Decimal(1) - Decimal(str(x.price))), size=x.size) for x in items
        ]

    # This is a display transformation of ONE instrument, not another book/token.
    no = OrderBook(
        instrument_id=market.instrument_id,
        synthetic=True,
        timestamp=stamp,
        received_at=received,
        state=yes.state,
        bids=complement(yes.asks),
        asks=complement(yes.bids),
        raw={"derived_from": market.instrument_id, "transformation": "1-price; swap sides"},
    )
    return yes, no


class PolymarketUSFeed:
    def __init__(self, settings: Settings, api: PublicAPI | None = None):
        self.api = api or PublicAPI(settings)
        self._owns_api = api is None
        self._history_cache: OrderedDict[tuple, tuple[float, list[QuoteHistoryPoint]]] = (
            OrderedDict()
        )

    async def close(self) -> None:
        if self._owns_api:
            await self.api.close()

    async def get_market_books(self, market: Market) -> tuple[OrderBook, OrderBook]:
        if market.venue != "polymarket_us":
            raise ValueError("US feed requires a US market")
        return parse_book(await self.api.get(f"/v1/markets/{market.slug}/book"), market)

    async def get_recent_trades(self, market: Market) -> None:
        # Public REST history is book-derived quotes, NOT a trade tape.
        # Authenticated trade WebSockets are outside this read-only, key-free sprint.
        return None

    async def get_price_history(
        self,
        market: Market,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        interval: str = "INTERVAL_1M",
    ) -> list[QuoteHistoryPoint]:
        if market.venue != "polymarket_us":
            raise ValueError("US history requires a US market")
        params: dict[str, Any] = {"symbol": market.slug}
        if start is not None or end is not None:
            if start is None or end is None or start.tzinfo is None or end.tzinfo is None:
                raise ValueError("History requires two timezone-aware timestamps")
            if not 0 < (end - start).total_seconds() <= 86400:
                raise ValueError("Custom history windows must be at most 24 hours")
            params.update(
                {
                    "timestamp.startTimestamp": int(start.timestamp()),
                    "timestamp.endTimestamp": int(end.timestamp()),
                    "fidelity": 1,
                }
            )
        else:
            profiles = {
                "INTERVAL_1H": 1,
                "INTERVAL_6H": 1,
                "INTERVAL_1D": 5,
                "INTERVAL_1W": 180,
                "INTERVAL_1M": 180,
                "INTERVAL_ALL": 180,
            }
            if interval not in profiles:
                raise ValueError("Unsupported history interval")
            params.update(fixedInterval=interval, fidelity=profiles[interval])
        key = tuple(sorted(params.items()))
        now = datetime.now(UTC).timestamp()
        cached = self._history_cache.get(key)
        if cached and now - cached[0] < 30:
            return [x.model_copy(deep=True) for x in cached[1]]
        payload = await self.api.get("/v1/price-history", params)
        if not isinstance(payload.get("history"), list):
            raise TypeError("History response lacks an array")
        points = []
        for row in payload["history"]:
            stamp = datetime.fromtimestamp(row["timestamp"], UTC)
            if start is not None and not start <= stamp < end:
                continue
            if stamp.timestamp() > datetime.now(UTC).timestamp():
                continue
            points.append(
                QuoteHistoryPoint(
                    market_id=market.market_id,
                    timestamp=stamp,
                    yes_ask=row["longPrice"],
                    no_ask=row["shortPrice"],
                )
            )
        points.sort(key=lambda x: x.timestamp)
        self._history_cache[key] = (datetime.now(UTC).timestamp(), points)
        self._history_cache.move_to_end(key)
        while len(self._history_cache) > 128:
            self._history_cache.popitem(last=False)
        return [x.model_copy(deep=True) for x in points]
