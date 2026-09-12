
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
