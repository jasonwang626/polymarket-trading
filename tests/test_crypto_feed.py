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


@pytest.mark.asyncio
async def test_hanging_sources_are_bounded_and_recover_without_leaked_requests():
    import asyncio

    settings = load_settings()
    settings.api.external_request_budget_seconds = 0.01
    active = 0
    hanging = True

    async def handler(request):
        nonlocal active
        active += 1
        try:
            if hanging:
                await asyncio.Event().wait()
            return httpx.Response(200, json={
                "time": datetime.now(UTC).isoformat(), "price": "62500", "volume": "1",
            })
        finally:
            active -= 1

    async with (
        httpx.AsyncClient(base_url=settings.api.coinbase_base_url,
                         transport=httpx.MockTransport(handler)) as coinbase,
        httpx.AsyncClient(base_url=settings.api.kraken_base_url,
                         transport=httpx.MockTransport(handler)) as kraken,
    ):
        feed = CryptoPriceFeed(settings, coinbase, kraken)
        # Repeated failure/recovery also verifies that failed requests do not
        # survive into later cycles or cause cached stale prices to be returned.
        for cycle in range(100):
            hanging = cycle % 2 == 0
            prices = await asyncio.wait_for(feed.get_btc_eth(), timeout=1)
            assert set(prices) == (set() if hanging else {"BTC-USD", "ETH-USD"})
            assert active == 0


@pytest.mark.asyncio
async def test_cancellation_propagates_and_cleans_up_requests():
    import asyncio

    settings = load_settings()
    started = asyncio.Event()
    active = 0

    async def handler(request):
        nonlocal active
        active += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    async with httpx.AsyncClient(base_url=settings.api.coinbase_base_url,
                                transport=httpx.MockTransport(handler)) as client:
        feed = CryptoPriceFeed(settings, client, client)
        task = asyncio.create_task(feed.get_btc_eth())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active == 0


@pytest.mark.asyncio
async def test_slow_primary_uses_fresh_fallback_within_budget():
    import asyncio

    settings = load_settings()
    settings.api.external_request_budget_seconds = 0.01

    async def primary(request):
        await asyncio.Event().wait()

    def fallback(request):
        key = "XXBTZUSD" if request.url.params["pair"] == "XBTUSD" else "XETHZUSD"
        return httpx.Response(200, json={"error": [], "result": {
            key: [["62500", "1", datetime.now(UTC).timestamp()]]}})

    async with (
        httpx.AsyncClient(base_url=settings.api.coinbase_base_url,
                         transport=httpx.MockTransport(primary)) as coinbase,
        httpx.AsyncClient(base_url=settings.api.kraken_base_url,
                         transport=httpx.MockTransport(fallback)) as kraken,
    ):
        prices = await asyncio.wait_for(
            CryptoPriceFeed(settings, coinbase, kraken).get_btc_eth(), timeout=1)
        assert set(prices) == {"BTC-USD", "ETH-USD"}
        assert all(p.venue == "kraken" for p in prices.values())
