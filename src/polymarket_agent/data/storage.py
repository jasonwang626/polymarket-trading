from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from polymarket_agent.models import (
    ExternalPrice,
    FeatureSnapshot,
    Market,
    MarketSnapshot,
    OrderBook,
    QuoteHistoryPoint,
    Trade,
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS markets (
    market_id TEXT PRIMARY KEY,
    condition_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    question TEXT NOT NULL,
    category TEXT NOT NULL,
    resolution_time TEXT NOT NULL,
    yes_token_id TEXT NOT NULL,
    no_token_id TEXT NOT NULL,
    liquidity REAL NOT NULL,
    volume_24h REAL NOT NULL,
    status TEXT NOT NULL,
    underlying TEXT,
    strike REAL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL REFERENCES markets(market_id),
    timestamp TEXT NOT NULL,
    yes_bid REAL,
    yes_ask REAL,
    no_bid REAL,
    no_ask REAL,
    yes_mid REAL,
    no_mid REAL,
    volume_24h REAL NOT NULL,
    liquidity REAL NOT NULL,
    spread REAL
);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_market_time
ON market_snapshots(market_id, timestamp);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL REFERENCES markets(market_id),
    outcome TEXT NOT NULL,
    token_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    best_bid REAL,
    best_ask REAL,
    bid_levels_json TEXT NOT NULL,
    ask_levels_json TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orderbooks_market_time
ON orderbook_snapshots(market_id, timestamp);

CREATE TABLE IF NOT EXISTS external_prices (
    price_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    venue TEXT NOT NULL,
    price REAL NOT NULL,
    volume_24h REAL
);

CREATE INDEX IF NOT EXISTS idx_external_prices_symbol_time
ON external_prices(symbol, timestamp);

CREATE TABLE IF NOT EXISTS market_trades (
    trade_key TEXT PRIMARY KEY,
    market_id TEXT NOT NULL REFERENCES markets(market_id),
    timestamp TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    side TEXT NOT NULL,
    outcome TEXT NOT NULL,
    transaction_hash TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_trades_market_time
ON market_trades(market_id, timestamp);

CREATE TABLE IF NOT EXISTS features (
    feature_snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL REFERENCES markets(market_id),
    timestamp TEXT NOT NULL,
    status TEXT NOT NULL,
    rank_score REAL NOT NULL,
    features_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_features_market_time
ON features(market_id, timestamp);

CREATE TABLE IF NOT EXISTS market_metadata_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL REFERENCES markets(market_id),
    observed_at TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quote_history (
    market_id TEXT NOT NULL REFERENCES markets(market_id),
    timestamp TEXT NOT NULL,
    yes_ask REAL NOT NULL,
    no_ask REAL NOT NULL,
    source TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (market_id, timestamp, yes_ask, no_ask, source)
);
"""


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] > 2:
                raise ValueError("Database schema is newer than this application")
            connection.executescript(SCHEMA)
            additions = {
                "markets": {
                    "venue": "TEXT NOT NULL DEFAULT 'polymarket_international'",
                    "instrument_id": "TEXT NOT NULL DEFAULT ''",
                    "contract_type": "TEXT NOT NULL DEFAULT 'unknown'",
                    "settlement_time": "TEXT",
                    "rule_hash": "TEXT NOT NULL DEFAULT ''",
                },
                "orderbook_snapshots": {
                    "instrument_id": "TEXT NOT NULL DEFAULT ''",
                    "synthetic": "INTEGER NOT NULL DEFAULT 0",
                    "received_at": "TEXT",
                },
                "external_prices": {"received_at": "TEXT"},
            }
            for table, columns in additions.items():
                existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
                for name, definition in columns.items():
                    if name not in existing:
                        connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            connection.execute("PRAGMA user_version=2")

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))

    def upsert_market(self, market: Market, observed_at: datetime) -> None:
        status = "WATCH" if market.active and not market.closed else "INACTIVE"
        params = (
            market.market_id,
            market.condition_id,
            market.slug,
            market.question,
            market.category,
            market.resolution_time.isoformat(),
            market.yes_token_id,
            market.no_token_id,
            market.liquidity,
            market.volume_24h,
            status,
            market.underlying,
            market.strike,
            observed_at.isoformat(),
            observed_at.isoformat(),
            self._json(market.raw),
        )
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO markets (
                    market_id, condition_id, slug, question, category, resolution_time,
                    yes_token_id, no_token_id, liquidity, volume_24h, status,
                    underlying, strike, first_seen_at, last_seen_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_id) DO UPDATE SET
                    condition_id=excluded.condition_id,
                    slug=excluded.slug,
                    question=excluded.question,
                    category=excluded.category,
                    resolution_time=excluded.resolution_time,
                    yes_token_id=excluded.yes_token_id,
                    no_token_id=excluded.no_token_id,
                    liquidity=excluded.liquidity,
                    volume_24h=excluded.volume_24h,
                    status=excluded.status,
                    underlying=excluded.underlying,
                    strike=excluded.strike,
                    last_seen_at=excluded.last_seen_at,
                    raw_json=excluded.raw_json
                """,
                params,
            )
            connection.execute(
                """UPDATE markets SET venue=?, instrument_id=?, contract_type=?,
                settlement_time=?, rule_hash=? WHERE market_id=?""",
                (market.venue, market.instrument_id, market.contract_type,
                 market.settlement_time.isoformat() if market.settlement_time else None,
                 market.rule_hash, market.market_id),
            )
            connection.execute(
                """INSERT INTO market_metadata_snapshots
                (market_id, observed_at, normalized_json, raw_json) VALUES (?, ?, ?, ?)""",
                (market.market_id, observed_at.isoformat(),
                 self._json(market.model_dump(mode="json")), self._json(market.raw)),
            )

    def insert_market_snapshot(self, snapshot: MarketSnapshot) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO market_snapshots (
                    market_id, timestamp, yes_bid, yes_ask, no_bid, no_ask,
                    yes_mid, no_mid, volume_24h, liquidity, spread
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.market_id,
                    snapshot.timestamp.isoformat(),
                    snapshot.yes_bid,
                    snapshot.yes_ask,
                    snapshot.no_bid,
                    snapshot.no_ask,
                    snapshot.yes_mid,
                    snapshot.no_mid,
                    snapshot.volume_24h,
                    snapshot.liquidity,
                    snapshot.spread,
                ),
            )
            return int(cursor.lastrowid)

    def insert_orderbook(self, market_id: str, outcome: str, book: OrderBook) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO orderbook_snapshots (
                    market_id, outcome, token_id, timestamp, best_bid, best_ask,
                    bid_levels_json, ask_levels_json, raw_json, instrument_id, synthetic, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    market_id,
                    outcome,
                    book.token_id,
                    book.timestamp.isoformat(),
                    book.best_bid,
                    book.best_ask,
                    self._json([x.model_dump() for x in book.bids]),
                    self._json([x.model_dump() for x in book.asks]),
                    self._json(book.raw),
                    book.instrument_id,
                    int(book.synthetic),
                    book.received_at.isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def insert_external_price(self, observation: ExternalPrice) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO external_prices (timestamp, symbol, venue, price, volume_24h, received_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.timestamp.isoformat(),
                    observation.symbol,
                    observation.venue,
                    observation.price,
                    observation.volume_24h,
                    observation.received_at.isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def insert_trades(self, trades: list[Trade]) -> int:
        inserted = 0
        with self.connect() as connection:
            for trade in trades:
                identity = "|".join(
                    [
                        trade.market_id,
                        trade.timestamp.isoformat(),
                        str(trade.price),
                        str(trade.size),
                        trade.side,
                        trade.outcome,
                        trade.transaction_hash,
                    ]
                )
                trade_key = hashlib.sha256(identity.encode()).hexdigest()
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO market_trades (
                        trade_key, market_id, timestamp, price, size, side,
                        outcome, transaction_hash, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade_key,
                        trade.market_id,
                        trade.timestamp.isoformat(),
                        trade.price,
                        trade.size,
                        trade.side,
                        trade.outcome,
                        trade.transaction_hash,
                        self._json(trade.raw),
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    def insert_features(
        self, features: FeatureSnapshot, status: str, rank_score: float
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO features (market_id, timestamp, status, rank_score, features_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    features.market_id,
                    features.timestamp.isoformat(),
                    status,
                    rank_score,
                    self._json(features.model_dump(mode="json")),
                ),
            )
            return int(cursor.lastrowid)

    def market_mid_at_or_before(
        self, market_id: str, timestamp: datetime, max_age_seconds: int = 60
    ) -> float | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT yes_mid FROM market_snapshots
                WHERE market_id = ? AND timestamp <= ? AND timestamp >= ? AND yes_mid IS NOT NULL
                ORDER BY timestamp DESC LIMIT 1
                """,
                (market_id, timestamp.isoformat(),
                 (timestamp - timedelta(seconds=max_age_seconds)).isoformat()),
            ).fetchone()
        return float(row["yes_mid"]) if row else None

    def external_price_at_or_before(
        self, symbol: str, timestamp: datetime, max_age_seconds: int = 60,
        venue: str | None = None, available_at: datetime | None = None,
    ) -> float | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT price FROM external_prices
                WHERE symbol = ? AND timestamp <= ? AND timestamp >= ?
                    AND (? IS NULL OR venue = ?)
                    AND received_at IS NOT NULL AND received_at <= ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (symbol, timestamp.isoformat(),
                 (timestamp - timedelta(seconds=max_age_seconds)).isoformat(), venue, venue,
                 (available_at or timestamp).isoformat()),
            ).fetchone()
        return float(row["price"]) if row else None

    def insert_quote_history(self, points: list[QuoteHistoryPoint], retrieved_at: datetime) -> int:
        with self.connect() as connection:
            before = connection.total_changes
            connection.executemany(
                """INSERT OR IGNORE INTO quote_history
                (market_id, timestamp, yes_ask, no_ask, source, retrieved_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                [(p.market_id, p.timestamp.isoformat(), p.yes_ask, p.no_ask,
                  p.source, retrieved_at.isoformat()) for p in points],
            )
            return connection.total_changes - before

    def counts(self) -> dict[str, int]:
        tables = (
            "markets",
            "market_snapshots",
            "orderbook_snapshots",
            "external_prices",
            "market_trades",
            "features",
            "market_metadata_snapshots",
            "quote_history",
        )
        with self.connect() as connection:
            return {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in tables
            }
