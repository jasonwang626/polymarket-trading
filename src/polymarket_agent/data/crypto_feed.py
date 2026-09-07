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
        return ExternalPrice(
            symbol=product_id,
            timestamp=datetime.now(UTC),
            price=float(payload["price"]),
            volume_24h=float(payload["volume"]) if payload.get("volume") else None,
        )

    async def get_btc_eth(self) -> dict[str, ExternalPrice]:
        observations = await asyncio.gather(
            *(self.get_spot(x) for x in self.SUPPORTED_PRODUCTS), return_exceptions=True
        )
        successful: dict[str, ExternalPrice] = {}
        for product, observation in zip(self.SUPPORTED_PRODUCTS, observations, strict=True):
            if isinstance(observation, BaseException):
                LOGGER.warning(
                    "External price request failed; affected markets will be NO_TRADE",
                    extra={"product": product, "error": repr(observation)},
                )
            else:
                successful[observation.symbol] = observation
        missing = set(self.SUPPORTED_PRODUCTS) - successful.keys()
        if missing:
            try:
                kraken = await self._get_kraken_spots()
                for product in missing:
                    if product in kraken:
                        successful[product] = kraken[product]
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

    async def _get_kraken_spots(self) -> dict[str, ExternalPrice]:
        response = await self.kraken_client.get(
            "/0/public/Ticker", params={"pair": "XBTUSD,ETHUSD"}
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise ValueError(f"Kraken ticker error: {payload['error']}")
        now = datetime.now(UTC)
        prices: dict[str, ExternalPrice] = {}
        for key, ticker in payload["result"].items():
            product = "BTC-USD" if "XBT" in key or "BTC" in key else "ETH-USD" if "ETH" in key else None
            if product:
                prices[product] = ExternalPrice(
                    symbol=product,
                    venue="kraken",
                    timestamp=now,
                    price=float(ticker["c"][0]),
                    volume_24h=float(ticker["v"][1]) if ticker.get("v") else None,
                )
        return prices
