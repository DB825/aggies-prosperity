# Aggies Prosperity

Upload `trader.py` to the Prosperity simulator. The local `datamodel.py` mirrors the
competition interface closely enough for syntax checks and lightweight local tests.

Research notebooks:

- `algorithmic_model_trading.ipynb`: algorithmic model process, findings,
  backtests, overfit controls, and profitability thesis.
- `manual_auction_strategy.ipynb`: manual auction mechanism, exact optimum, and
  auction backtest.

## Round 1 Strategy

- Trades `ASH_COATED_OSMIUM` and `INTARIAN_PEPPER_ROOT` with the 80-unit position
  limits.
- Models pepper root as a slowly growing product with live-book intercept
  calibration and a `0.001 * timestamp` slope.
- Builds a long pepper inventory early and keeps it through the close to maximize
  marked-to-market drift capture.
- Treats osmium as a stationary mean-reversion market around 10,000, with an
  EMA/imbalance microstructure signal and a looser crossing edge.
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
