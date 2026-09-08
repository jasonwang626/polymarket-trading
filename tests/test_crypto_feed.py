from datetime import UTC, datetime

import httpx
import pytest

from polymarket_agent.config import load_settings
from polymarket_agent.data.crypto_feed import CryptoPriceFeed


@pytest.mark.asyncio
async def test_kraken_fallback_when_coinbase_is_unavailable():
    def coinbase_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, request=request)

    def kraken_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/0/public/Trades"
        pair = request.url.params["pair"]
        key, price = ("XXBTZUSD", "62500") if pair == "XBTUSD" else ("XETHZUSD", "2450")
        return httpx.Response(
            200,
            request=request,
            json={
                "error": [],
                "result": {key: [[price, "1", datetime.now(UTC).timestamp(), "b", "m", "", 1]]},
            },
        )

    settings = load_settings()
    async with (
        httpx.AsyncClient(
            base_url=settings.api.coinbase_base_url,
            transport=httpx.MockTransport(coinbase_handler),
        ) as coinbase,
        httpx.AsyncClient(
            base_url=settings.api.kraken_base_url,
            transport=httpx.MockTransport(kraken_handler),
        ) as kraken,
    ):
        feed = CryptoPriceFeed(settings, client=coinbase, kraken_client=kraken)
        prices = await feed.get_btc_eth()

    assert prices["BTC-USD"].price == 62_500
    assert prices["ETH-USD"].price == 2_450
    assert prices["BTC-USD"].venue == "kraken"
