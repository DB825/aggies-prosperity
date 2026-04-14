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
