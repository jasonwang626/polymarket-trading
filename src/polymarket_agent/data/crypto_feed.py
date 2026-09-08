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

    def _accept_price(self, observation: ExternalPrice) -> bool:
        checked_at = datetime.now(UTC)
        age = (checked_at - observation.timestamp).total_seconds()
        limit = self.settings.scanner.external_price_stale_seconds
        reason = "future_timestamp" if age < 0 else "stale" if age > limit else "accepted"
        fields = {"venue": observation.venue, "product": observation.symbol,
                      "source_timestamp": observation.timestamp.isoformat(),
                      "received_at": observation.received_at.isoformat(),
                      "checked_at": checked_at.isoformat(), "age_seconds": age,
                      "max_age_seconds": limit, "reason": reason}
        LOGGER.log(logging.INFO if reason == "accepted" else logging.WARNING,
                   "External price %s product=%s venue=%s source=%s received=%s "
                   "checked=%s age_seconds=%.6f limit_seconds=%s",
                   reason, observation.symbol, observation.venue,
                   fields["source_timestamp"], fields["received_at"],
                   fields["checked_at"], age, limit, extra=fields)
        return reason == "accepted"

    @staticmethod
    def _log_failure(product: str, venue: str, error: Exception) -> None:
        if isinstance(error, (TimeoutError, httpx.TimeoutException)):
            reason = "timeout"
        elif isinstance(error, httpx.HTTPStatusError):
            reason = "http_status_error"
        elif isinstance(error, httpx.HTTPError):
            reason = "transport_error"
        else:
            reason = "parse_or_validation_error"
        # Record the exception type, not arbitrary payloads or request headers.
        LOGGER.warning("External price rejected product=%s venue=%s reason=%s error_type=%s",
                       product, venue, reason, type(error).__name__,
                       extra={"product": product, "venue": venue, "reason": reason,
                                  "error_type": type(error).__name__})

    async def get_spot(self, product_id: str) -> ExternalPrice:
        if product_id not in self.SUPPORTED_PRODUCTS:
            raise ValueError(f"Unsupported public price product: {product_id}")
        response = await self.client.get(f"/products/{product_id}/ticker")
        response.raise_for_status()
        payload = response.json()
        LOGGER.info("External source timestamp product=%s venue=coinbase raw_time=%r",
                    product_id, str(payload.get("time"))[:200])
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
                self._log_failure(product, "coinbase", observation)
            elif self._accept_price(observation):
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
                        "Used public Kraken fallback prices products=%s",
                    sorted(missing & kraken.keys()),
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
            LOGGER.info("External source timestamp product=%s venue=kraken raw_time=%r",
                        product, str(row[2])[:200])
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
            if isinstance(value, ExternalPrice):
                if self._accept_price(value):
                    prices[product] = value
            else:
                self._log_failure(product, "kraken", value)
        return prices
