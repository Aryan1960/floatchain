# FloatChain

A trade-up contract optimizer for Counter-Strike 2 (CS2) skins: the math engine, ML pricing model, and decision layer for a real player-driven marketplace, built and evaluated against live data rather than a toy dataset.

## The idea, in plain terms

If you're not into CS2: think of it like a structured swap market. Players can combine ten lower-tier cosmetic items ("skins") into a contract that returns one random higher-tier item, similar to pooling ten shares of a small-cap stock for a shot at one share of something in the next market-cap bracket up. Which specific item comes out, and its exact condition, is determined by a "float" value each input carries, a continuous number from 0 to 1 that's effectively a wear/condition score. Pricing that outcome correctly, and deciding whether the contract is even worth running, isn't obvious up front. That's what this project answers, with real math and a real trained pricing model instead of a guess.

The project is explicit that the trading strategy itself is likely breakeven-to-loss in practice; its actual value is as an engineering piece: a correct math engine, a pricing model that gets independently benchmarked (not just fit and trusted), and a decision layer that's honest when the numbers say "don't build the fancy version."

## Architecture

Three layers, each gating the next rather than being built speculatively up front.

### Layer 1: Math engine

Pure, dependency-free Python implementing the actual float mechanics:

- **Adjusted float**: each input's float is normalized within its own skin's float-cap range (`(raw_float - min_float) / (max_float - min_float)`), averaged across all inputs, then rescaled into the output skin's own float-cap range. Not a raw arithmetic mean, an adjusted one, matching the real post-2026 formula.
- **Collection/probability weighting**: each input contributes an equal share (1/10 normally, 1/5 for a 5-input Covert-to-Gold contract) toward which collection the output is drawn from; every eligible output skin within that collection is then equally likely.
- **Rarity tiers**: 7-tier ladder (Consumer through Extraordinary/Gold); a contract needs 10 inputs normally, or 5 for a Covert-to-Gold (knife/glove) contract.
- **Wear thresholds**: Factory New < 0.07, Minimal Wear < 0.15, Field-Tested < 0.38, Well-Worn < 0.45, Battle-Scarred above that.
- **Monte Carlo simulation** (1,000 runs by default) over the full outcome distribution for a realistic profit/breakeven/loss spread, not just a point estimate.

### Layer 2: ML pricing

There's no persistence layer or historical sales data handed to this project. A scheduled collector polls a live marketplace API hourly, storing real `(float, price)` observations per skin, and everything downstream is trained on that, not synthetic data.

- **XGBoost** for skins with enough data (50+ clean points), **KNN** as a sparse-data fallback (5-49 points), and an honest "not enough data" response below that, never a silent guess.
- Every prediction is benchmarked against the marketplace's own float-aware price estimate, not evaluated in isolation. Beating that estimate, not just fitting a curve, is the actual bar.
- **Outlier filtering**: listings get judged against their nearest real neighbors by float, not the whole skin's price distribution, so a genuine second price tier (some items are worth a real premium at extreme wear) doesn't get discarded as noise alongside actual junk. Getting this right took two iterations, the first attempt correctly caught real contamination but also incorrectly discarded a legitimate price regime, caught and fixed by checking against multiple known real cases.
- **Deal radar**: flags currently-stored listings priced meaningfully below the fitted curve as potential mispricings, using a multi-armed bandit to spend a small, limited budget of live verification calls on the skins most likely to yield a real, still-available discount.

Building this pipeline against real data surfaced genuine data-quality bugs along the way: sticker/decoration value riding along on listing prices with nothing to do with the skin's float, a structural sampling flaw in the collector that left parts of some skins' float ranges permanently unsampled, and the anomaly-detection issue above. Each was diagnosed against live data, not assumed.

### Layer 3: Chain-search decision layer

A classical, non-learned beam search asking "sell this output now, or feed it into another contract?", built specifically as a gate: a reinforcement-learning approach (PPO + GNN encoder) was scoped but explicitly not built unless this classical layer showed a real, measured edge to chaining.

A live-data backtest across real tracked skins found chaining never beat selling immediately (0 wins across every tested sample, before and after marketplace fees), because funding one unit of the next tier costs roughly 10 units of the current one, and no tested branch's output was worth close to that multiple. That's a clean negative result, not a data gap, so the RL/GNN layer stayed unbuilt. A negative result, reached honestly, is still a result.

## Tech stack

```
Backend:              Python, FastAPI, Uvicorn, httpx, Pydantic
Database:              SQLite
ML / Data Science:     XGBoost, scikit-learn, NumPy, pandas, joblib
Frontend:              React, TypeScript, Vite
Algorithms:            Monte Carlo simulation, beam search with backward induction,
                        local-neighborhood robust-statistics outlier detection,
                        multi-armed bandit (UCB1)
External data:         CSFloat API (live listings), ByMykel/CSGO-API (item catalog)
Testing:                Pytest, pytest-asyncio
```

## API

```
POST /api/contracts/evaluate      run a single trade-up contract end to end
POST /api/chains/evaluate         evaluate a contract plus the chain-search decision
GET  /api/pricing/predict         a price prediction for one skin/float
GET  /api/pricing/curve           the fitted price curve + underlying real data, for charting
GET  /api/pricing/anomalies       currently-flagged mispriced listings for a skin
GET  /api/pricing/status          data collection progress, per skin
GET  /api/pricing/eval-history    model accuracy over time
GET  /api/pricing/deals           live deal-radar candidates
GET  /api/skins/search            catalog search/autocomplete
GET  /api/skins/price             live price lookup for a skin/float
```

## Running it

```bash
# backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in CSFLOAT_API_KEY
.venv/bin/uvicorn app.main:app --reload --port 8000

# frontend (separate terminal)
cd frontend
npm install
npm run dev   # proxies to the backend on :8000
```

The UI has three views.

**Calculator**: build a 5- or 10-input trade-up contract with skin autocomplete (shows the collection and enforces the rarity-tier/StatTrak matching rule), a float slider with manual entry, a live wear badge, and an out-of-range warning if a float falls outside that skin's real cap. Market price autofills from the live pricing endpoint with manual override. Results show the priced outcome distribution as stat tiles plus a profit/breakeven/loss bar from the Monte Carlo run and a full outcomes table.

**Pricing insights**: a stat strip (real listings collected, skins tracked, collector status) and a headline "beats the marketplace's own estimate on X/Y skins" readout, backed by an accuracy trend chart pulling from every scheduled evaluation run, not just the latest one. Pick any tracked skin and float to get a live prediction, shown side by side with the marketplace's own estimate. Below that, a hand-built SVG price curve chart plots every real listing collected for that skin against the fitted model curve and the marketplace's estimate, with a live crosshair, hover tooltips, and toggles for real listings, excluded outliers, and the marketplace overlay independently. If a prediction is extrapolating across a real gap in the collected data, a warning says so explicitly instead of looking falsely confident. Underneath the chart, an "inside the model" panel renders one actual tree from that skin's fitted XGBoost model, split thresholds and all, not a mockup.

**Deal radar**: a table of currently-flagged real listings priced meaningfully below the fitted curve, each with a confidence count (how much real data backs that flag) and a "verified live" badge for the handful re-checked against the marketplace directly. Clicking a row expands that skin's own price curve inline, with the flagged listing marked exactly where it sits, connected by a line back to what the curve says it should cost, so the discount is something you can see, not just a percentage. Refreshes automatically in step with the collector's own schedule.

## What's deliberately not here

An RL/GNN chain optimizer, gated behind Layer 3's classical search proving real value first, which it didn't. Building it anyway would have been complexity added without justification, so it wasn't built. The feasibility study behind this project called that outcome plausible from the start; the honest version of a portfolio project is one that's willing to report a negative result instead of quietly building the impressive-sounding thing regardless of what the data said.
