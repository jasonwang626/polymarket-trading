from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx

from polymarket_agent.config import Settings
from polymarket_agent.models import ExternalPrice

LOGGER = logging.getLogger(__name__)


class CryptoPriceFeed:
    """Public Coinbase spot ticker adapter; no authentication or trading methods."""

    SUPPORTED_PRODUCTS = ("BTC-USD", "ETH-USD")

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        kraken_client: httpx.AsyncClient | None = None,
    ):
        self.settings = settings
        self._owns_client = client is None
        self._owns_kraken_client = kraken_client is None
        self.client = client or httpx.AsyncClient(
            base_url=settings.api.coinbase_base_url,
            timeout=settings.api.timeout_seconds,
            headers={"User-Agent": settings.api.user_agent},
        )
        self.kraken_client = kraken_client or httpx.AsyncClient(
            base_url=settings.api.kraken_base_url,
            timeout=settings.api.timeout_seconds,
            headers={"User-Agent": settings.api.user_agent},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
        if self._owns_kraken_client:
            await self.kraken_client.aclose()

    async def get_spot(self, product_id: str) -> ExternalPrice:
        if product_id not in self.SUPPORTED_PRODUCTS:
            raise ValueError(f"Unsupported public price product: {product_id}")
        response = await self.client.get(f"/products/{product_id}/ticker")
        response.raise_for_status()
        payload = response.json()
        timestamp = datetime.fromisoformat(payload["time"])
        if timestamp.tzinfo is None:
            raise ValueError("Coinbase ticker time lacks timezone")
        return ExternalPrice(
            symbol=product_id,
            timestamp=timestamp,
            received_at=datetime.now(UTC),
            price=float(payload["price"]),
            volume_24h=float(payload["volume"]) if payload.get("volume") else None,
        )

    async def get_btc_eth(self) -> dict[str, ExternalPrice]:
        observations = await asyncio.gather(
            *(asyncio.wait_for(self.get_spot(x), self.settings.api.external_request_budget_seconds)
              for x in self.SUPPORTED_PRODUCTS), return_exceptions=True
        )
        successful: dict[str, ExternalPrice] = {}
        for product, observation in zip(self.SUPPORTED_PRODUCTS, observations, strict=True):
            if isinstance(observation, asyncio.CancelledError):
                raise observation
            if isinstance(observation, Exception):
                LOGGER.warning(
                    "External price request failed; affected markets will be NO_TRADE",
                    extra={"product": product, "error": repr(observation)},
                )
            elif not 0 <= (datetime.now(UTC) - observation.timestamp).total_seconds() <= (
                self.settings.scanner.external_price_stale_seconds
            ):
                LOGGER.warning("Coinbase source timestamp is stale or in the future",
                               extra={"product": product})
            else:
                successful[observation.symbol] = observation
        missing = set(self.SUPPORTED_PRODUCTS) - successful.keys()
        if missing:
            try:
                kraken = await self._get_kraken_spots(sorted(missing))
                for product in missing:
                    if product in kraken:
                        successful[product] = kraken[product]
                if kraken:
                    LOGGER.info(
                        "Used public Kraken fallback prices",
                        extra={"products": sorted(missing & kraken.keys())},
                    )
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                LOGGER.warning(
                    "Kraken fallback price request failed; affected markets will be NO_TRADE",
                    extra={"products": sorted(missing), "error": repr(exc)},
                )
        return successful

    async def _get_kraken_spots(self, products: list[str]) -> dict[str, ExternalPrice]:
        # Kraken Ticker lacks the exchange timestamp of its last price. Recent
        # Trades supplies that timestamp; do not label receipt time as price time.
        async def fetch(product: str) -> ExternalPrice:
            pair = {"BTC-USD": "XBTUSD", "ETH-USD": "ETHUSD"}[product]
            response = await self.kraken_client.get(
                "/0/public/Trades", params={"pair": pair, "count": 1}
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise ValueError(f"Kraken public trade error: {payload['error']}")
            expected = {"BTC-USD": "XXBTZUSD", "ETH-USD": "XETHZUSD"}[product]
            row = payload["result"][expected][-1]
            return ExternalPrice(
                symbol=product, venue="kraken",
                timestamp=datetime.fromtimestamp(float(row[2]), UTC),
                received_at=datetime.now(UTC), price=float(row[0]),
            )

        results = await asyncio.gather(
            *(asyncio.wait_for(fetch(x), self.settings.api.external_request_budget_seconds)
              for x in products), return_exceptions=True
        )
        prices: dict[str, ExternalPrice] = {}
        for product, value in zip(products, results, strict=True):
            if isinstance(value, asyncio.CancelledError):
                raise value
            if isinstance(value, ExternalPrice) and 0 <= (
                datetime.now(UTC) - value.timestamp
            ).total_seconds() <= self.settings.scanner.external_price_stale_seconds:
                prices[product] = value
            else:
                LOGGER.warning("Kraken public trade request failed or source timestamp invalid",
                               extra={"product": product,
                                      "error": type(value).__name__ if isinstance(value, ExternalPrice)
                                      else repr(value)})
        return prices
