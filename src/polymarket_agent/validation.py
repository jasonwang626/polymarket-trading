"""Offline quality audit of a captured scanner database; no network or orders."""
from __future__ import annotations

import itertools
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any


def _time(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError('Audit timestamps must include timezone')
    return stamp.astimezone(UTC)


def _summary(values: list[float]) -> dict[str, Any]:
    return {'count': len(values), 'min': min(values), 'median': median(values),
            'max': max(values)} if values else {'count': 0}


def audit_database(path: Path) -> dict[str, Any]:
    # Read-only URI never creates a missing database or runs schema migrations.
    uri = path.resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        schema = db.execute('PRAGMA user_version').fetchone()[0]
        if schema != 2:
            raise ValueError(f'Audit requires schema v2; found {schema}')
        integrity = [r[0] for r in db.execute('PRAGMA integrity_check')]
        foreign_keys = len(db.execute('PRAGMA foreign_key_check').fetchall())
        tables = ['markets', 'market_snapshots', 'orderbook_snapshots',
                  'external_prices', 'features', 'market_metadata_snapshots', 'quote_history']
        counts = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in tables}
        by_market: dict[str, Any] = {}
        all_times: list[datetime] = []
        totals: Counter[str] = Counter()
        for market in db.execute('SELECT market_id, question FROM markets ORDER BY market_id'):
            rows = list(db.execute(
                'SELECT timestamp, status, features_json FROM features WHERE market_id=?',
                (market['market_id'],)))
            statuses: Counter[str] = Counter()
            issues: Counter[str] = Counter()
            times = []
            for row in rows:
                feature = json.loads(row['features_json'])
                times.append(_time(row['timestamp']))
                statuses[row['status']] += 1
                labels = feature.get('data_issues', [])
                if labels:
                    issues['has_data_issues'] += 1
                if any('本輪未重新讀取' in x for x in labels):
                    issues['deferred_without_read'] += 1
                elif feature.get('external_price_stale', True):
                    issues['external_missing_or_stale'] += 1
                if any('時間過期' in x for x in labels):
                    issues['book_stale'] += 1
                if any('未來' in x for x in labels):
                    issues['book_future_or_legacy_combined_warning'] += 1
                if any('暫停' in x or '未開放' in x or '關閉' in x or '狀態未知' in x
                       for x in labels):
                    issues['not_open_or_last_known_halted'] += 1
            times.sort()
            all_times.extend(times)
            totals.update(statuses)
            gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(times)]
            by_market[market['market_id']] = {
                'question': market['question'], 'observations': len(rows),
                'statuses': dict(statuses), 'quality_flags_overlapping': dict(issues),
                'observation_gap_seconds': _summary(gaps),
            }
        book_states: Counter[str] = Counter()
        book_ages = []
        missing_book_receipt = 0
        for row in db.execute('SELECT timestamp, received_at, raw_json FROM orderbook_snapshots '
                              'WHERE synthetic=0'):
            raw = json.loads(row['raw_json'])
            book_states[str(raw.get('marketData', {}).get('state', 'UNKNOWN'))] += 1
            if row['received_at']:
                book_ages.append((_time(row['received_at']) - _time(row['timestamp'])).total_seconds())
            else:
                missing_book_receipt += 1
        external: dict[str, list[float]] = defaultdict(list)
        external_counts: Counter[str] = Counter()
        missing_external_receipt = 0
        for row in db.execute('SELECT * FROM external_prices'):
            key = row['symbol'] + '/' + row['venue']
            external_counts[key] += 1
            if row['received_at']:
                external[key].append((_time(row['received_at']) - _time(row['timestamp'])).total_seconds())
            else:
                missing_external_receipt += 1
        return {
            'report_version': 1, 'schema_version': schema,
            'integrity': integrity, 'foreign_key_errors': foreign_keys, 'counts': counts,
            'window_start_utc': min(all_times).isoformat() if all_times else None,
            'window_end_utc': max(all_times).isoformat() if all_times else None,
            'statuses': dict(totals), 'markets': by_market,
            'native_book_states': dict(book_states),
            'native_book_age_at_receipt_seconds': _summary(book_ages),
            'missing_book_receipt_times': missing_book_receipt,
            'external_counts': dict(external_counts),
            'external_age_at_receipt_seconds': {k: _summary(v) for k, v in external.items()},
            'missing_external_receipt_times': missing_external_receipt,
            'limitations': [
                '資料庫中的有效外部價格不包含被拒絕的來源回應；拒絕原因需搭配日誌。',
                '觀測間隔不是網路延遲；資料庫不能證明預期輪數或 HTTP 成功率。',
                '品質旗標可能重疊；暫停等待的特徵不是新行情快照。',
                '這是已保存資料的品質稽核，不是策略重播、成交率或損益回測。',
            ],
        }


def markdown_report(report: dict[str, Any]) -> str:
    def cell(value: str) -> str:
        return value.replace('|', '\\|').replace('\n', ' ')

    lines = ['# 唯讀掃描資料驗證報告', '',
             f"觀測期間：{report['window_start_utc']} 至 {report['window_end_utc']}", '',
             f"完整性：{', '.join(report['integrity'])}；外鍵錯誤：{report['foreign_key_errors']}",
             f"特徵筆數：{report['counts']['features']}；分類：{report['statuses']}", '',
             '| 市場 | 觀測筆數 | WATCH | NO_TRADE | 最長觀測間隔（秒） |',
             '|---|---:|---:|---:|---:|']
    for market in report['markets'].values():
        gap = market['observation_gap_seconds'].get('max')
        gap_text = f'{gap:.3f}' if gap is not None else '—'
        lines.append(f"| {cell(market['question'])} | {market['observations']} | "
                     f"{market['statuses'].get('WATCH', 0)} | "
                     f"{market['statuses'].get('NO_TRADE', 0)} | {gap_text} |")
    lines += ['', '原生委託簿狀態：' + json.dumps(report['native_book_states']), '',
              '有效外部價格筆數：' + json.dumps(report['external_counts']), '', '限制：', '']
    lines += ['- ' + x for x in report['limitations']]
    return '\n'.join(lines) + '\n'
