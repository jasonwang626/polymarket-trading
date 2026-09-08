"""Causal replay of captured US observations, not orders, fills, or P&L."""
from __future__ import annotations

import json
import sqlite3
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from polymarket_agent.config import Settings
from polymarket_agent.data.polymarket_us import parse_book
from polymarket_agent.features.market_features import FeatureEngine
from polymarket_agent.models import ExternalPrice, Market
from polymarket_agent.scanner.scanner import classify
from polymarket_agent.validation import _time


class Timeline:
    def __init__(self, rows: list[dict], field: str):
        self.rows = sorted(rows, key=lambda r: (_time(r[field]), r.get('snapshot_id', 0)))
        self.times = [_time(r[field]) for r in self.rows]

    def latest(self, as_of: datetime) -> dict | None:
        index = bisect_right(self.times, as_of) - 1
        return self.rows[index] if index >= 0 else None


class ReplayHistory:
    def __init__(self):
        self.mids: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
        self.external: list[ExternalPrice] = []

    def market_mid_at_or_before(self, market_id, timestamp, max_age_seconds=60):
        cutoff = timestamp - timedelta(seconds=max_age_seconds)
        matches = [(time, price) for time, price in self.mids[market_id]
                   if cutoff <= time <= timestamp]
        return max(matches, key=lambda x: x[0])[1] if matches else None

    def external_price_at_or_before(self, symbol, timestamp, max_age_seconds=60,
                                    venue=None, available_at=None):
        cutoff = timestamp - timedelta(seconds=max_age_seconds)
        matches = [p for p in self.external if p.symbol == symbol
                   and (venue is None or venue == p.venue)
                   and cutoff <= p.timestamp <= timestamp
                   and p.received_at <= (available_at or timestamp)]
        return max(matches, key=lambda p: p.timestamp).price if matches else None


def replay_capture(path: Path, settings: Settings) -> tuple[dict[str, Any], list[dict]]:
    """Use actual saved observation times; do not infer missing polling cycles."""
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if db.execute('PRAGMA user_version').fetchone()[0] != 2:
            raise ValueError('Replay requires schema v2')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Source database integrity failed')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Source database foreign keys failed')
        def read(table):
            return [dict(r) for r in db.execute(f'SELECT * FROM {table}')]
        schedule = read('market_snapshots')
        metadata = read('market_metadata_snapshots')
        books = [r for r in read('orderbook_snapshots') if not r['synthetic']]
        external = read('external_prices')
        feature_count = db.execute('SELECT count(*) FROM features').fetchone()[0]
    meta_groups: dict[str, list] = defaultdict(list)
    book_groups: dict[str, list] = defaultdict(list)
    omitted = Counter()
    for row in metadata:
        if row['observed_at'] is None:
            omitted['metadata_missing_observed_at'] += 1
        else:
            meta_groups[row['market_id']].append(row)
    for row in books:
        if row['received_at'] is None:
            omitted['book_missing_received_at'] += 1
        else:
            book_groups[row['market_id']].append(row)
    meta_index = {key: Timeline(rows, 'observed_at') for key, rows in meta_groups.items()}
    book_index = {key: Timeline(rows, 'received_at') for key, rows in book_groups.items()}
    history = ReplayHistory()
    for row in external:
        if row['received_at'] is None:
            omitted['external_missing_received_at'] += 1
            continue
        history.external.append(ExternalPrice(
            symbol=row['symbol'], venue=row['venue'], timestamp=_time(row['timestamp']),
            received_at=_time(row['received_at']), price=row['price'],
            volume_24h=row['volume_24h'],
        ))
    engine = FeatureEngine(settings, history)
    output = []
    statuses = Counter()
    transitions = []
    previous = {}
    previous_time = {}
    gaps = []
    for observation in sorted(schedule, key=lambda r: (_time(r['timestamp']), r['snapshot_id'])):
        now = _time(observation['timestamp'])
        mid = observation['market_id']
        event = {'observation_id': observation['snapshot_id'], 'market_id': mid,
                 'as_of': now.isoformat(), 'status': 'UNDETERMINED'}
        meta = meta_index[mid].latest(now) if mid in meta_index else None
        book = book_index[mid].latest(now) if mid in book_index else None
        if mid in previous_time:
            gaps.append((now - previous_time[mid]).total_seconds())
        previous_time[mid] = now
        if meta is None or book is None:
            event['reasons'] = ['No metadata or book received by observation time']
            statuses['UNDETERMINED'] += 1
            output.append(event)
            continue
        market = Market.model_validate_json(meta['normalized_json'])
        if market.venue != 'polymarket_us':
            raise ValueError('Replay currently supports Polymarket US captures only')
        yes, no = parse_book(json.loads(book['raw_json']), market)
        # Parsing defaults receipt to the current clock. Restore the captured
        # receipt on BOTH sides before historical computation.
        yes.received_at = no.received_at = _time(book['received_at'])
        candidates = [p for p in history.external if p.symbol == market.underlying
                      and p.received_at <= now and p.timestamp <= now]
        price = max(candidates, key=lambda p: (p.received_at, p.timestamp)) if candidates else None
        snapshot, features = engine.compute(market, yes, no, None, price, now)
        status, reasons = classify(features, market, settings)
        if snapshot.yes_mid is not None:
            history.mids[mid].append((now, snapshot.yes_mid))
        state = (yes.state, market.rule_hash)
        if previous.get(mid) != state:
            transitions.append({'market_id': mid, 'as_of': now.isoformat(),
                                'previous_state': previous.get(mid, (None, None))[0],
                                'previous_rule_hash': previous.get(mid, (None, None))[1],
                                'state': yes.state, 'rule_hash': market.rule_hash})
            previous[mid] = state
        event.update(status=status.value, reasons=reasons, market_state=yes.state,
                     metadata_snapshot_id=meta['snapshot_id'],
                     book_snapshot_id=book['snapshot_id'],
                     book_source_time=yes.timestamp.isoformat(),
                     book_received_at=yes.received_at.isoformat(),
                     external_source_time=price.timestamp.isoformat() if price else None,
                     external_received_at=price.received_at.isoformat() if price else None,
                     external_venue=price.venue if price else None,
                     features=features.model_dump(mode='json'))
        statuses[status.value] += 1
        output.append(event)
    report = {
        'replay_version': 1, 'policy': 'latest_received_price_at_recorded_observation',
        'schedule': 'market_snapshots; polling deferrals have no observation and are excluded',
        'observations': len(output), 'stored_decision_features': feature_count,
        'statuses': dict(statuses), 'omitted_input_rows': dict(omitted),
        'max_within_market_observation_gap_seconds': max(gaps) if gaps else None,
        'state_or_rule_events': transitions,
        'settings': settings.model_dump(mode='json', exclude={'storage', 'project_root', 'app'}),
        'limitations': [
            '重播當時已收到的最新有效來源紀錄，可沿用既有價格直到過期；與原 scanner 每輪只用新請求的政策不同。',
            '來源僅含當時有保存的價格，不重建被拒絕的請求，也不宣稱精確重現原始決策。',
            '排程來自實際行情快照；暫停等待、未觀測期間及市場清單外資料不會被補造。',
            '使用當前明列設定及分類程式；不使用來源資料庫的已算特徵、分類或事後最新市場規則。',
            '狀態轉換是觀測事件，不是平倉或成交；無 BRTI 標籤、勝率、費用、交易或損益。',
        ],
    }
    return report, output
