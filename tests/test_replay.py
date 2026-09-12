import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarket_agent.config import load_settings
from polymarket_agent.data.storage import Storage
from polymarket_agent.models import ExternalPrice, Market, MarketSnapshot
from polymarket_agent.offline import OfflineDiscovery, OfflineMarketFeed, OfflinePriceFeed
from polymarket_agent.replay import ReplayHistory, Timeline, replay_capture
from polymarket_agent.scanner.scanner import Scanner


@pytest.fixture
async def capture(tmp_path):
    db = Storage(tmp_path / 'capture.sqlite3')
    db.initialize()
    fixtures = Path(__file__).parent / 'fixtures'
    scanner = Scanner(load_settings(), db, OfflineDiscovery(fixtures),
                      OfflineMarketFeed(fixtures), OfflinePriceFeed())
    await scanner.scan_once()
    await scanner.close()
    return db


@pytest.mark.asyncio
async def test_replay_is_read_only_and_ignores_saved_feature_predictions(capture):
    db = capture
    with db.connect() as connection:
        connection.execute("UPDATE features SET status='BOGUS', features_json='{}'")
    path = db.path
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report, events = replay_capture(path, load_settings())
    assert report['statuses'] == {'WATCH': 1}
    assert events[0]['features']['fair_probability'] is None
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert replay_capture(path, load_settings()) == (report, events)


@pytest.mark.asyncio
async def test_future_metadata_books_and_late_prices_do_not_change_past(capture):
    db = capture
    _, baseline = replay_capture(db.path, load_settings())
    as_of = datetime.fromisoformat(baseline[0]['as_of'])
    with db.connect() as connection:
        meta = connection.execute('SELECT normalized_json FROM market_metadata_snapshots').fetchone()
        row = dict(connection.execute('SELECT * FROM orderbook_snapshots WHERE synthetic=0').fetchone())
    market = Market.model_validate_json(meta[0])
    market.closed = True
    market.rule_hash = 'future-rule'
    db.upsert_market(market, as_of + timedelta(hours=1))
    # Old source timestamp does not make a late-arriving price available earlier.
    db.insert_external_price(ExternalPrice(symbol='BTC-USD', timestamp=as_of - timedelta(seconds=1),
                                           received_at=as_of + timedelta(seconds=1), price=1))
    db.insert_external_price(ExternalPrice(symbol='BTC-USD',
                                           timestamp=as_of + timedelta(seconds=1),
                                           received_at=as_of - timedelta(microseconds=1), price=2))
    with db.connect() as connection:
        row.pop('snapshot_id')
        row['received_at'] = (as_of + timedelta(seconds=1)).isoformat()
        raw = json.loads(row['raw_json'])
        raw['marketData']['state'] = 'MARKET_STATE_HALTED'
        row['raw_json'] = json.dumps(raw)
        columns = ','.join(row)
        connection.execute(f"INSERT INTO orderbook_snapshots ({columns}) VALUES "
                           f"({','.join('?' for _ in row)})", list(row.values()))
    _, replayed = replay_capture(db.path, load_settings())
    assert replayed == baseline


@pytest.mark.asyncio
async def test_halted_to_open_event_and_missing_early_observation(capture):
    db = capture
    with db.connect() as connection:
        row = dict(connection.execute('SELECT * FROM orderbook_snapshots WHERE synthetic=0').fetchone())
        first = dict(connection.execute('SELECT * FROM market_snapshots').fetchone())
        raw = json.loads(row['raw_json'])
        halted = copy.deepcopy(raw)
        halted['marketData']['state'] = 'MARKET_STATE_HALTED'
        connection.execute('UPDATE orderbook_snapshots SET raw_json=? WHERE synthetic=0',
                           (json.dumps(halted),))
    now = datetime.fromisoformat(first['timestamp'])
    later = now + timedelta(seconds=60)
    raw['marketData']['transactTime'] = later.isoformat()
    row.pop('snapshot_id')
    row.update(timestamp=later.isoformat(), received_at=later.isoformat(), raw_json=json.dumps(raw))
    with db.connect() as connection:
        connection.execute(f"INSERT INTO orderbook_snapshots ({','.join(row)}) VALUES "
                           f"({','.join('?' for _ in row)})", list(row.values()))
    db.insert_market_snapshot(MarketSnapshot(market_id=first['market_id'], timestamp=later))
    db.insert_market_snapshot(MarketSnapshot(market_id=first['market_id'],
                                             timestamp=now - timedelta(hours=1)))
    report, events = replay_capture(db.path, load_settings())
    assert events[0]['status'] == 'UNDETERMINED'
    assert [e['state'] for e in report['state_or_rule_events']] == ['HALTED', 'OPEN']
    assert events[1]['status'] == 'NO_TRADE'
    assert events[2]['features']['external_price_stale'] is True


def test_timeline_and_history_enforce_availability_boundaries():
    now = datetime.now(UTC)
    timeline = Timeline([{'observed_at': now.isoformat(), 'snapshot_id': 1}], 'observed_at')
    assert timeline.latest(now - timedelta(microseconds=1)) is None
    assert timeline.latest(now)['snapshot_id'] == 1
    history = ReplayHistory()
    history.external.append(ExternalPrice(symbol='BTC-USD', timestamp=now,
                                           received_at=now + timedelta(seconds=5), price=1))
    assert history.external_price_at_or_before('BTC-USD', now, available_at=now) is None
    assert history.external_price_at_or_before(
        'BTC-USD', now, available_at=now + timedelta(seconds=5)) == 1
