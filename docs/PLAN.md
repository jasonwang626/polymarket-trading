# Polymarket Autonomous Trading Agent — PLAN

## Goal

Build the system incrementally from a market scanner into a controlled autonomous trading agent.

The development order is intentionally:

```text
Data → Features → Quant Signal → Paper Agent → Evaluation → Micro Live → Self-Improvement
```

not:

```text
LLM → Wallet → Trade
```

---

# Phase 0 — Project Foundation

## Deliverables
- Repository skeleton
- Python environment
- Configuration system
- Logging
- Local database
- Secret handling
- Test framework

## Tasks
- [ ] Create repository structure
- [ ] Configure Python 3.11+
- [ ] Add dependency management
- [ ] Add `.env.example`
- [ ] Add `settings.yaml`
- [ ] Add `risk.yaml`
- [ ] Configure SQLite initially
- [ ] Configure structured logging
- [ ] Add unit test framework
- [ ] Add basic CLI entry points

## Exit criteria
Running:

```bash
python scripts/run_scanner.py
```

starts the application and initializes configuration, logging, and storage.

---

# Phase 1 — Market Scanner

## Objective

Create a read-only Polymarket crypto scanner with no wallet connection.

## Deliverables
- Market discovery
- Market snapshot collection
- Order-book collection
- External BTC/ETH price collection
- Feature computation
- Watchlist ranking

## Tasks

### Polymarket connectivity
- [ ] Connect to current official Polymarket SDK/API
- [ ] Retrieve active markets
- [ ] Retrieve token IDs
- [ ] Retrieve best bid/ask
- [ ] Retrieve order-book depth
- [ ] Retrieve recent trades
- [ ] Retrieve liquidity and volume

### Market filtering
- [ ] Filter crypto markets
- [ ] Filter by resolution horizon
- [ ] Filter by minimum liquidity
- [ ] Filter by maximum spread
- [ ] Filter inactive/closed markets

### External prices
- [ ] Add BTC spot feed
- [ ] Add ETH spot feed
- [ ] Timestamp all external observations
- [ ] Detect stale external prices

### Storage
- [ ] Create `markets`
- [ ] Create `market_snapshots`
- [ ] Create `orderbook_snapshots`
- [ ] Create `external_prices`

### Initial features
- [ ] Mid-price
- [ ] Spread
- [ ] Spread %
- [ ] Volume 1m / 5m / 15m
- [ ] Volume velocity
- [ ] Volume acceleration
- [ ] Order-book imbalance
- [ ] Price momentum 1m / 5m / 15m
- [ ] Time to expiry
- [ ] Distance to strike

### Scanner outputs
- [ ] WATCH
- [ ] POSSIBLE_YES
- [ ] POSSIBLE_NO
- [ ] NO_TRADE

## Exit criteria
Scanner runs continuously for at least one trading session and produces timestamped data without wallet access.

---

# Phase 2 — Quantitative Signal Model

## Objective

Estimate fair probability independently from Polymarket price.

## Tasks
- [ ] Define market-specific target labels
- [ ] Create historical training dataset
- [ ] Build baseline probability model
- [ ] Calibrate probabilities
- [ ] Compute gross edge
- [ ] Estimate transaction costs
- [ ] Compute net edge
- [ ] Create signal threshold rules
- [ ] Backtest baseline strategy

## Baseline comparisons
- [ ] Always PASS
- [ ] Market probability only
- [ ] Momentum only
- [ ] Quant model
- [ ] Quant model + microstructure filter

## Exit criteria
Quant strategy produces reproducible historical signals with evaluation metrics.

---

# Phase 3 — LLM Agent

## Objective

Add an LLM as a decision-review layer rather than the primary predictive model.

## Tasks
- [ ] Define agent system prompt
- [ ] Define JSON output schema
- [ ] Add schema validation
- [ ] Provide market question
- [ ] Provide resolution criteria
- [ ] Provide quantitative signal
- [ ] Provide selected feature snapshot
- [ ] Provide recent trade context
- [ ] Add PASS / WATCH / BUY_YES / BUY_NO decisions
- [ ] Version prompts
- [ ] Version model configuration

## Model comparison
Test:
- [ ] Kimi K3
- [ ] GPT-5.6
- [ ] Optional additional models

Measure:
- latency
- cost
- JSON compliance
- decision consistency
- incremental value over quant-only

## Exit criteria
Agent produces structured decisions without direct wallet or secret access.

---

# Phase 4 — Paper Trading Engine

## Objective

Simulate real execution realistically.

## Tasks
- [ ] Simulated order placement
- [ ] Bid/ask-aware fills
- [ ] Partial-fill simulation
- [ ] Slippage estimation
- [ ] Position tracking
- [ ] Portfolio tracking
- [ ] Market resolution
- [ ] Realized P&L
- [ ] Daily P&L report

## Journal
Record every:
- signal
- agent decision
- risk decision
- simulated order
- fill
- outcome

## Exit criteria
The system can run end-to-end unattended in paper mode.

---

# Phase 5 — Evaluation Framework

## Objective

Determine whether the agent actually adds value.

## Comparison arms

### A
Quant only

### B
Quant + LLM

### C
Quant + LLM + order-book filter

### D
Quant + LLM + order-book + volume acceleration

## Metrics
- [ ] Accuracy
- [ ] Brier score
- [ ] Calibration
- [ ] Win rate
- [ ] Expected value
- [ ] Realized P&L
- [ ] Maximum drawdown
- [ ] Profit factor
- [ ] Slippage
- [ ] Fill rate
- [ ] Opportunity cost of PASS
- [ ] LLM incremental value

## Exit criteria
A daily and cumulative evaluation report can identify whether the LLM and each feature family improve performance.

---

# Phase 6 — Risk Engine

## Objective

Implement deterministic controls before any live trading.

## Tasks
- [ ] Maximum trade size
- [ ] Maximum market exposure
- [ ] Maximum portfolio exposure
- [ ] Maximum daily loss
- [ ] Maximum drawdown
- [ ] Maximum spread
- [ ] Minimum liquidity
- [ ] Minimum edge
- [ ] Minimum confidence
- [ ] Correlated position limits
- [ ] Stale-data rejection
- [ ] Duplicate-order protection
- [ ] Cooldown
- [ ] Kill switch

## Exit criteria
Risk engine can reject or reduce orders independently of the LLM.

---

# Phase 7 — Shadow Mode

## Objective

Use live market data and real intended orders without sending them.

## Tasks
- [ ] Generate real-time intended orders
- [ ] Record exact intended execution time
- [ ] Track hypothetical fill
- [ ] Compare intended vs achievable fill
- [ ] Evaluate latency
- [ ] Validate live-data reliability

## Exit criteria
Shadow results closely match expected execution assumptions.

---

# Phase 8 — Micro Live Trading

## Objective

Enable tightly controlled live execution.

## Initial constraints
Suggested starting mode:
- very small capital
- limit orders only
- strict daily loss cap
- one market at a time
- no strategy auto-modification

## Tasks
- [ ] Trading credential separation
- [ ] Order submission
- [ ] Cancel/replace
- [ ] Partial fills
- [ ] Portfolio reconciliation
- [ ] Kill switch
- [ ] Live monitoring
- [ ] Post-trade reconciliation

## Exit criteria
Small live trades execute correctly and reconcile with recorded state.

---

# Phase 9 — Strategy Improvement Agent

## Objective

Allow the system to propose improvements without direct production modification.

## Tasks
- [ ] Daily strategy review
- [ ] Failure pattern detection
- [ ] Feature attribution
- [ ] Generate strategy hypotheses
- [ ] Generate candidate rule changes
- [ ] Generate candidate parameter changes
- [ ] Version candidate strategy
- [ ] Automatic backtest

## Example

Observation:

```text
Trades with spread > 0.03 have negative expectancy.
```

Candidate:

```python
if spread > 0.03:
    decision = "PASS"
```

Then:

```text
Backtest
   ↓
Walk-forward
   ↓
Shadow
   ↓
Promotion decision
```

## Exit criteria
Agent can propose and test strategies without directly modifying production.

---

# Phase 10 — Controlled Autonomous Improvement

## Objective

Automate promotion only after strong evidence.

## Promotion gate

A candidate must meet all configured requirements, for example:
- minimum sample size
- positive out-of-sample expectancy
- acceptable maximum drawdown
- no major degradation in calibration
- successful shadow performance
- reproducibility
- rollback capability

## Required safeguards
- immutable strategy history
- production rollback
- model version logging
- prompt version logging
- rule version logging
- promotion audit trail

---

# Recommended First Development Sprint

## Sprint 1

Build only:

```text
Market Discovery
      ↓
Market Data
      ↓
External BTC/ETH Data
      ↓
Feature Engine
      ↓
Scanner
```

### Sprint 1 files

```text
src/
├── discovery/
│   └── polymarket.py
├── data/
│   ├── polymarket_feed.py
│   ├── crypto_feed.py
│   └── storage.py
├── features/
│   └── market_features.py
└── scanner/
    └── scanner.py

scripts/
└── run_scanner.py
```

### Sprint 1 console output

Example:

```text
BTC Up or Down — 15m

YES       0.56
NO        0.44

Spread    0.02
Volume5m  $18,430
VolAccel  2.8x
OB Imbal  0.67
BTC 5m    +0.42%

Signal:
POSSIBLE_YES
```

No LLM.
No wallet.
No order submission.

---

# Recommended Technology Stack

## Core
- Python 3.11+
- asyncio
- pandas or polars
- pydantic
- SQLite initially
- PostgreSQL later if needed

## Modeling
- scikit-learn
- scipy
- lightgbm/xgboost later if justified

## LLM
Swappable provider interface:
- Kimi K3
- GPT-5.6

## Monitoring
Initial:
- console + structured logs

Later:
- dashboard
- alerts
- trade/evaluation UI

---

# Immediate Next Step

Implement **Sprint 1** before adding the LLM.

Definition of done:

```text
The application can continuously scan selected crypto Polymarket markets,
combine Polymarket and BTC/ETH market data,
calculate volume, price, spread, and order-book features,
store observations,
and classify each market as:

WATCH
POSSIBLE_YES
POSSIBLE_NO
NO_TRADE
```

Once that works reliably, move to the quantitative probability model.
