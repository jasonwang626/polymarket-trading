import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from polymarket_agent.config import load_settings
from polymarket_agent.data.polymarket_us import parse_book
from polymarket_agent.data.storage import Storage
from polymarket_agent.offline import OfflineDiscovery, OfflineMarketFeed, OfflinePriceFeed
from polymarket_agent.scanner.scanner import Scanner

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.mark.asyncio
@pytest.mark.parametrize('raw,state', [('MARKET_STATE_OPEN', 'OPEN'),
                                      ('MARKET_STATE_HALTED', 'HALTED'),
                                      ('MARKET_STATE_CLOSED', 'CLOSED'),
                                      ('NEW_STATE', 'UNKNOWN'), (None, 'UNKNOWN')])
async def test_state_preserved_on_both_sides(raw, state):
    market = (await OfflineDiscovery(FIXTURES).discover())[0]
    yes, no = parse_book({'marketData': {'marketSlug': market.slug, 'state': raw,
                                       'transactTime': datetime.now(UTC).isoformat()}}, market)
    assert yes.state == no.state == state
    assert yes.raw['marketData']['state'] == raw


@pytest.mark.asyncio
async def test_halted_skips_without_fabricating_books_and_recovers(tmp_path):
    class Feed(OfflineMarketFeed):
        calls = 0
        state = 'HALTED'
        fail = False

        async def get_market_books(self, market):
            self.calls += 1
            if self.fail:
                raise httpx.ReadTimeout('simulated')
            yes, no = await super().get_market_books(market)
            yes.state = no.state = self.state
            return yes, no

    settings = load_settings()
    storage = Storage(tmp_path / 'scanner.sqlite3')
    storage.initialize()
    feed = Feed(FIXTURES)
    scanner = Scanner(settings, storage, OfflineDiscovery(FIXTURES), feed, OfflinePriceFeed())
    first = (await scanner.scan_once())[0]
    key = first.market.market_id
    assert first.status.value == 'NO_TRADE'
    assert '暫停' in first.reasons[0]
    skipped = (await scanner.scan_once())[0]
    assert '本輪未重新讀取' in skipped.reasons[0]
    assert skipped.features.polymarket_yes_mid is None
    assert feed.calls == 1
    assert storage.counts()['orderbook_snapshots'] == 2
    assert storage.counts()['features'] == 2

    scanner._halted_until[key] = asyncio.get_running_loop().time() - 1
    feed.fail = True
    assert (await scanner.scan_once())[0].status.value == 'NO_TRADE'
    await scanner.scan_once()
    assert feed.calls == 2  # Failed rechecks also wait before the next attempt.

    scanner._halted_until[key] = asyncio.get_running_loop().time() - 1
    feed.fail = False
    feed.state = 'OPEN'
    assert (await scanner.scan_once())[0].status.value == 'WATCH'
    assert key not in scanner._halted_until
    await scanner.scan_once()
    assert feed.calls == 4

    scanner._halted_until[key] = asyncio.get_running_loop().time() + 60
    await scanner.scan_once(now=datetime.now(UTC))
    assert feed.calls == 5  # Explicit as-of clocks bypass live polling schedules.
    await scanner.close()
