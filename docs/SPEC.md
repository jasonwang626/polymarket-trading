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

### In scope for v1

Focus on liquid, short-horizon crypto-related Polymarket markets where an external reference price is available.

Examples:
- BTC above/below a threshold by a specified time
- ETH above/below a threshold by a specified time
- BTC up/down markets
- Other crypto event contracts with clearly defined resolution conditions

### Out of scope for v1

- Politics
- Elections
- Geopolitical events
- Illiquid long-duration markets
- Fully autonomous live strategy modification
- Leverage
- External borrowing
- Cross-exchange arbitrage requiring capital movement between venues

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
- Track market metadata and token IDs.
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
  "yes_token_id": "string",
  "no_token_id": "string",
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

Default:
PAPER

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
- yes_token_id
- no_token_id
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
- Default execution mode must be PAPER.
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
3. Store price, spread, liquidity, volume, and order-book data.
4. Join Polymarket markets with external BTC/ETH prices.
5. Compute initial feature set.
6. Generate WATCH / POSSIBLE_YES / POSSIBLE_NO / NO_TRADE signals.
7. Run continuously without a connected wallet.
8. Produce a daily evaluation report.

Phase 2 is successful when:
- The LLM decision layer runs on top of quantitative signals.
- Paper trades are generated.
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
