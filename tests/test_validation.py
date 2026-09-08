import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from polymarket_agent.data.storage import Storage
from polymarket_agent.models import ExternalPrice, FeatureSnapshot, Market
from polymarket_agent.validation import audit_database, markdown_report


def test_audit_preserves_database_and_separates_deferred_observations(tmp_path):
    path = tmp_path / 'capture.sqlite3'
    storage = Storage(path)
    storage.initialize()
    now = datetime.now(UTC)
    market = Market(market_id='m', question='BTC | test', resolution_time=now + timedelta(days=1))
    storage.upsert_market(market, now)
    feature = FeatureSnapshot(market_id='m', timestamp=now, seconds_to_expiry=86400,
                              minutes_to_expiry=1440,
                              data_issues=['上次確認市場暫停；本輪未重新讀取'])
    storage.insert_features(feature, 'NO_TRADE', 0)
    storage.insert_external_price(ExternalPrice(symbol='BTC-USD', timestamp=now,
                                               received_at=now, price=60000))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = audit_database(path)
    assert report['integrity'] == ['ok']
    assert report['counts']['orderbook_snapshots'] == 0
    flags = report['markets']['m']['quality_flags_overlapping']
    assert flags['deferred_without_read'] == 1
    assert 'external_missing_or_stale' not in flags
    assert report['external_counts'] == {'BTC-USD/coinbase': 1}
    assert 'BTC \\| test' in markdown_report(report)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_empty_capture_is_not_reported_as_trading_validation(tmp_path):
    path = tmp_path / 'empty.sqlite3'
    Storage(path).initialize()
    report = audit_database(path)
    assert report['window_start_utc'] is None
    assert report['statuses'] == {}
    assert any('損益' in x for x in report['limitations'])


def test_missing_database_is_never_created(tmp_path):
    import sqlite3

    path = tmp_path / 'missing.sqlite3'
    with pytest.raises(sqlite3.OperationalError):
        audit_database(path)
    assert not path.exists()


def test_unknown_schema_fails_without_migration(tmp_path):
    path = tmp_path / 'future.sqlite3'
    storage = Storage(path)
    storage.initialize()
    with storage.connect() as db:
        db.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError, match='schema v2'):
        audit_database(path)
