import asyncio
import copy
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from polymarket_agent.config import load_settings
from polymarket_agent.data.polymarket_us import PolymarketUSFeed, parse_book
from polymarket_agent.data.public_http import PublicAPI
from polymarket_agent.data.storage import Storage
from polymarket_agent.discovery.polymarket_us import (
    PolymarketUSDiscovery,
    parse_rules,
    parse_us_market,
)
from polymarket_agent.features.market_features import FeatureEngine, rank_score
from polymarket_agent.models import (
    ExternalPrice,
    MarketSnapshot,
    QuoteHistoryPoint,
    ScanStatus,
    Trade,
)
from polymarket_agent.scanner.scanner import Scanner, classify

NOW = datetime(2026, 9, 8, tzinfo=UTC)


@pytest.fixture
def rows():
    return json.loads((Path(__file__).parent / "fixtures/us_markets.json").read_text())


@pytest.fixture
def market(rows):
    return parse_us_market(rows["events"][1]["markets"][0])


@pytest.fixture
def store(tmp_path):
    db = Storage(tmp_path / "us.sqlite3")
    db.initialize()
    return db


def book_payload(market, now=NOW):
    return {
        "marketData": {
            "marketSlug": market.slug,
            "state": "MARKET_STATE_OPEN",
            "transactTime": now.isoformat(),
            "bids": [{"px": {"value": "0.55", "currency": "USD"}, "qty": "3000"}],
            "offers": [{"px": {"value": "0.57", "currency": "USD"}, "qty": "1000"}],
        }
    }


def test_real_rules_use_observation_deadline_and_single_instrument(market):
    assert market.contract_type == "touch_above"
    assert market.strike == 100_000
    assert market.resolution_time == datetime(2026, 10, 1, 4, tzinfo=UTC)
    assert market.settlement_time == datetime(2027, 1, 14, 23, tzinfo=UTC)
    assert market.market_id == f"polymarket_us:{market.slug}"
    assert market.instrument_id == market.slug
    assert not market.yes_token_id and not market.no_token_id and not market.condition_id
    assert market.resolution_source == "CF_BENCHMARKS_BRTI"


def test_all_supported_rule_families(rows):
    parsed = [parse_us_market(m) for e in rows["events"] for m in e["markets"]]
    assert all(p is not None and not p.rule_issues for p in parsed)
    assert {p.contract_type for p in parsed} == {
        "touch_above",
        "touch_below",
        "terminal_above",
        "terminal_below",
        "terminal_range",
    }
    high = parse_us_market(rows["events"][4]["markets"][0])
    assert high.strike == 89_999.99  # Do not round a slug's 90k into a different contract.
    terminal = next(p for p in parsed if p.contract_type == "terminal_range")
    assert terminal.strike == 145_000 and terminal.upper_strike == 149_999.99
    assert terminal.resolution_time == datetime(2027, 1, 1, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    "change",
    [
        {"slug": "itfme-ethan-tennis"},
        {"question": "Ethan versus someone"},
        {"marketSides": []},
        {"endDate": "bad"},
    ],
)
def test_bad_market_metadata_rejected(rows, change):
    row = copy.deepcopy(rows["events"][0]["markets"][0])
    row.update(change)
    assert parse_us_market(row) is None


def test_missing_deadline_not_replaced_by_settlement(rows):
    row = copy.deepcopy(rows["events"][0]["markets"][0])
    row["description"] = "Bitcoin above $200,000 at some later time"
    assert parse_us_market(row) is None


def test_unknown_rules_and_source_marked_ineligible(rows):
    row = rows["events"][0]["markets"][0]
    rules = parse_rules(
        row["description"].replace("CF Bitcoin Real-Time Index (BRTI)", "other index"), row["title"]
    )
    assert rules["rule_issues"] and not rules["resolution_source"]


def test_no_book_is_exact_complement_and_not_an_independent_token(market):
    yes, no = parse_book(book_payload(market), market)
    assert yes.best_bid == 0.55 and yes.best_ask == 0.57
    assert no.best_bid == 0.43 and no.best_ask == 0.45
    assert no.synthetic and not yes.synthetic
    assert no.instrument_id == yes.instrument_id == market.slug
    assert no.bids[0].size == yes.asks[0].size
    assert yes.best_ask + no.best_ask == 1.02


@pytest.mark.parametrize("bad", ["wrong_slug", "no_timestamp", "non_usd", "nan_quantity"])
def test_book_metadata_is_validated(market, bad):
    payload = book_payload(market)
    row = payload["marketData"]
    if bad == "wrong_slug":
        row["marketSlug"] = "something-else"
    elif bad == "no_timestamp":
        del row["transactTime"]
    elif bad == "non_usd":
        row["bids"][0]["px"]["currency"] = "EUR"
    else:
        row["bids"][0]["qty"] = "NaN"
    with pytest.raises((ValueError, KeyError)):
        parse_book(payload, market)


@pytest.mark.parametrize("bad", ["crossed", "one_sided", "stale", "future", "closed"])
def test_bad_books_never_produce_usable_signal(market, store, bad):
    settings = load_settings()
    payload = book_payload(market)
    row = payload["marketData"]
    if bad == "crossed":
        row["bids"][0]["px"]["value"] = ".60"
    elif bad == "one_sided":
        row["offers"] = []
    elif bad in {"stale", "future"}:
        row["transactTime"] = (NOW + timedelta(seconds=1 if bad == "future" else -3600)).isoformat()
    else:
        row["state"] = "MARKET_STATE_HALTED"
    yes, no = parse_book(payload, market)
    external = ExternalPrice(symbol="BTC-USD", timestamp=NOW, price=65_000)
    snap, f = FeatureEngine(settings, store).compute(market, yes, no, None, external, NOW)
    assert classify(f, market, settings)[0] == ScanStatus.NO_TRADE
    assert snap.yes_mid is None


def test_valid_us_data_is_monitor_only_with_missing_volume(market, store):
    settings = load_settings()
    yes, no = parse_book(book_payload(market), market)
    external = ExternalPrice(symbol="BTC-USD", timestamp=NOW, price=65_000)
    _, f = FeatureEngine(settings, store).compute(market, yes, no, None, external, NOW)
    assert f.volume_5m is None and not f.recent_trades_available
    assert f.fair_probability is None
    assert classify(f, market, settings)[0] == ScanStatus.WATCH
    f.momentum_5m, f.orderbook_imbalance, f.external_return_5m = 0.9, 0.99, 0.5
    assert classify(f, market, settings)[0] == ScanStatus.WATCH


@pytest.mark.parametrize("offset,symbol", [(1, "BTC-USD"), (-3600, "BTC-USD"), (0, "ETH-USD")])
def test_invalid_external_observation_blocks_classification(market, store, offset, symbol):
    settings = load_settings()
    yes, no = parse_book(book_payload(market), market)
    external = ExternalPrice(symbol=symbol, timestamp=NOW + timedelta(seconds=offset), price=65_000)
    _, f = FeatureEngine(settings, store).compute(market, yes, no, None, external, NOW)
    assert f.external_price_stale
    assert classify(f, market, settings)[0] == ScanStatus.NO_TRADE


def test_future_trades_old_history_and_cross_venue_history_not_used(market, store):
    settings = load_settings()
    store.upsert_market(market, NOW)
    store.insert_market_snapshot(
        MarketSnapshot(
            market_id=market.market_id,
            timestamp=NOW - timedelta(days=1),
            yes_mid=0.1,
        )
    )
    store.insert_external_price(
        ExternalPrice(
            symbol="BTC-USD",
            venue="kraken",
            timestamp=NOW - timedelta(minutes=5),
            price=10_000,
        )
    )
    trade = Trade(
        market_id=market.market_id, timestamp=NOW + timedelta(seconds=1), price=0.5, size=100
    )
    yes, no = parse_book(book_payload(market), market)
    _, f = FeatureEngine(settings, store).compute(
        market,
        yes,
        no,
        [trade],
        ExternalPrice(symbol="BTC-USD", timestamp=NOW, price=65_000),
        NOW,
    )
    assert f.volume_1m == 0 and f.recent_trade_count == 0
    assert f.momentum_1m is None and f.external_return_5m is None
    assert f.standardized_distance_to_strike is None


def test_zero_spread_is_ranked_better_than_unknown(market, store):
    yes, no = parse_book(book_payload(market), market)
    _, f = FeatureEngine(load_settings(), store).compute(market, yes, no, None, None, NOW)
    f.spread = 0
    zero = rank_score(market, f)
    f.spread = None
    assert zero > rank_score(market, f)


def test_real_historical_crossed_quote_is_preserved_and_flagged(market, store):
    point = QuoteHistoryPoint(
        market_id=market.market_id, timestamp=datetime(2026, 8, 28, 1, 22, 22, tzinfo=UTC),
        yes_ask=.07, no_ask=.92,
    )
    assert point.is_crossed
    store.upsert_market(market, NOW)
    store.insert_quote_history([point], NOW)
    with store.connect() as c:
        row = c.execute("SELECT yes_ask,no_ask FROM quote_history").fetchone()
        assert tuple(row) == (.07, .92)


def test_storage_preserves_metadata_versions_and_enforces_foreign_keys(store, market):
    store.upsert_market(market, NOW)
    updated = market.model_copy(update={"rule_hash": "changed"})
    store.upsert_market(updated, NOW + timedelta(seconds=10))
    assert store.counts()["market_metadata_snapshots"] == 2
    with store.connect() as c:
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c.execute("SELECT venue FROM markets").fetchone()[0] == "polymarket_us"
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_market_snapshot(MarketSnapshot(market_id="orphan", timestamp=NOW))


@pytest.mark.asyncio
async def test_discovery_retries_and_filters_expired_markets(rows):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200, json=rows)

    settings = load_settings()
    settings.api.retry_base_seconds = 0
    async with httpx.AsyncClient(
        base_url=settings.api.us_base_url, transport=httpx.MockTransport(handler)
    ) as client:
        api = PublicAPI(settings, client)
        discovery = PolymarketUSDiscovery(settings, api)
        markets = await discovery.discover(NOW)
    assert len(calls) == 2 and all(r.method == "GET" for r in calls)
    assert all(m.active and not m.closed and m.resolution_time > NOW for m in markets)
    assert discovery.last_stats["rejected"]["not_open"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 451])
async def test_access_denials_are_not_retried(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    settings = load_settings()
    async with httpx.AsyncClient(
        base_url=settings.api.us_base_url, transport=httpx.MockTransport(handler)
    ) as client:
        api = PublicAPI(settings, client)
        with pytest.raises(httpx.HTTPStatusError):
            await api.get("/v1/search")
        with pytest.raises(ValueError):
            await api.get("/v1/orders")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_history_is_cached_filtered_and_separate_from_trades(market, store):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "history": [
                    {"timestamp": int(NOW.timestamp()), "longPrice": 0.57, "shortPrice": 0.45},
                    {
                        "timestamp": int((NOW + timedelta(days=1)).timestamp()),
                        "longPrice": 0.6,
                        "shortPrice": 0.42,
                    },
                ]
            },
        )

    settings = load_settings()
    async with httpx.AsyncClient(
        base_url=settings.api.us_base_url, transport=httpx.MockTransport(handler)
    ) as client:
        feed = PolymarketUSFeed(settings, PublicAPI(settings, client))
        kwargs = {"start": NOW, "end": NOW + timedelta(days=1)}
        points = await feed.get_price_history(market, **kwargs)
        assert len(points) == 1
        points[0].yes_ask = 0.01  # Caller cannot corrupt the cache.
        again = await feed.get_price_history(market, **kwargs)
        assert again[0].yes_ask == 0.57
        assert await feed.get_recent_trades(market) is None
        with pytest.raises(ValueError):
            await feed.get_price_history(market, start=NOW, end=NOW + timedelta(days=2))
    assert len(calls) == 1 and calls[0].url.params["fidelity"] == "1"
    store.upsert_market(market, NOW)
    assert store.insert_quote_history(again, NOW) == 1
    assert store.insert_quote_history(again, NOW) == 0
    assert store.counts()["market_trades"] == 0
    assert store.counts()["market_snapshots"] == 0


@pytest.mark.asyncio
async def test_loop_survives_discovery_failure_and_honors_cancellation(store):
    class Discovery:
        async def discover(self, now=None):
            return []

    class Prices:
        async def get_btc_eth(self):
            return {}

    scanner = Scanner(load_settings(), store, Discovery(), object(), Prices())
    calls = 0
    original = scanner.scan_once

    async def transient():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary")
        return await original()

    scanner.scan_once = transient
    scanner.settings.scanner.refresh_seconds = 0
    await scanner.run_forever(max_cycles=2)
    assert calls == 2

    async def cancel():
        raise asyncio.CancelledError()

    scanner.scan_once = cancel
    with pytest.raises(asyncio.CancelledError):
        await scanner.run_forever(max_cycles=1)
