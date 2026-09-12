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


def _reason_categories(reasons: list[str]) -> Counter[str]:
    categories: Counter[str] = Counter()
    for reason in reasons:
        if '時間過期' in reason:
            categories['book_stale'] += 1
        if '缺少雙邊報價' in reason or '買賣價交叉' in reason:
            categories['book_missing_or_crossed'] += 1
        if '外部價格缺失或過期' in reason:
            categories['external_missing_or_stale'] += 1
        if '雙邊掛單金額不足' in reason:
            categories['depth_below_limit'] += 1
        if reason.startswith('spread ') and '超過上限' in reason:
            categories['spread_over_limit'] += 1
        if '市場未開放' in reason or '市場已到期' in reason:
            categories['market_unavailable'] += 1
        if '尚未支援的合約規則' in reason:
            categories['unsupported_rules'] += 1
        if '行情監控' in reason and '尚未建立經驗證的勝率模型' in reason:
            categories['monitor_only'] += 1
    return categories


def audit_database(path: Path) -> dict[str, Any]:
    # Read-only URI never creates a missing database or runs schema migrations.
    uri = path.resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        schema = db.execute('PRAGMA user_version').fetchone()[0]
        if schema not in {2, 3}:
            raise ValueError(f'Audit requires schema v2 or v3; found {schema}')
        integrity = [r[0] for r in db.execute('PRAGMA integrity_check')]
        foreign_keys = len(db.execute('PRAGMA foreign_key_check').fetchall())
        tables = ['markets', 'market_snapshots', 'orderbook_snapshots',
                  'external_prices', 'features', 'market_metadata_snapshots', 'quote_history']
        counts = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in tables}
        by_market: dict[str, Any] = {}
        all_times: list[datetime] = []
        totals: Counter[str] = Counter()
        quality_totals: Counter[str] = Counter()
        decision_reason_totals: Counter[str] = Counter()
        feature_columns = {row['name'] for row in db.execute('PRAGMA table_info(features)')}
        has_decision_reasons = 'decision_reasons_json' in feature_columns
        for market in db.execute('SELECT market_id, question FROM markets ORDER BY market_id'):
            reason_select = ', decision_reasons_json' if has_decision_reasons else ''
            rows = list(db.execute(
                f'SELECT timestamp, status, features_json{reason_select} '
                'FROM features WHERE market_id=?', (market['market_id'],)))
            statuses: Counter[str] = Counter()
            issues: Counter[str] = Counter()
            decision_reasons: Counter[str] = Counter()
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
                if has_decision_reasons:
                    stored_reasons = json.loads(row['decision_reasons_json'])
                    if not isinstance(stored_reasons, list) or not all(
                        isinstance(reason, str) for reason in stored_reasons
                    ):
                        raise ValueError('decision_reasons_json must contain a list of strings')
                    decision_reasons.update(_reason_categories(stored_reasons))
            times.sort()
            all_times.extend(times)
            totals.update(statuses)
            quality_totals.update(issues)
            decision_reason_totals.update(decision_reasons)
            gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(times)]
            by_market[market['market_id']] = {
                'question': market['question'], 'observations': len(rows),
                'statuses': dict(statuses), 'quality_flags_overlapping': dict(issues),
                'decision_reason_categories_overlapping': dict(decision_reasons),
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
            'report_version': 2, 'schema_version': schema,
            'integrity': integrity, 'foreign_key_errors': foreign_keys, 'counts': counts,
            'window_start_utc': min(all_times).isoformat() if all_times else None,
            'window_end_utc': max(all_times).isoformat() if all_times else None,
            'statuses': dict(totals), 'markets': by_market,
            'quality_flags_overlapping': dict(quality_totals),
            'decision_reason_categories_overlapping': dict(decision_reason_totals),
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
             f"特徵筆數：{report['counts']['features']}；分類：{report['statuses']}",
             f"重疊品質旗標：{report.get('quality_flags_overlapping', {})}",
             f"重疊決策原因：{report.get('decision_reason_categories_overlapping', {})}", '',
             '| 市場 | 觀測筆數 | WATCH | NO_TRADE | 資料問題 | 委託簿過期 | 最長間隔（秒） |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for market in report['markets'].values():
        gap = market['observation_gap_seconds'].get('max')
        gap_text = f'{gap:.3f}' if gap is not None else '—'
        lines.append(f"| {cell(market['question'])} | {market['observations']} | "
                     f"{market['statuses'].get('WATCH', 0)} | "
                     f"{market['statuses'].get('NO_TRADE', 0)} | "
                     f"{market['quality_flags_overlapping'].get('has_data_issues', 0)} | "
                     f"{market['quality_flags_overlapping'].get('book_stale', 0)} | "
                     f"{gap_text} |")
    lines += ['', '原生委託簿狀態：' + json.dumps(report['native_book_states']), '',
              '有效外部價格筆數：' + json.dumps(report['external_counts']), '', '限制：', '']
    lines += ['- ' + x for x in report['limitations']]
    return '\n'.join(lines) + '\n'
