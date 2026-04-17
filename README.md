# Aggies Prosperity

Use `trader.py` for the active Prosperity simulator submission. Round 1 is
archived in `trader_round1.py`. The local `datamodel.py` mirrors the competition
interface closely enough for syntax checks and lightweight local tests.

Research notebooks:

- `round2_algorithmic_trading_strategy.ipynb`: round 2 data regime,
  contingent-claims framing, ridge/KNN signal screens, risk controls, and
  strategy replay.
- `round1_algorithmic_model_trading.ipynb`: algorithmic model process, findings,
  backtests, overfit controls, and profitability thesis.
- `round1_manual_auction_strategy.ipynb`: manual auction mechanism, exact
  optimum, and auction backtest.

## Round 2 Strategy

- Active submission file: `trader.py`.
- Uses `data/round2` for the reproducible local research set.
- Trades `ASH_COATED_OSMIUM` and `INTARIAN_PEPPER_ROOT` with the 80-unit
  position limits.
- Models pepper root as a deterministic drift claim with slope
  `0.001 * timestamp`, calibrated from the live day-open intercept, then builds
  to the long 80-unit cap while the intercept remains stable.
- Models osmium as stationary around the 10,000 anchor with short-horizon
  mean-reversion, EMA fair-value smoothing, and best-level order-book imbalance.
- Uses risk gates rather than unconditional averaging down: pepper flattens if
  the live intercept falls more than 35 XIRECs below day-open intercept, and
  osmium flattens if observed fair value moves more than 35 XIRECs from the
  stationary anchor.
- Deterministic local replay over days -1, 0, and 1: +248,673 XIRECs, with
  +238,190 from pepper root and +10,483 from osmium.
- Replay improvement versus archived round 1 parameters on the same round 2
  data: +1,992 XIRECs.
- Execution-stress Monte Carlo, with 97% crossing-fill probability, up to 1
  XIREC adverse slippage, and 3 XIRECs closing mark noise: 5th percentile
  +244,844.4 XIRECs, 85% of draws above +245,000 XIRECs.
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
python scripts\manual_auction_backtest.py
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
