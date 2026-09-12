"""Collect public settlement evidence; candidate payouts are not training labels."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from polymarket_agent.data.public_http import PublicAPI
from polymarket_agent.discovery.polymarket_us import parse_us_market
from polymarket_agent.validation import _time

BASE_URL = 'https://gateway.polymarket.us'


def payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def assess_evidence(slug: str, responses: list[dict]) -> dict:
    result = {'market_id': f'polymarket_us:{slug}', 'candidate_payout': None,
              'label': None, 'label_status': 'unverified',
              'eligible_for_supervised_training': False, 'reasons': []}
    expected = [f'/v1/market/slug/{slug}', f'/v1/markets/{slug}/settlement']
    if len(responses) != 2:
        result['reasons'].append('incomplete_evidence')
        return result
    for response, path in zip(responses, expected, strict=True):
        if response.get('url') != BASE_URL + path:
            result['reasons'].append('unexpected_source_url')
        if response.get('status') != 'ok':
            result['reasons'].append('source_request_failed')
            continue
        try:
            if not isinstance(response.get('payload'), dict):
                raise TypeError('Evidence payload must be an object')
            if payload_hash(response['payload']) != response.get('payload_sha256'):
                result['reasons'].append('payload_hash_mismatch')
            if _time(response['received_at']) < _time(response['requested_at']):
                result['reasons'].append('clock_moved_backwards')
        except (KeyError, TypeError, ValueError):
            result['reasons'].append('invalid_evidence_envelope')
    if result['reasons']:
        return result
    raw = responses[0]['payload'].get('market', responses[0]['payload'])
    if not isinstance(raw, dict) or raw.get('slug') != slug:
        result['reasons'].append('market_identity_mismatch')
        return result
    market = parse_us_market(raw)
    if market is None:
        result['reasons'].append('unsupported_market_rules_or_identity')
        return result
    result['rule_hash'] = market.rule_hash
    result['resolution_time'] = market.resolution_time.isoformat()
    result['evidence_available_at'] = max(_time(r['received_at']) for r in responses).isoformat()
    payload = responses[1]['payload']
    if payload.get('slug') != slug:
        result['reasons'].append('settlement_identity_mismatch')
        return result
    value = payload.get('settlement')
    if value is None:
        result['reasons'].append('settlement_not_available')
        return result
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise TypeError('Invalid payout type')
        payout = Decimal(str(value))
        if not payout.is_finite() or payout < 0 or payout > 1:
            raise ValueError('Invalid payout range')
    except (InvalidOperation, ValueError, TypeError):
        result['reasons'].append('invalid_settlement_value')
        return result
    result['candidate_payout'] = str(payout)
    if payout not in (Decimal(0), Decimal(1)):
        result['reasons'].append('nonbinary_or_alternative_settlement')
    if raw.get('closed') is not True or raw.get('active') is not False:
        result['reasons'].append('market_not_confirmed_closed')
    if market.rule_issues:
        result['reasons'].append('market_rule_issues')
    # The documented response contains only slug/settlement. Until a resolved
    # BTC response and finality/alternative terms are validated, do not promote
    # even a plausible binary payout into a supervised label.
    result['reasons'].append('finality_and_alternative_terms_not_verified')
    return result


async def collect_evidence(api: PublicAPI, slug: str) -> dict:
    if not re.fullmatch(r'cpc-btc-[a-zA-Z0-9_-]+', slug):
        raise ValueError('Only Polymarket US BTC slugs are supported')
    responses = []
    for path in [f'/v1/market/slug/{slug}', f'/v1/markets/{slug}/settlement']:
        response = {'url': BASE_URL + path, 'requested_at': datetime.now(UTC).isoformat()}
        try:
            payload = await api.get(path)
            digest = payload_hash(payload)
            response.update(status='ok', payload=payload, payload_sha256=digest)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            response.update(status='unavailable', error_type=type(exc).__name__)
            if isinstance(exc, httpx.HTTPStatusError):
                response['http_status'] = exc.response.status_code
        response['received_at'] = datetime.now(UTC).isoformat()
        responses.append(response)
        if response['status'] != 'ok':
            break
    return {'evidence_version': 1, 'slug': slug, 'responses': responses,
            'assessment': assess_evidence(slug, responses),
            'integrity_note': '雜湊僅用於偵測保存後內容變更，不是官方簽章或真實性證明。'}
