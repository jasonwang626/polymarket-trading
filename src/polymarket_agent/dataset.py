"""Event-grouped chronological manifests; unverified outcomes never become labels."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from polymarket_agent.models import Market
from polymarket_agent.replay import Timeline
from polymarket_agent.validation import _time


def split_groups(records: list[dict], train_end: datetime, validation_end: datetime,
                 embargo_seconds: int = 86400) -> tuple[dict, list[dict]]:
    if train_end.tzinfo is None or validation_end.tzinfo is None:
        raise ValueError('Split boundaries must include timezone')
    if train_end >= validation_end or embargo_seconds < 0:
        raise ValueError('Ordered split boundaries and nonnegative embargo are required')
    parents: dict[str, str] = {}

    def root(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    def union(a, b):
        a, b = root(a), root(b)
        parents[max(a, b)] = min(a, b)

    unknown_markets = {r['market_id'] for r in records if not r.get('event_id')}
    for row in records:
        if row.get('event_id'):
            union('market:' + row['market_id'], 'event:' + row['event_id'])
    groups: dict[str, list] = defaultdict(list)
    for row in records:
        groups[root('market:' + row['market_id'])].append(row)
    manifest = []
    summaries = []
    embargo = timedelta(seconds=embargo_seconds)
    for members in groups.values():
        event_ids = sorted({r['event_id'] for r in members if r.get('event_id')})
        market_ids = sorted({r['market_id'] for r in members})
        group_id = hashlib.sha256(json.dumps([event_ids, market_ids]).encode()).hexdigest()[:20]
        first = min(_time(r['as_of']) for r in members)
        last = max(_time(r['as_of']) for r in members)
        deadlines = [_time(r['resolution_time']) for r in members if r.get('resolution_time')]
        horizon = max(deadlines) if len(deadlines) == len(members) else None
        split, reason = 'excluded', 'crosses_boundary_or_embargo'
        if any(mid in unknown_markets for mid in market_ids):
            reason = 'missing_event_identity'
        elif horizon is None:
            reason = 'missing_resolution_horizon'
        elif last < train_end and horizon < train_end:
            split, reason = 'train', 'entire_group_before_train_end'
        elif first >= train_end + embargo and last < validation_end and horizon < validation_end:
            split, reason = 'validation', 'entire_group_in_validation_window'
        elif first >= validation_end + embargo:
            split, reason = 'test', 'entire_group_after_validation_embargo'
        elif last < train_end and horizon >= train_end:
            reason = 'outcome_horizon_crosses_train_end'
        elif first >= train_end + embargo and last < validation_end and horizon >= validation_end:
            reason = 'outcome_horizon_crosses_validation_end'
        summaries.append({'group_id': group_id, 'event_ids': event_ids, 'market_ids': market_ids,
                          'observations': len(members), 'first_observed': first.isoformat(),
                          'last_observed': last.isoformat(),
                          'max_resolution_time': horizon.isoformat() if horizon else None,
                          'split': split, 'reason': reason})
        for row in members:
            manifest.append({**row, 'group_id': group_id, 'split': split, 'split_reason': reason,
                             'label': None, 'label_status': 'unverified',
                             'eligible_for_supervised_training': False})
    manifest.sort(key=lambda r: (_time(r['as_of']), r['market_id'], r['observation_id']))
    summaries.sort(key=lambda r: r['group_id'])
    summary = {
        'manifest_version': 1, 'train_end_exclusive': train_end.isoformat(),
        'validation_end_exclusive': validation_end.isoformat(), 'embargo_seconds': embargo_seconds,
        'observations': len(manifest), 'groups': summaries,
        'split_counts': dict(Counter(r['split'] for r in manifest)),
        'verified_labels': 0, 'ready_for_training': False,
        'limitations': [
            '只建立資料切分清單，不訓練模型、不生成勝率或交易。',
            '沒有驗證過的 BRTI 或正式結算標籤；closed、HALTED、最後價格均不當作結果標籤。',
            '同一事件及同一市場的事件身分變更採傳遞合併，不跨資料集；缺少事件身分的群組隔離。',
            '訓練／驗證同時限制觀測時間與最大結果觀察截止，並保留邊界後 embargo；跨界群組整組排除。',
            '不同事件仍可能共享 BTC 風險與重疊觀察期；事件分組不保證統計獨立。',
            '邊界須預先指定；不能依後續模型表現反覆調整以挑選有利結果。',
        ],
    }
    return summary, manifest


def build_manifest(path: Path, train_end: datetime, validation_end: datetime,
                   embargo_seconds: int = 86400) -> tuple[dict, list[dict]]:
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if db.execute('PRAGMA user_version').fetchone()[0] not in {2, 3}:
            raise ValueError('Dataset manifest requires schema v2 or v3')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Source database integrity failed')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Source database foreign keys failed')
        meta: dict[str, list] = defaultdict(list)
        for row in db.execute('SELECT * FROM market_metadata_snapshots'):
            if row['observed_at']:
                meta[row['market_id']].append(dict(row))
        indexes = {key: Timeline(rows, 'observed_at') for key, rows in meta.items()}
        records = []
        for row in db.execute('SELECT * FROM market_snapshots'):
            as_of = _time(row['timestamp'])
            selected = indexes[row['market_id']].latest(as_of) if row['market_id'] in indexes else None
            model = Market.model_validate_json(selected['normalized_json']) if selected else None
            raw = json.loads(selected['raw_json']) if selected else {}
            event_slug = (raw.get('event') or {}).get('slug')
            event_id = f'{model.venue}:{event_slug}' if model and event_slug else None
            records.append({
                'observation_id': row['snapshot_id'], 'market_id': row['market_id'],
                'as_of': as_of.isoformat(), 'event_id': event_id,
                'metadata_snapshot_id': selected['snapshot_id'] if selected else None,
                'rule_hash': model.rule_hash if model else None,
                'resolution_time': model.resolution_time.isoformat() if model else None,
            })
    return split_groups(records, train_end, validation_end, embargo_seconds)
