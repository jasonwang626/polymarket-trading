# polymarket-trading

**Polymarket US 中長天期 BTC 唯讀掃描器**。使用者於 2026-09-08 核准此範圍；規格與順序以 [SPEC.md](docs/SPEC.md)／[PLAN.md](docs/PLAN.md) 為準。

程式會搜尋合約、讀取委託簿、收集 BTC／ETH 現貨參考價格、儲存快照並檢查資料品質。目前輸出 `WATCH` 或 `NO_TRADE`；尚未計算經驗證的勝率，也沒有模擬交易、LLM、帳號登入、錢包或下單功能。

## 安裝與執行

需要 Python 3.11+：

```bash
uv sync --frozen --extra dev
uv run python scripts/run_scanner.py --offline --once
uv run python scripts/run_scanner.py --once
```

離線模式使用明確標記的合成範例，預設寫入 `data/offline-us.sqlite3`。即時資料預設寫入 `data/scanner-us.sqlite3`。

持續掃描，按 Ctrl-C 停止：

```bash
uv run python scripts/run_scanner.py
```

執行三輪、限制三個市場，使用獨立驗證資料庫：

```bash
uv run python scripts/run_scanner.py --cycles 3 --max-markets 3 --database data/validation-us.sqlite3
```

日誌寫入 `logs/scanner.jsonl`。單一合約讀取失敗會記錄 `NO_TRADE`；暫時性市場搜尋失敗會於下一輪重試。資料庫故障不會被當成正常行情略過。

## 歷史報價收集

已提供逐日切分的公開歷史收集工具，日期為 UTC，起始日包含、結束日不包含：

```bash
uv run python scripts/collect_us_history.py \
  --slug cpc-btc-100k-09-30-2026 \
  --start 2026-08-07 --end 2026-09-07 \
  --database data/history-us.sqlite3
```

此範例的合約可能已到期，仍可嘗試讀取官方保留的歷史。API 回傳空陣列時會報告 0 筆，不生成資料。收集結果為 `quote_history` 的顯示買價；**這不是逐筆成交、模擬成交或損益回測**。

## 市場與資料語意

- 美國版每個市場是一個 instrument，以 `polymarket_us:{slug}` 識別。
- NO 顯示報價由同一委託簿轉換，並標記 `synthetic=true`；不代表另一組 token 或獨立深度。
- 分開辨識期間觸價、到期門檻及到期區間。觀察截止時間取自規則，另存 API 的結算日期。
- 目前合約以 CF Benchmarks BRTI 規則判定。Coinbase／Kraken 的現貨只是參考，不能代替該指數判定結果。
- 公開 REST 介接沒有逐筆成交流，分鐘成交量、成交量加速度等欄位是空值。
- 外部價格使用交易所時間；缺失、過期、未來時間、幣別錯誤、單邊或交叉委託簿都會阻止有效訊號。
- 動能只取目標時間之前、容忍範圍內的同來源歷史。第一輪及資料缺口不產生虛構報酬率。
- 全部美國版結果維持行情監控，`fair_probability=null`。方向性標籤只保留在舊版回歸程式，不由目前 CLI 產生。

## 設定

`config/settings.yaml` 設定刷新頻率、觀察天期、價差及深度下限。預設最優價附近 1% 的雙邊掛單金額各至少 100 美元，並限制最寬價差 0.08。這些是可調整的資料篩選門檻，不是已驗證的獲利條件。

API 固定使用 `https://gateway.polymarket.us`，共用每秒 5 次節流器與有限重試。401／403／451 不重試或繞過；429 依 Retry-After 延後。歷史重複請求至少快取 30 秒。

掃描器不需要任何 Polymarket API key；啟動時會拒絕已知的交易憑證環境變數。`config/risk.yaml` 仍固定 READ_ONLY。

## 儲存與相容性

| 資料表 | 用途 |
|---|---|
| `markets` | 目前市場 metadata、平台、instrument、規則類型及日期 |
| `market_metadata_snapshots` | 每次觀察的完整規則與 metadata，保留歷程 |
| `market_snapshots` | 當輪買賣價、有效 mid 與價差 |
| `orderbook_snapshots` | 原始／合成委託簿，來源與接收時間 |
| `external_prices` | BTC／ETH 現貨價格，交易所時間與接收時間 |
| `quote_history` | 美國版歷史顯示買價，與交易紀錄分開 |
| `features` | 特徵、資料品質、分類與排序 |
| `market_trades` | 舊版成交資料；目前 US REST 不寫入虛構成交 |

SQLite schema v2 採新增欄位及資料表升級，保留舊版資料。`condition_id`、`yes_token_id`、`no_token_id` 僅供舊國際版紀錄；US 紀錄為空字串。不要刪除原始資料庫來升級。

舊國際版解析器保留供歷史回歸測試，CLI 固定使用美國版。舊月測試報告不能當成美國版策略績效。

## 驗證與進度

```bash
uv run pytest -q
uv run ruff check .
```

詳見 [實作狀態](docs/IMPLEMENTATION_STATUS.md) 與 [美國版驗證報告](docs/US_VALIDATION_REPORT.md)。下一個階段先建立資料重播，再驗證勝率、風控與本機模擬交易；完整交易時段的穩定性仍需實際部署驗收。

API 依據：[Polymarket US](https://docs.polymarket.us/api-reference/introduction)、[歷史資料](https://docs.polymarket.us/api-reference/price-history/get-price-history)、[Coinbase ticker](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker)、[Kraken Recent Trades](https://docs.kraken.com/api-reference/market-data/get-recent-trades)。


## 自動檢查已收集的資料

停止掃描後，在專案目錄執行：

```bash
uv run --frozen python scripts/validate_capture.py \
  --database data/mac-hour.sqlite3 \
  --output-dir reports/mac-hour-validation
```

工具不連網、不改動資料庫；產生 `validation.md` 與 `validation.json`。若報告已存在，請使用新的 output-dir，避免混淆不同次測試。資料庫須為 schema v2；空資料會明確呈現空觀測期間。完整性檢查成功不等同行情新鮮、資料完整或策略可獲利。

暫停市場等待期間仍有 NO_TRADE 決策特徵，但不會產生新的委託簿快照，因此特徵筆數與行情筆數可不同。報告保留此差異。

已驗證的 Mac 樣本：
- [一小時資料稽核](docs/validation/mac-hour/validation.md)
- [自動同步時間後資料稽核](docs/validation/mac-clock-check/validation.md)


## 歷史行情品質重播

```bash
uv run --frozen python scripts/replay_capture.py \
  --database data/mac-hour.sqlite3 \
  --output-dir reports/mac-hour-replay
```

輸出 `summary.json`、逐筆 `events.jsonl` 與 `report.md`，不連網或修改來源資料。規則、委託簿及參考價格均受當時可得時間限制，歷史特徵重新計算。缺少規則或委託簿時為 UNDETERMINED。

重播可沿用當時已收到且未過期的最近參考價格；目前即時 scanner 每輪僅使用新請求結果。因此結果可能不同，這是明列的品質政策比較，並非策略獲利或精確原始決策還原。市場暫停等待沒有真實行情快照，不補造重播資料。

實測摘要：[一小時品質重播](docs/replay/mac-hour/report.md)、[時鐘同步後品質重播](docs/replay/mac-clock-check/report.md)。


## 事件分組與模型資料準備度

以下日期僅示範如何對目前 Mac 樣本驗證切分阻擋條件，不是已選定的模型實驗設計：

```bash
uv run --frozen python scripts/build_dataset_manifest.py \
  --database data/mac-hour.sqlite3 \
  --train-end 2026-09-09T00:00:00Z \
  --validation-end 2026-09-11T00:00:00Z \
  --output-dir reports/mac-dataset-manifest
```

輸出 `summary.json`、`manifest.jsonl` 及 `report.md`，不修改原始資料。相同事件／市場的關聯群組不跨資料集，預設邊界後隔離 24 小時，訓練／驗證也限制結果觀察截止。缺少事件身分或跨界的群組會排除。

目前沒有經驗證的結果標籤，因此所有資料皆不可用於監督式訓練。此工具不從價格或市場暫停推斷輸贏。樣本結果見 [Mac 一小時分組清單摘要](docs/datasets/mac-hour/report.md)。


## 收集公開結算證據

```bash
uv run --frozen python scripts/collect_settlement_evidence.py \
  --slug cpc-btc-100k-09-30-2026 \
  --output-dir reports/settlement-evidence
```

此 slug 僅為已知合約示例，不代表它已結算。可重複提供 --slug（最多 20 個不重複商品）；輸出目錄必須不存在。收集不需要交易帳戶或金鑰；不連接 BRTI 授權服務。

輸出保留官方 GET 回應的 JSON、取得時間、完整性 hash 與驗證原因。candidate_payout 不是模型標籤；目前所有結果仍不可訓練。連線失敗也會保存原因並以非零狀態結束。詳見 [結算資料查核](docs/SETTLEMENT_DATA_REVIEW.md)。
