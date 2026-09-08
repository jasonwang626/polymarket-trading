# Polymarket Autonomous Trading Agent — SPEC

## 1. Purpose

Build a modular autonomous agent for Polymarket that can:

1. Discover and monitor relevant markets.
2. Collect market and external reference data.
3. Generate quantitative features and trading signals.
4. Use an LLM agent to review signals and market context.
5. Apply deterministic risk controls.
6. Execute paper trades first, then optionally controlled live trades.
7. Log every decision and outcome.
8. Evaluate strategy performance.
9. Propose strategy improvements through a gated backtest and validation workflow.

The system must not allow an LLM to directly bypass deterministic risk controls or silently modify production trading logic.

---

## 2. Initial Scope

### 核准的 v1 範圍（2026-09-08）

使用者已確認使用 Polymarket US，並同意第一版改為美國版現有的中長天期 BTC 合約。這一節取代原本以短天期 BTC／ETH 為主的假設。

- 執行平台固定為 `polymarket_us`；只使用免登入的公開 REST 行情 API。
- 合約觀察截止時間預設介於 0.05 小時至 365 天，可縮小範圍；實際候選仍須通過市場開放、規則可解析、價差與雙邊掛單深度檢查。
- 分別辨識期間觸價 `touch_above`／`touch_below`，及到期判定 `terminal_above`／`terminal_below`／`terminal_range`。它們不能共用未經驗證的勝率模型。
- 依完整 Yes 判定條件解析金額、比較運算、觀察截止時間及結算指數；不能用年份、slug 的四捨五入金額或 API 的 `endDate` 猜測。
- `resolution_time` 在目前 schema 表示規則中的觀察截止時間；`settlement_time` 另存 API 的 `endDate`。ET 使用 `America/New_York` 的日光節約時間規則，再轉成 UTC。
- 目前支援的實際規則以 CF Benchmarks BRTI 的 60 秒截尾平均值判定。Coinbase／Kraken 現貨是參考特徵，不能代替 BRTI 判定觸價或合約結果。
- 保留 BTC／ETH 外部價格收集能力；ETH Polymarket 合約及 15 分鐘 Up/Down 不列為美國版現階段已支援功能。
- 本次只有行情掃描、儲存、歷史報價讀取與資料品質檢查；輸出 `WATCH` 或 `NO_TRADE`。`fair_probability` 保留空值，禁止把動能或掛單分數當成勝率。
- 不連接帳號、錢包、交易 API 憑證，不建立訂單，也不啟用紙上或實盤執行。

### 不在這次交付範圍

體育／政治市場、槓桿、借款、跨平台資金移轉、未經確認的 ETH 合約、LLM 決策、勝率模型、模擬／實盤交易與自動策略升級。

### 美國版資料契約

1. 每個二元市場是一個 instrument；使用 `market_id = polymarket_us:{slug}` 和 `instrument_id = slug`。不能捏造兩個 ERC-1155 token ID。
2. YES bid／ask 是實際 instrument 報價；NO bid = 1 − YES ask，NO ask = 1 − YES bid。NO 委託簿須標記為合成顯示，不得把兩邊深度或成交量重複加總。
3. 歷史 `longPrice`／`shortPrice` 為委託簿衍生的顯示買價，儲存到獨立 `quote_history`。兩者可能合計大於 1；不能強制正規化、當作 mid、逐筆成交或實際成交保證。
4. 30 天預設歷史主要為三小時間隔。自訂時間查詢逐日切分，每次至多 24 小時；原始觀察不一定等間隔。不向前填補缺值來製造分鐘訊號。
5. 未連接需要憑證的成交流時，1／5／15 分鐘成交量及衍生指標應為 `null`，不是 0。`recent_trades_available=false`。
6. 委託簿記錄來源時間與接收時間。外部價格保留 Coinbase `time` 或 Kraken Recent Trades 的交易所時間；未來、過期或幣別不符的觀察不得通過品質檢查。
7. 動能查詢只能使用目標時間之前、設定容忍範圍內的樣本，且外部歷史須來自同一價格供應商。單一絕對報酬率不能假裝成波動度。
8. 美國版流動性門檻使用最優價附近 1% 的雙邊掛單金額，各至少 100 美元（可設定）；metadata 的 `volume24hr` 只保留為供應商欄位與排序用途，未確認單位前不當作逐筆成交美元量。
9. 公開 API 共用節流器，預設每秒 5 個請求；只重試網路暫時故障、429、500／502／503／504。401／403／451 不重試或更換路徑繞過。歷史重複查詢至少快取 30 秒。
10. 美國版與舊國際版資料使用不同識別；新預設資料庫 `data/scanner-us.sqlite3`。舊資料庫可非破壞性新增欄位，原始快照保留；每個 SQLite connection 都啟用外鍵。

最小數量與費用須保留原始 metadata。公開 API 的數量精度與教學文件存在差異，實盤前必須用正式規格確認；本階段不產生可執行數量。費用模型及進出場價格將在 paper sprint 依美國版規則建立。

---

## 3. Core Architecture

```text
Market Discovery
      ↓
Market Data Engine
      ↓
Feature Engine
      ↓
Quant Signal Engine
      ↓
LLM Decision Agent
      ↓
Deterministic Risk Engine
      ↓
Paper / Live Execution Engine
      ↓
Trade Journal
      ↓
Evaluation Engine
      ↓
Strategy Improvement Agent
      ↓
Backtest / Walk-forward / Shadow Validation
      ↓
Promotion Gate
```

---

## 4. Functional Components

## 4.1 Market Discovery

Responsibilities:
- Retrieve active Polymarket markets.
- Filter by category, liquidity, volume, expiry horizon, and market type.
- Identify markets with clear resolution rules.
- Track market metadata, venue, instrument ID, rule hash, and observation deadline.
- Maintain a watchlist.

Minimum filters:
- Crypto-related market
- Active/open market
- Minimum liquidity threshold
- Maximum acceptable bid/ask spread
- Resolution time within configurable horizon
- Sufficient recent trade activity

Output:
```json
{
  "market_id": "string",
  "question": "string",
  "venue": "polymarket_us",
  "instrument_id": "market-slug",
  "contract_type": "touch_above",
  "resolution_time": "timestamp",
  "liquidity": 0,
  "volume_24h": 0,
  "status": "WATCH"
}
```

---

## 4.2 Market Data Engine

Collect:
- YES best bid
- YES best ask
- NO best bid
- NO best ask
- Mid-price
- Spread
- Order-book depth
- Recent trades
- Trade volume
- Time to expiry
- Market metadata
- Resolution rules

External reference data:
- BTC spot
- ETH spot
- Short-horizon price returns
- Volatility
- Optional external exchange order-book data later

Refresh target:
- Configurable
- Initial target: every 5–10 seconds

Persistence:
- Store timestamped snapshots.
- Never overwrite raw snapshots.
- Maintain normalized tables for analysis.
- 長時間收集須使用有界的 capture session。每次使用全新目錄，啟動時保存設定與執行環境，結束時保存停止原因、輪次統計、SQLite 唯讀稽核及所有成品的 SHA-256。
- 已存在的 session 目錄不得重用；正常、部分錯誤及人工中止必須可區分。封存前將 WAL 合併回主資料庫，使單一 SQLite 檔可攜且可驗證。

---

## 4.3 Feature Engine

Initial features:

### Price features
- polymarket_yes_mid
- polymarket_no_mid
- spread
- spread_pct
- price_change_1m
- price_change_5m
- price_change_15m
- price_change_1h
- momentum_5m
- momentum_15m
- momentum_1h

### Volume features
- volume_1m
- volume_5m
- volume_15m
- volume_1h
- volume_velocity
- volume_acceleration
- buy_volume
- sell_volume
- buy_sell_ratio

### Order-book features
- best_bid
- best_ask
- bid_depth_1pct
- ask_depth_1pct
- orderbook_imbalance
- top_level_imbalance
- depth_ratio

Example:

```text
orderbook_imbalance =
bid_depth / (bid_depth + ask_depth)
```

### External market features
- btc_spot
- eth_spot
- return_1m
- return_5m
- return_15m
- realized_volatility
- distance_to_strike
- standardized_distance_to_strike

### Time features
- seconds_to_expiry
- minutes_to_expiry
- market_age
- time_bucket

---

## 4.4 Quant Signal Engine

Purpose:
Estimate a fair probability independently from the Polymarket market price.

Initial model options:
1. Simple parametric probability model
2. Logistic regression
3. Gradient boosting
4. Calibrated classifier
5. Later: ensemble models

Output:
```json
{
  "fair_probability": 0.64,
  "market_probability": 0.56,
  "gross_edge": 0.08,
  "estimated_cost": 0.015,
  "net_edge": 0.065,
  "signal": "YES"
}
```

Primary rule:
The LLM must not invent fair probability without quantitative support.

---

## 4.5 LLM Decision Agent

Possible models:
- Kimi K3
- GPT-5.6
- Other swappable models

Responsibilities:
- Read market question and resolution criteria.
- Review quantitative features and signal.
- Check whether the market logic is internally consistent.
- Detect unusual context or missing information.
- Recommend TRADE or PASS.
- Provide structured rationale.
- Never directly submit an order.

Required structured output:

```json
{
  "decision": "BUY_YES",
  "fair_probability": 0.64,
  "market_probability": 0.56,
  "estimated_edge": 0.08,
  "confidence": 0.76,
  "max_entry_price": 0.58,
  "suggested_position_pct": 0.003,
  "risk_flags": [],
  "reason": "Quantitative edge is positive and market microstructure supports the signal."
}
```

Allowed decisions:
- BUY_YES
- BUY_NO
- PASS
- WATCH

The system must validate the JSON schema before continuing.

---

## 4.6 Deterministic Risk Engine

The LLM cannot override this layer.

Controls:
- Maximum position size
- Maximum market exposure
- Maximum total portfolio exposure
- Maximum daily loss
- Maximum drawdown
- Maximum spread
- Minimum liquidity
- Minimum net edge
- Minimum confidence
- Maximum slippage
- Correlated exposure limit
- Duplicate-order protection
- Stale-data rejection
- Market-close protection
- Cooldown after repeated losses
- Kill switch

Example policy:

```text
if stale_data:
    REJECT

if spread > max_spread:
    REJECT

if net_edge < min_edge:
    REJECT

if daily_loss <= -daily_loss_limit:
    HALT

if proposed_position > allowed_position:
    REDUCE
```

---

## 4.7 Execution Engine

Modes:
1. PAPER
2. SHADOW
3. LIVE_MICRO
4. LIVE

Current implemented mode: READ_ONLY.

When the future execution engine is implemented, its default must be PAPER.

Responsibilities:
- Generate intended order.
- Estimate expected fill.
- Submit limit order in live modes.
- Track partial fills.
- Cancel stale orders.
- Record execution price.
- Estimate slippage.
- Prevent duplicate submissions.

No production live trading should be enabled by default.

---

## 4.8 Trade Journal

Every decision must be logged, including PASS decisions.

Fields:
- timestamp
- market_id
- question
- market_snapshot_id
- feature_snapshot_id
- quant_signal
- LLM decision
- confidence
- predicted fair probability
- market probability
- expected edge
- proposed position
- approved position
- execution price
- fill quantity
- realized outcome
- realized P&L
- slippage
- resolution result
- model version
- strategy version
- agent prompt version
- risk-rule version

Goal:
Full reproducibility of every trade decision.

---

## 4.9 Evaluation Engine

Minimum metrics:

### Prediction
- Accuracy
- Brier score
- Log loss
- Calibration
- Precision by trade direction
- Signal hit rate

### Trading
- Win rate
- Average win
- Average loss
- Expected value
- Realized P&L
- Return on capital
- Maximum drawdown
- Profit factor
- Sharpe-like risk-adjusted metric
- Slippage
- Fill rate

### Agent value-added
Compare:
- Quant model only
- Quant + LLM
- Quant + LLM + microstructure filters

Key question:
Does the LLM improve realized decision quality after transaction costs?

---

## 4.10 Strategy Improvement Agent

Purpose:
Generate hypotheses, not directly modify production.

Workflow:

```text
Trade History
    ↓
Evaluation
    ↓
Hypothesis
    ↓
Candidate Strategy
    ↓
Historical Backtest
    ↓
Walk-forward Test
    ↓
Shadow Trading
    ↓
Performance Gate
    ↓
Promotion
```

Example hypothesis:
> Avoid trades when spread exceeds 0.03 because historical expectancy is negative.

Candidate rule:
```python
if spread > 0.03:
    decision = "PASS"
```

The candidate must pass validation before promotion.

---

## 5. Strategy Promotion Rules

A candidate strategy must:
- Have sufficient sample size.
- Improve at least one target metric.
- Not materially worsen drawdown.
- Pass walk-forward testing.
- Pass shadow trading.
- Be versioned.
- Be reversible.

Production strategy changes must be auditable.

---

## 6. Data Model

Suggested initial tables:

### markets
- market_id
- question
- category
- resolution_time
- venue
- instrument_id
- contract_type
- settlement_time
- rule_hash
- status

### market_snapshots
- snapshot_id
- market_id
- timestamp
- yes_bid
- yes_ask
- no_bid
- no_ask
- volume
- liquidity
- spread

### orderbook_snapshots
- snapshot_id
- market_id
- timestamp
- bid_levels
- ask_levels

### external_prices
- timestamp
- symbol
- venue
- price
- volume

### features
- feature_snapshot_id
- market_id
- timestamp
- computed features

### decisions
- decision_id
- market_id
- timestamp
- quant output
- LLM output
- risk result

### trades
- trade_id
- decision_id
- mode
- intended price
- executed price
- quantity
- status
- P&L

### evaluations
- evaluation_id
- strategy_version
- period
- metrics

### strategy_versions
- version
- description
- parent_version
- status
- created_at

---

## 7. Suggested Repository Structure

```text
polymarket-agent/
│
├── README.md
├── SPEC.md
├── PLAN.md
├── pyproject.toml
├── .env.example
├── config/
│   ├── settings.yaml
│   └── risk.yaml
│
├── src/
│   ├── discovery/
│   ├── data/
│   ├── features/
│   ├── signals/
│   ├── agents/
│   ├── risk/
│   ├── execution/
│   ├── journal/
│   ├── evaluation/
│   └── strategy/
│
├── tests/
│
├── notebooks/
│   ├── exploration.ipynb
│   ├── feature_analysis.ipynb
│   └── backtest.ipynb
│
├── data/
│   ├── raw/
│   ├── processed/
│   └── results/
│
└── scripts/
    ├── run_scanner.py
    ├── run_paper_agent.py
    └── evaluate.py
```

---

## 8. Security Requirements

- Never expose wallet private keys to the LLM.
- Never include secrets in prompts.
- Never commit secrets to Git.
- Store credentials in environment variables or a secret manager.
- Separate read-only data credentials from trading credentials.
- Current scanner mode must be READ_ONLY; future execution mode must default to PAPER.
- Require explicit configuration to enable LIVE_MICRO.
- Maintain a kill switch outside the LLM.
- Log all order submissions and cancellations.

---

## 9. Non-Functional Requirements

### Reliability
- Recover from API disconnects.
- Detect stale WebSocket data.
- Retry transient failures.
- Maintain idempotent order logic.

### Observability
- Structured logs.
- Error logs.
- Strategy version tracking.
- Model version tracking.
- Decision tracing.

### Reproducibility
A historical decision should be reconstructable from stored:
- market data
- features
- prompts
- model version
- risk rules
- strategy version

---

## 10. v1 Success Criteria

Phase 1 is successful when the system can:

1. Discover active crypto-related Polymarket markets.
2. Refresh market data every 5–10 seconds.
3. Store price, spread, book depth and raw order-book data; unavailable trade volume remains null.
4. Join Polymarket markets with external BTC/ETH prices.
5. Compute initial feature set.
6. Generate WATCH / NO_TRADE monitoring labels. Directional signals require the later validated model.
7. Run continuously without a connected wallet.
8. Produce reproducible validation evidence; strategy performance reports belong to the later evaluation sprint.

Phase 2 is successful when:
- Market-specific fair probabilities, deterministic risk checks and realistic paper execution are validated.
- Paper trades are generated before adding an LLM reviewer.
- Decisions are reproducible.
- Quant-only vs Quant+LLM performance can be compared.

Phase 3 is successful when:
- A small, deterministic risk-controlled live execution mode works.
- No LLM has direct access to secrets.
- Kill switch and loss limits are tested.

Phase 4 is successful when:
- Strategy improvement proposals are automatically generated.
- Candidate changes are backtested and shadow-tested.
- No production strategy is changed without passing promotion gates.


## 11. 美國版介接依據（2026-09-08 查核）

- [公開與需驗證 API 的分工](https://docs.polymarket.us/api-reference/introduction)
- [單一 instrument 市場結構](https://docs.polymarket.us/learn/trading/basics/buying-yes-vs-selling-no)
- [市場搜尋](https://docs.polymarket.us/api-reference/search/search)
- [委託簿](https://docs.polymarket.us/api-reference/markets/get-market-book)
- [歷史報價的語意與取樣](https://docs.polymarket.us/api-reference/price-history/get-price-history)
- [美國版費用規則](https://docs.polymarket.us/fees)
- [Coinbase ticker 來源時間](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker)
- [Kraken Recent Trades](https://docs.kraken.com/api-reference/market-data/get-recent-trades)

其餘功能章節描述後續目標；已實作範圍與驗收狀態以第 2 節、PLAN.md 和 IMPLEMENTATION_STATUS.md 為準。


### 診斷補充（2026-09-08）

外部價格驗收紀錄必須包含來源、商品、來源時間、接收時間、檢查時間、資料年齡與門檻；拒絕原因區分 stale、future_timestamp、timeout、transport_error、http_status_error、parse_or_validation_error。被拒絕的可解析價格保留診斷紀錄，不寫入有效外部價格表。原始時間欄位以限制長度的文字記錄供解析錯誤追查，不記錄完整回應或憑證。

HTTP 成功不等同來源時間新鮮。委託簿紀錄需顯示來源時間與接收時間，衍生 NO 標記 synthetic；不以接收時間覆寫來源時間。畫面顯示最優價 1% 範圍內的 YES 買賣掛單美元金額，流動性拒絕理由列出每側門檻。時間與流動性門檻不因本次診斷更新而放寬。


### 市場暫停處理（2026-09-08）

US 委託簿原始狀態保留於 raw_json；正規化為 OPEN、HALTED、CLOSED、UNKNOWN，未知值不得視為開放。HALTED 在即時掃描採 `scanner.halted_recheck_seconds`（預設 60 秒）重新檢查。等待期間回傳 NO_TRADE 與上次確認暫停的說明，保存本輪決策特徵，但不保存虛構行情／委託簿快照；未知價格與深度顯示空值。重新檢查失敗也遵守等待間隔。成功讀取 OPEN 後恢復正常輪詢，最晚在下一次重新檢查察覺恢復。其他非開放狀態仍拒絕訊號。

排程使用 monotonic clock，僅適用未提供固定 as-of 時間的即時掃描。市場離開成功更新的觀察清單時清除對應排程；重新啟動程式會重新讀取狀態。公開搜尋與外部價格仍依原有排程執行。


### 資料稽核及歷史可得時間（2026-09-08）

歷史外部價格查詢同時限制交易所來源時間與 received_at；來源時間早於查詢點但當時尚未收到的價格不得用於計算歷史特徵。FeatureEngine 以當次計算時間 available_at 作為接收截止；單獨呼叫歷史查詢未指定 available_at 時，以查詢 timestamp 為截止。缺少 received_at 的舊紀錄不建立當時可得性假設。

提供不連網的 validate_capture 工具，以 SQLite mode=ro 與 query_only 開啟 schema v2，不升級或修改原始資料；產生 JSON 與 Markdown 報告。包含完整性、外鍵、觀測數、狀態、原生委託簿狀態、時間差、觀測間隔及重疊品質旗標。暫停等待須與實際行情快照分開計數。報告不由 SQLite 推測預期輪數或 HTTP 成功率，不將資料稽核宣稱為策略重播或損益回測。


### 歷史行情品質重播（2026-09-08）

重播 schema v2 的實際 market_snapshots 時點，逐筆依觀測時間排序，不補造未觀測輪數。市場規則版本須 observed_at <= as_of；委託簿須 received_at <= as_of；外部價格同時要求來源時間及接收時間 <= as_of。缺少當時規則或委託簿時輸出 UNDETERMINED，缺少接收時間的輸入列不建立可得性假設並計數。

重播使用當前 FeatureEngine 與分類設定，歷史 mid 由已重播且通過品質檢查的快照逐筆建立，不讀取來源已計算的特徵／分類，不讀取事後 markets 最新規則。每筆事件保留 metadata/book 快照 ID、所用來源／接收時間與重新計算的特徵。記錄市場狀態及規則雜湊轉換、缺口與不可判定情況；這些事件不是實際退場或成交。

價格政策明確標記 latest_received_price_at_recorded_observation：選取當時已收到的最新參考價格，允許沿用直到原有過期檢查拒絕。與即時 scanner 每輪只使用新請求結果的政策不同，因此不能宣稱精確還原原始決策，亦不修改即時 scanner 行為。即使已收到委託簿，來源時間在未來仍會被品質判斷拒絕。

輸出 summary.json、events.jsonl、report.md；不得覆寫既有輸出。輸入唯讀、不連網、不產生訂單、部位、成交、BRTI 標籤或損益。


### 事件分組、時間切分與標籤準備度（2026-09-08）

build_dataset_manifest 以當時可取得的 metadata 快照中的 event.slug 建立事件身分，市場與事件做傳遞合併，涵蓋同事件不同門檻及同市場的事件名稱變更。同一群組不得出現在多個資料集；任一成員缺少當時事件身分則隔離整個連通群組，不用事後事件名稱補填過去。分組可利用完整樣本中的身分關係防止切分洩漏，不能當作預測特徵。

使用者須事先指定含時區的 train-end、validation-end；訓練與驗證的整組觀測時間、最大 resolution_time 均須早於對應截止。每個邊界後預設保留 86400 秒 embargo；跨界或碰到隔離期間的事件整組排除，不隨機拆觀測列。此保守做法可能排除大量中長天期合約，不能為增加資料量而悄悄改變邊界。不同事件仍可能因同屬 BTC 而相關，不宣稱統計獨立。

目前尚未接入經驗證的正式結算或 BRTI 標籤，所有列 label=null、label_status=unverified、eligible_for_supervised_training=false。不得從最後價格、closed、HALTED 或觸價猜測建立 0/1 標籤。清單完成不代表模型訓練準備度通過。


### 公開結算證據（2026-09-08）

新增固定 GET /v1/markets/cpc-btc-{...}/settlement 至公開路徑允許清單；不開放訂單或帳戶端點。保存市場規則回應與 settlement 回應的 canonical JSON、來源 URL、請求／接收時間及 SHA-256。公開存取失敗不得當作 No 結果；401／403／451 不重試或換路徑規避。批次最多 20 個不重複 BTC slug，輸出目錄須為新目錄。

候選結算值需 finite、介於 0 與 1，布林與不合法型別拒絕，非二元值標記特殊結算。商品、規則、hash、來源與時間均驗證；但 hash 不是官方簽章。尚未完成真實已結算 BTC 樣本與特殊條款／結果可得時間驗收，所以即使候選值為 0 或 1 也保持 label=null、unverified，不接入可訓練標籤。詳細依據見 SETTLEMENT_DATA_REVIEW.md。
