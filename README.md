# Aggies Prosperity

Use `trader.py` for the active Prosperity simulator submission. Round 2 is
archived in `trader_round2.py`, and Round 1 is archived in `trader_round1.py`.
The local `datamodel.py` mirrors the competition interface closely enough for
syntax checks and lightweight local tests.

Research notebooks:

- `round3_algorithmic_options_strategy.ipynb`: Round 3 Solvenar workflow,
  Hydrogel/Velvetfruit microstructure diagnostics, voucher volatility
  calibration, external Prosperity references, and replay summary.
- `round2_algorithmic_trading_strategy.ipynb`: round 2 data regime,
  contingent-claims framing, ridge/KNN signal screens, risk controls, and
  strategy replay.
- `round2_manual_invest_expand_strategy.ipynb`: 50,000 XIREC Research/Scale/Speed
  allocation optimizer, Speed-rank game theory, and submission recommendation.
- `round1_algorithmic_model_trading.ipynb`: algorithmic model process, findings,
  backtests, overfit controls, and profitability thesis.
- `round1_manual_auction_strategy.ipynb`: manual auction mechanism, exact
  optimum, and auction backtest.

## Round 3 Strategy

- Active submission file: `trader.py`.
- Trades `HYDROGEL_PACK`, `VELVETFRUIT_EXTRACT`, and the 10
  `VELVETFRUIT_EXTRACT_VOUCHER` contracts with their posted position limits.
- Treats Hydrogel as a stationary market around the 10,000 anchor, using a slow
  EMA, L1 imbalance adjustment, and wide crossing thresholds to avoid getting
  chopped by noise.
- Treats Velvetfruit Extract as a slower mean-reversion product and only crosses
  when dislocations are meaningfully larger than the typical spread.
- Prices the liquid vouchers with Black-Scholes fair values using strike-specific
  volatility anchors calibrated from the historical TTE 8/7/6-day chain, then
  rolls that calibration forward to live TTE 5 days.
- Keeps the deep ITM vouchers (`VEV_4000`, `VEV_4500`) on an intrinsic-value
  style leash because their extrinsic value is tiny relative to spread noise.
- Controls voucher inventory with internal position caps and a portfolio delta
  budget rather than expensive full delta hedging through the underlying.
- Deterministic crossing replay over historical days 0, 1, and 2: +228,940.0
  XIRECS, with +131,402.0 from Hydrogel, +44,777.5 from Velvetfruit Extract,
  and +52,760.5 from the voucher sleeve.
- Metrics snapshot: `logs/round3_diagnostics.json`.

## Round 2 Manual Challenge

- Recommended submission: Research 16%, Scale 49%, Speed 35%.
- Uses the full 50,000 XIREC budget.
- Continuous optimum conditional on 35% Speed: Research 16.17%, Scale 48.83%,
  Speed 35.00%; the integer submission is easier and nearly identical.
- The 35% Speed allocation is chosen to sit above common integer-balanced
  `33/33/34` submissions while preserving enough budget for the deterministic
  Research/Scale engine.
- The allocation clears 200,000 XIRECs if it beats at least 61.7% of Speed
  investments; at the 70th Speed percentile it scores about 227,948 XIRECs, and
  at top rank it scores about 329,021 XIRECs.
- Metrics snapshot: `logs/round2_manual_invest_expand.json`.

## Round 2 Strategy

- Active submission file: `trader.py`.
- Uses `data/round2` for the reproducible local research set.
- Bids 825 XIRECs for Market Access Fee coverage. The bid is above the common
  placeholder/example range while staying close to the measured one-day marginal
  value of the extra 25% book access in local volume-sensitivity checks.
- Trades `ASH_COATED_OSMIUM` and `INTARIAN_PEPPER_ROOT` with the 80-unit
  position limits.
- Models pepper root as a deterministic drift claim with slope
  `0.001 * timestamp`, calibrated from the live day-open intercept, then builds
  to the long 80-unit cap while the intercept remains stable.
- Models osmium as stationary around the 10,000 anchor with short-horizon
  mean-reversion, EMA fair-value smoothing, capped best-level order-book
  imbalance, active crossing inventory skew, and a crowded-inventory gate that
  suppresses imbalance when it would add to an already large position.
- Uses risk gates rather than unconditional averaging down: pepper flattens if
  the live intercept falls more than 35 XIRECs below day-open intercept, and
  osmium flattens if observed fair value moves more than 35 XIRECs from the
  stationary anchor.
- Deterministic local replay over days -1, 0, and 1: +249,317 XIRECs, with
  +238,195 from pepper root and +11,122 from osmium. A trade-print-aware replay
  reaches +249,352 XIRECs.
- Replay improvement versus archived round 1 parameters on the same round 2
  data: +2,636 XIRECs, and +33 XIRECs versus the restored second Round 2
  iteration in the quote-only replay.
- Execution-stress Monte Carlo, with 97% crossing-fill probability, up to 1
  XIREC adverse slippage, and 3 XIRECs closing mark noise: 5th percentile
  +245,406.2 XIRECs, 100% of draws above +245,000 XIRECs.
- Metrics snapshot: `logs/round2_diagnostics.json`.

## Round 1 Results

- Overall position: 1359.
- Previous total: 0 XIRECs.
- Algorithmic trading: +94,022.5625 XIRECs, displayed as +94,023, with round
  ranking 2046th.
- Manual trading: +87,995.10 XIRECs, displayed as +87,995, with round ranking
  1st.
- Round 1 total: +182,017.6625 XIRECs, displayed as 182,018.
- New total PnL: 182,018 XIRECs.
- Mission progress: 91%.
- Badges unlocked: 5.
- Manual orders: buy 9,999 `DRYLAND_FLAX` at +30 for +9,999 PnL; buy 19,999
  `EMBER_MUSHROOM` at +17 for +77,996.10 PnL.
- Metrics snapshot: `logs/round1_metrics.md`.

## Round 1 Strategy

- Trades `ASH_COATED_OSMIUM` and `INTARIAN_PEPPER_ROOT` with the 80-unit position
  limits.
- Models pepper root as a slowly growing product with live-book intercept
  calibration and a `0.001 * timestamp` slope.
- Builds a long pepper inventory early and keeps it through the close to maximize
  marked-to-market drift capture.
- Treats osmium as a stationary mean-reversion market around 10,000, with an
  EMA/imbalance microstructure signal and a looser crossing edge.
- Includes model-based stop-loss gates: pepper de-risks if the live drift
  intercept breaks below the day-open intercept, and osmium de-risks if it leaves
  the stationary anchor regime.
- Keeps the research notes in `logs/round1_strategy_findings.ipynb`.

## Diagnostics

Run the dependency-free diagnostics harness with:

```powershell
python scripts\round1_diagnostics.py
python scripts\round2_diagnostics.py --mc-chains 1 --mc-draws 40
python scripts\round3_diagnostics.py
python scripts\manual_auction_backtest.py
python scripts\build_round3_notebook.py
```

The latest run is saved to `logs/round1_diagnostics.json` and includes:

- Profit-first crossing-fill replay: 249,384.5 XIRECs across the three historical
  days, 49,384.5 above the 200,000 XIREC floor.
- Parameter sensitivity grid: 144/144 tested parameter combinations remained
  above the 200,000 XIREC target.
- Selection protocol: candidates are gated by a train-only robust score on days
  -2 and -1, then the highest-PnL row inside that train-validated plateau is
  selected; day 0 is reported as the local holdout stress check.
- Selected parameter set: rank 2/144 by train-only robust score, rank 1/144 by
  all-days combined PnL, with 83,273 XIRECs on holdout day 0.
- Lightweight linear signal screen: osmium day-0 holdout MSE improves from 8.772
  to 7.301, with 70.54% next-tick directional accuracy.
- Risk-control checks: the pepper trend-break and osmium anchor-break guards had
  zero historical triggers, so they protect live regime breaks without reducing
  the normal-path backtest.
- Monte Carlo execution stress: 4 chains x 40 draws with 97% crossing-fill
  probability, up to 1 XIREC adverse slippage, and 3 XIREC mark noise.
- Gelman-Rubin R-hat: 1.0000.
- Geweke checks: all chains passed `abs(z) < 2`.
- Monte Carlo stressed 5th percentile: 245,764.4 XIRECs, with 100% of draws above
  245,000.
- Anderson-Darling and K-S normality checks: neither rejects the Monte Carlo PnL
  sample at the 5% level.

The manual auction backtest is saved to `logs/manual_auction_backtest.json`. It
exhaustively scans integer bid prices and quantities, confirms the recommended
orders are exact optima under the screenshot order books, and records threshold
checks around the one-unit quantities.
