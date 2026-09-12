import ast
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from polymarket_agent.config import Settings, assert_no_trading_secrets, load_settings
from polymarket_agent.data.crypto_feed import CryptoPriceFeed
from polymarket_agent.data.storage import Storage
from polymarket_agent.discovery.polymarket import infer_strike, parse_market
from polymarket_agent.models import FeatureSnapshot, Market, OrderBook, ScanStatus
from polymarket_agent.scanner.scanner import classify

FIXTURES = Path(__file__).parent / "fixtures"


def test_upgrade_real_v1_schema_keeps_existing_data(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as c:
        c.executescript((FIXTURES / "schema_v1.sql").read_text())
        c.execute("""INSERT INTO markets VALUES (
            'old','condition','old-slug','old BTC','crypto','2026-12-01T00:00:00+00:00',
            'yes','no',1000,2000,'WATCH','BTC-USD',100000,'2026-09-01','2026-09-01','{}')""")
        c.execute("""INSERT INTO market_snapshots
            (market_id,timestamp,yes_mid,volume_24h,liquidity)
            VALUES ('old','2026-09-01T00:00:00+00:00',0.5,2000,1000)""")
    db = Storage(path)
    db.initialize()
    db.initialize()
    with db.connect() as c:
        old = c.execute("SELECT * FROM markets WHERE market_id='old'").fetchone()
        assert old["yes_token_id"] == "yes" and old["venue"] == "polymarket_international"
        assert c.execute("PRAGMA user_version").fetchone()[0] == 3
        feature_columns = {row["name"] for row in c.execute("PRAGMA table_info(features)")}
        assert "decision_reasons_json" in feature_columns
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.counts()["market_snapshots"] == 1


def test_unknown_newer_schema_is_not_downgraded(tmp_path):
    db = Storage(tmp_path / "new.sqlite3")
    with db.connect() as c:
        c.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="newer"):
        db.initialize()
    with db.connect() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 99


def test_legacy_year_not_mistaken_for_strike_and_up_down_labels_supported():
    assert infer_strike("In 2026 will Bitcoin be above $62,000?", "BTC-USD") == 62000
    row = json.loads((FIXTURES / "market.json").read_text())
    row["outcomes"] = ["Up", "Down"]
    assert parse_market(row) is not None
    row["outcomes"] = ["Maybe", "Yes", "No"]
    assert parse_market(row) is None


def test_last_trade_is_not_a_mid_quote():
    assert OrderBook(token_id="old", last_trade_price=0.8).mid is None


@pytest.mark.asyncio
async def test_coinbase_preserves_exchange_timestamp():
    source = datetime.now(UTC) - timedelta(hours=1)
    async with httpx.AsyncClient(
        base_url="https://api.exchange.coinbase.com",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={"time": source.isoformat(), "price": "65000", "volume": "100"},
            )
        ),
    ) as client:
        feed = CryptoPriceFeed(load_settings(), client=client, kraken_client=client)
        price = await feed.get_spot("BTC-USD")
    assert price.timestamp == source
    assert price.received_at > price.timestamp


@pytest.mark.parametrize(
    "name",
    [
        "POLYMARKET_US_API_KEY",
        "POLYMARKET_US_API_SECRET",
        "POLYMARKET_US_KEY_ID",
        "POLYMARKET_US_SECRET_KEY",
    ],
)
def test_us_credentials_are_rejected(monkeypatch, name):
    monkeypatch.setenv(name, "synthetic-test-value")
    with pytest.raises(RuntimeError, match="Refusing"):
        assert_no_trading_secrets()


def test_us_platform_and_read_only_cannot_be_changed_in_yaml():
    raw = load_settings().model_dump()
    raw["project_root"] = Path(".")
    raw["app"]["venue"] = "polymarket_international"
    with pytest.raises(ValueError):
        Settings(**raw)
    raw["app"]["venue"] = "polymarket_us"
    raw["app"]["read_only"] = False
    with pytest.raises(ValueError):
        Settings(**raw)


def test_legacy_below_does_not_interpret_btc_rise_as_yes():
    market = Market(
        market_id="old",
        question="Bitcoin below $100,000?",
        resolution_time=datetime.now(UTC) + timedelta(days=1),
        liquidity=10000,
    )
    f = FeatureSnapshot(
        market_id="old",
        spread=0.02,
        orderbook_imbalance=0.8,
        external_return_5m=0.1,
        seconds_to_expiry=86400,
        minutes_to_expiry=1440,
        recent_trades_available=True,
        recent_trade_count=1,
    )
    assert classify(f, market, load_settings())[0] == ScanStatus.WATCH
    f.recent_trade_count = 0
    assert classify(f, market, load_settings())[0] == ScanStatus.NO_TRADE


def test_source_has_no_http_mutations_or_trading_sdk():
    root = Path(__file__).parents[1] / "src/polymarket_agent"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"post", "put", "patch", "delete", "sign_transaction"}
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("web3", "eth_account", "py_clob_client"))
