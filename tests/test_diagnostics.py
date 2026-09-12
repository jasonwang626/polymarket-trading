import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from polymarket_agent.config import load_settings
from polymarket_agent.data.crypto_feed import CryptoPriceFeed
from polymarket_agent.data.storage import Storage
from polymarket_agent.features.market_features import FeatureEngine
from polymarket_agent.logging import JsonFormatter
from polymarket_agent.models import ExternalPrice, Market, OrderBook


@pytest.mark.asyncio
@pytest.mark.parametrize('offset,reason', [(-60, 'stale'), (60, 'future_timestamp'), (-1, 'accepted')])
async def test_price_diagnostic_retains_rejected_timestamp(caplog, offset, reason):
    stamp = datetime.now(UTC)
    observation = ExternalPrice(symbol='BTC-USD', price=60000,
                               timestamp=stamp + timedelta(seconds=offset), received_at=stamp)
    async with httpx.AsyncClient() as client:
        feed = CryptoPriceFeed(load_settings(), client, client)
        with caplog.at_level(logging.INFO):
            assert feed._accept_price(observation) == (reason == 'accepted')
    record = caplog.records[-1]
    assert record.reason == reason
    payload = json.loads(JsonFormatter().format(record))
    assert payload['source_timestamp'] == observation.timestamp.isoformat()
    assert payload['received_at'] == stamp.isoformat()
    assert payload['max_age_seconds'] == 30
    assert reason in record.getMessage()
    assert observation.timestamp.isoformat() in record.getMessage()


@pytest.mark.parametrize('error,reason', [
    (TimeoutError(), 'timeout'), (httpx.ConnectError('test'), 'transport_error'),
    (ValueError('do not expose payload'), 'parse_or_validation_error'),
])
def test_failure_diagnostic_is_specific_without_payload(caplog, error, reason):
    CryptoPriceFeed._log_failure('BTC-USD', 'coinbase', error)
    assert caplog.records[-1].reason == reason
    assert 'do not expose payload' not in caplog.text


@pytest.mark.parametrize('age,reason,word', [(194, 'stale', '過期'), (-1, 'future_timestamp', '未來')])
def test_book_diagnostic_separates_age_and_receipt(tmp_path, caplog, age, reason, word):
    now = datetime.now(UTC)
    storage = Storage(tmp_path / 'test.sqlite3')
    storage.initialize()
    market = Market(market_id='test', question='Bitcoin', resolution_time=now + timedelta(days=1))
    book = OrderBook(timestamp=now - timedelta(seconds=age), received_at=now)
    with caplog.at_level(logging.INFO):
        _, features = FeatureEngine(load_settings(), storage).compute(
            market, book, book, None, None, now)
    records = [r for r in caplog.records if r.getMessage().startswith('Book timestamp')]
    assert len(records) == 2
    assert all(r.reason == reason and r.age_seconds == age for r in records)
    assert all(r.received_at == now.isoformat() for r in records)
    assert word in features.data_issues[0]
