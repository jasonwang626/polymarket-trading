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
    storage.insert_features(feature, 'NO_TRADE', 0, feature.data_issues)
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
    assert report['report_version'] == 2
    assert report['quality_flags_overlapping']['deferred_without_read'] == 1
    assert report['markets']['m']['decision_reason_categories_overlapping'] == {}
    assert 'BTC \\| test' in markdown_report(report)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_empty_capture_is_not_reported_as_trading_validation(tmp_path):
    path = tmp_path / 'empty.sqlite3'
    Storage(path).initialize()
    report = audit_database(path)
    assert report['window_start_utc'] is None
    assert report['statuses'] == {}
    assert any('損益' in x for x in report['limitations'])


def test_schema_v3_audit_aggregates_persisted_decision_reasons(tmp_path):
    path = tmp_path / 'reasons.sqlite3'
    storage = Storage(path)
    storage.initialize()
    now = datetime.now(UTC)
    market = Market(market_id='m', question='BTC depth',
                    resolution_time=now + timedelta(days=1))
    storage.upsert_market(market, now)
    feature = FeatureSnapshot(market_id='m', timestamp=now, seconds_to_expiry=86400,
                              minutes_to_expiry=1440)
    reason = '最優價附近的雙邊掛單金額不足（買方 $1.00；賣方 $2.00；每側門檻 $100.00）'
    storage.insert_features(feature, 'NO_TRADE', 0, [reason])

    report = audit_database(path)
    assert report['schema_version'] == 3
    assert report['decision_reason_categories_overlapping'] == {'depth_below_limit': 1}
    assert report['markets']['m']['decision_reason_categories_overlapping'] == {
        'depth_below_limit': 1
    }
    assert 'depth_below_limit' in markdown_report(report)


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
