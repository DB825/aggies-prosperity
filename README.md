# Aggies Prosperity

Upload `trader.py` to the Prosperity simulator. The local `datamodel.py` mirrors the
competition interface closely enough for syntax checks and lightweight local tests.

## Round 1 Strategy

- Trades `ASH_COATED_OSMIUM` and `INTARIAN_PEPPER_ROOT` with the 80-unit position
  limits.
- Models pepper root as a slowly growing product with live-book intercept
  calibration and a `0.001 * timestamp` slope.
- Builds a long pepper inventory early, then exits near the end of the day to
  realize the drift.
- Treats osmium as a stationary mean-reversion market around 10,000, with a small
  order-book imbalance adjustment and conservative passive quotes.
- Keeps the research notes in `logs/round1_strategy_findings.ipynb`.

## Diagnostics

Run the dependency-free diagnostics harness with:

```powershell
python scripts\round1_diagnostics.py
python scripts\manual_auction_backtest.py
```

The latest run is saved to `logs/round1_diagnostics.json` and includes:

- Conservative crossing-fill replay: 236,444 XIRECs across the three historical
  days, with flat end-of-day inventory.
- Parameter sensitivity grid: 48/48 tested parameter combinations remained above
  the 200,000 XIREC target.
- Monte Carlo execution stress: 4 chains x 40 draws with 97% crossing-fill
  probability, up to 1 XIREC adverse slippage, and 3 XIREC mark noise.
- Gelman-Rubin R-hat: 1.0000.
- Geweke checks: all chains passed `abs(z) < 2`.
- Anderson-Darling and K-S normality checks: neither rejects the Monte Carlo PnL
  sample at the 5% level.

The manual auction backtest is saved to `logs/manual_auction_backtest.json`. It
exhaustively scans integer bid prices and quantities, confirms the recommended
orders are exact optima under the screenshot order books, and records threshold
checks around the one-unit quantities.
