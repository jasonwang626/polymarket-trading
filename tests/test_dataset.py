import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from polymarket_agent.data.storage import Storage
from polymarket_agent.dataset import build_manifest, split_groups
from polymarket_agent.models import Market, MarketSnapshot

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def record(mid, event, day, deadline=None, **extra):
    return {'market_id': mid, 'event_id': event, 'observation_id': day,
            'as_of': (BASE + timedelta(days=day)).isoformat(),
            'resolution_time': (BASE + timedelta(days=deadline or day + 1)).isoformat(), **extra}


def split(rows):
    return split_groups(rows, BASE + timedelta(days=10), BASE + timedelta(days=20), 86400)


def test_event_thresholds_crossing_boundary_are_purged_together():
    summary, rows = split([record('btc100', 'same-event', 1),
                           record('btc150', 'same-event', 12)])
    assert len(summary['groups']) == 1
    assert {r['split'] for r in rows} == {'excluded'}


def test_market_identity_bridges_event_renames():
    summary, rows = split([record('one', 'old-event', 1), record('one', 'new-event', 2),
                           record('two', 'new-event', 3)])
    assert len(summary['groups']) == 1
    assert len({r['group_id'] for r in rows}) == 1


def test_partitions_embargo_and_label_horizon():
    summary, rows = split([record('train', 'a', 1), record('validation', 'b', 12),
                           record('test', 'c', 22), record('gap', 'd', 10),
                           record('late', 'e', 2, deadline=12)])
    mapping = {r['market_id']: r for r in rows}
    assert mapping['train']['split'] == 'train'
    assert mapping['validation']['split'] == 'validation'
    assert mapping['test']['split'] == 'test'
    assert mapping['gap']['split'] == 'excluded'
    assert mapping['late']['split_reason'] == 'outcome_horizon_crosses_train_end'
    assert not summary['ready_for_training']
    assert all(r['label'] is None and not r['eligible_for_supervised_training'] for r in rows)


def test_missing_identity_quarantines_connected_group():
    _, rows = split([record('one', None, 1), record('one', 'event', 2),
                    record('two', 'event', 3)])
    assert all(r['split_reason'] == 'missing_event_identity' for r in rows)


def test_unverified_supplied_labels_cannot_enable_training():
    _, rows = split([record('one', 'event', 1, label=1, label_status='verified',
                           eligible_for_supervised_training=True)])
    assert rows[0]['label'] is None
    assert rows[0]['label_status'] == 'unverified'


def test_invalid_boundaries_fail():
    with pytest.raises(ValueError):
        split_groups([], BASE, BASE)
    with pytest.raises(ValueError):
        split_groups([], BASE.replace(tzinfo=None), BASE + timedelta(days=1))
    with pytest.raises(ValueError):
        split_groups([], BASE, BASE + timedelta(days=1), -1)


def test_manifest_uses_as_of_metadata_without_changing_source(tmp_path):
    path = tmp_path / 'capture.sqlite3'
    db = Storage(path)
    db.initialize()
    market = Market(market_id='one', question='BTC', resolution_time=BASE + timedelta(days=2),
                    raw={'event': {'slug': 'original'}})
    db.upsert_market(market, BASE)
    db.insert_market_snapshot(MarketSnapshot(market_id='one', timestamp=BASE + timedelta(days=1)))
    market.raw = {'event': {'slug': 'future-event'}}
    db.upsert_market(market, BASE + timedelta(days=3))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    summary, rows = build_manifest(path, BASE + timedelta(days=10), BASE + timedelta(days=20))
    assert rows[0]['event_id'] == 'polymarket_international:original'
    assert rows[0]['split'] == 'train'
    assert summary['verified_labels'] == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
