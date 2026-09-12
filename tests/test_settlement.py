import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from polymarket_agent.config import load_settings
from polymarket_agent.data.public_http import PublicAPI
from polymarket_agent.settlement import BASE_URL, assess_evidence, collect_evidence, payload_hash


@pytest.fixture
def source():
    raw = json.loads((Path(__file__).parent / 'fixtures/us_markets.json').read_text())['events'][0]['markets'][0]
    raw.update(closed=True, active=False)
    return raw


def evidence(raw, payout):
    slug = raw['slug']
    now = datetime.now(UTC).isoformat()
    paths = [f'/v1/market/slug/{slug}', f'/v1/markets/{slug}/settlement']
    payloads = [{'market': raw}, {'slug': slug, 'settlement': payout}]
    return [{'url': BASE_URL + p, 'status': 'ok', 'requested_at': now, 'received_at': now,
             'payload': x, 'payload_sha256': payload_hash(x)} for p, x in zip(paths, payloads, strict=True)]


@pytest.mark.parametrize('value', [0, 1, '0.00', '1.00'])
def test_binary_settlement_remains_candidate_until_finality_verified(source, value):
    result = assess_evidence(source['slug'], evidence(source, value))
    assert result['candidate_payout'] is not None
    assert result['label'] is None and not result['eligible_for_supervised_training']
    assert result['reasons'] == ['finality_and_alternative_terms_not_verified']


@pytest.mark.parametrize('value', [True, {}, 'NaN', 'Infinity', -1, 2])
def test_invalid_values_are_not_labels(source, value):
    result = assess_evidence(source['slug'], evidence(source, value))
    assert result['candidate_payout'] is None
    assert result['reasons'] == ['invalid_settlement_value']


def test_missing_alternative_and_open_market(source):
    assert assess_evidence(source['slug'], evidence(source, None))['reasons'] == ['settlement_not_available']
    result = assess_evidence(source['slug'], evidence(source, 0.5))
    assert 'nonbinary_or_alternative_settlement' in result['reasons']
    source.update(closed=False, active=True)
    assert 'market_not_confirmed_closed' in assess_evidence(source['slug'], evidence(source, 1))['reasons']


def test_corruption_wrong_source_and_identity_are_rejected(source):
    original = evidence(source, 1)
    corrupt = copy.deepcopy(original)
    corrupt[1]['payload']['settlement'] = 0
    assert assess_evidence(source['slug'], corrupt)['reasons'] == ['payload_hash_mismatch']
    corrupt = copy.deepcopy(original)
    corrupt[1]['url'] = 'https://example.com/settlement'
    assert 'unexpected_source_url' in assess_evidence(source['slug'], corrupt)['reasons']
    corrupt = copy.deepcopy(original)
    corrupt[1]['payload']['slug'] = 'other'
    corrupt[1]['payload_sha256'] = payload_hash(corrupt[1]['payload'])
    assert assess_evidence(source['slug'], corrupt)['reasons'] == ['settlement_identity_mismatch']


@pytest.mark.asyncio
async def test_collector_get_only_and_access_denial_stops(source):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'GET'
        return httpx.Response(403)
    settings = load_settings()
    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        result = await collect_evidence(PublicAPI(settings, client), source['slug'])
    assert len(calls) == 1
    assert result['responses'][0]['http_status'] == 403
    assert result['assessment']['label'] is None


@pytest.mark.asyncio
async def test_collector_preserves_documented_payloads(source):
    def handler(request):
        assert request.method == 'GET'
        if request.url.path.endswith('/settlement'):
            return httpx.Response(200, json={'slug': source['slug'], 'settlement': 1})
        return httpx.Response(200, json={'market': source})
    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        result = await collect_evidence(PublicAPI(load_settings(), client), source['slug'])
        with pytest.raises(ValueError):
            await collect_evidence(PublicAPI(load_settings(), client), '../orders')
    assert len(result['responses']) == 2
    assert all(r['payload_sha256'] == payload_hash(r['payload']) for r in result['responses'])
    assert result['assessment']['candidate_payout'] == '1'
