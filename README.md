# polymarket-trading

Phase 0／Sprint 1 的交付版本：一個**唯讀**的 Polymarket 加密貨幣市場掃描器。

目前只做：

```text
Market Discovery
      ↓
Polymarket Market Data
      ↓
BTC / ETH External Price
      ↓
Feature Engine
      ↓
WATCH / POSSIBLE_YES / POSSIBLE_NO / NO_TRADE
```

目前不包含 LLM、錢包、簽章、下單、paper trading 或 live trading。所有網路介面都只公開讀取方法；若執行環境中出現 Polymarket 私鑰、錢包地址或交易 API 憑證，掃描器會拒絕啟動。

## 快速開始

需求：Python 3.11 以上。建議使用 `uv`：

```bash
uv sync --extra dev
```

先以內建 fixtures 驗證完整流程（不連網）：

```bash
uv run python scripts/run_scanner.py --offline --once
```

執行一輪即時唯讀掃描：

```bash
uv run python scripts/run_scanner.py --once
```

依 `config/settings.yaml` 的更新頻率持續執行：

```bash
uv run python scripts/run_scanner.py
```

按 `Ctrl-C` 安全停止。掃描結果存入 `data/scanner.sqlite3`，結構化日誌寫入 `logs/scanner.jsonl`。

## 測試與品質檢查

```bash
uv run pytest -q
uv run ruff check .
```

## 專案結構

```text
polymarket-agent/
├── config/
│   ├── settings.yaml
│   └── risk.yaml
├── data/
├── docs/
│   ├── SPEC.md
│   ├── PLAN.md
│   └── IMPLEMENTATION_STATUS.md
├── logs/
├── scripts/
│   └── run_scanner.py
├── src/polymarket_agent/
│   ├── config.py
│   ├── logging.py
│   ├── models.py
│   ├── offline.py
│   ├── discovery/polymarket.py
│   ├── data/
│   │   ├── crypto_feed.py
│   │   ├── polymarket_feed.py
│   │   └── storage.py
│   ├── features/market_features.py
│   └── scanner/scanner.py
└── tests/
```

## 資料來源

- Gamma API：搜尋 active／open markets 與取得 token IDs。
- CLOB API：讀取 YES／NO order books。
- Data API：讀取市場近期成交。
- Coinbase Exchange public ticker：BTC-USD 與 ETH-USD 外部參考價格。
- Kraken public ticker：Coinbase 暫時失效時的唯讀備援來源。

這些端點皆不需錢包或交易驗證。實作依據：

- https://docs.polymarket.com/getting-started/api
- https://docs.polymarket.com/market-data/market-details
- https://docs.polymarket.com/api-reference/market-data/get-order-book
- https://docs.polymarket.com/market-data/public-analytics
- https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-all-known-trading-pairs
- https://docs.kraken.com/api-reference/market-data/get-ticker-information

## SQLite 資料表

| 資料表 | 用途 | 寫入方式 |
|---|---|---|
| `markets` | 市場主檔與目前 metadata | 依 `market_id` 更新 |
| `market_snapshots` | YES／NO bid、ask、mid、spread | append-only |
| `orderbook_snapshots` | 完整 bid／ask levels 與原始 JSON | append-only |
| `external_prices` | BTC／ETH timestamped spot price | append-only |
| `market_trades` | 公開成交紀錄 | 去重後保留 |
| `features` | 每輪特徵、分類與 ranking | append-only |

## 初始特徵

- YES／NO mid-price、spread、spread %
- YES price change／momentum：1m、5m、15m
- 成交量：1m、5m、15m；velocity、acceleration、buy／sell ratio
- 1% order-book depth、imbalance、top-level imbalance、depth ratio
- BTC／ETH spot return：1m、5m、15m
- seconds／minutes to expiry
- 可辨識 threshold 市場的 distance to strike 與標準化距離
- 外部價格 stale flag

第一輪執行時，部分歷史型特徵會是空值；資料累積滿 1／5／15 分鐘後才會逐步可用。這是刻意設計，避免用不存在的歷史資料製造訊號。

## 掃描分類

- `NO_TRADE`：資料過期、order book 不完整、spread 過大、流動性不足或市場已到期。
- `POSSIBLE_YES`：order-book imbalance、YES momentum、外部價格動能中至少兩項支持 YES。
- `POSSIBLE_NO`：同樣條件中至少兩項支持 NO。
- `WATCH`：市場可監控，但支持證據未達設定門檻。

這些只是 scanner 標籤，不是交易建議，也不會建立 intended order。

## 設定

一般執行與篩選參數在 `config/settings.yaml`。`config/risk.yaml` 只預留給後續 deterministic risk engine，現在固定：

```yaml
mode: READ_ONLY
wallet_enabled: false
order_submission_enabled: false
live_trading_enabled: false
```

Phase 2 的 quantitative fair-probability model、Phase 3 的 LLM review layer 與 Phase 4 的 paper trading 都尚未加入，符合 `SPEC.md`／`PLAN.md` 的開發順序。
