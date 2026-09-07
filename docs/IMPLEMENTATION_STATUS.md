# Phase 0／Sprint 1 Implementation Status

更新日期：2026-09-07

## Phase 0

- [x] Repository skeleton
- [x] Python 3.11+ packaging (`pyproject.toml` + `uv.lock`)
- [x] YAML configuration system
- [x] `.env.example` with no secrets
- [x] Read-only safety assertions
- [x] Structured JSONL logging
- [x] SQLite initialization and schema
- [x] Pytest framework
- [x] CLI entry point and `scripts/run_scanner.py`

## Sprint 1

- [x] Active Polymarket market discovery through Gamma API
- [x] YES／NO token ID parsing
- [x] Crypto, status, horizon, liquidity, volume filters
- [x] CLOB best bid／ask and full order-book collection
- [x] Data API recent-trade collection
- [x] Coinbase BTC／ETH public ticker
- [x] Kraken public ticker fallback
- [x] External-price stale detection
- [x] Append-only market, order-book and external-price snapshots
- [x] Recent-trade deduplication
- [x] Price, volume, order-book, external and expiry features
- [x] Watchlist ranking
- [x] `WATCH`, `POSSIBLE_YES`, `POSSIBLE_NO`, `NO_TRADE`
- [x] Offline end-to-end fixture mode
- [x] Unit and integration tests
- [x] Live read-only API smoke test for Gamma, CLOB and Data API

## 尚未完成的長時間驗證

`PLAN.md` 的 Sprint 1 exit criterion 要求掃描器連續執行至少一個 trading session。程式已支援持續執行，但本次交付只完成單輪 live smoke test，尚未宣稱長時間 session 驗證完成。

本執行環境對 Coinbase 與 Kraken 回傳 gateway／proxy error；scanner 依設計將受影響市場標成 `NO_TRADE`，其餘 Polymarket 資料仍可正常蒐集。離線整合測試已驗證外部價格、特徵、分類與儲存的完整資料路徑；實際部署環境仍應確認至少一個外部價格來源可連線。

## 明確未納入

- LLM decision agent
- Wallet／private key
- Order creation or submission
- Paper／shadow／live execution
- Quantitative fair-probability model
- Autonomous strategy changes

