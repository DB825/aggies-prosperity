# Final Results

Final leaderboard snapshot for **Aggies**:

| Metric | Result |
|---|---:|
| Overall placement | #193 |
| Total PnL | 487,287 XIRECs |
| Algorithmic placement | #56 |
| Manual placement | #2838 |
| Country placement | #54 |

## Retrospective

The strongest lesson from the competition was that good non-discretionary
trading is less about having one clever idea and more about repeatedly turning
uncertain markets into explicit rules: fair values, entry thresholds, risk gates,
position limits, and validation that survives new data.

There were real ups and downs across the rounds, including rounds where ideas
went negative. That made the process more useful. The best improvements came
from separating research from live decision rules: build a hypothesis, backtest
it, stress it, and only deploy it when the rule still made sense after costs,
spreads, and position constraints.

Specific strategy themes in this repo:

- Fixed-fair and EMA/order-book-imbalance market making for stationary products.
- Drift/intercept modeling for trend products such as `INTARIAN_PEPPER_ROOT`.
- Manual auction and allocation optimizers where the math was finite and exact.
- Black-Scholes-style voucher valuation with a shrunk live volatility smile,
  residual trading, and adjacent-strike spreads around `VEV_5000 / VEV_5100`.
- Fee-aware news/catalyst sizing for the Ashflow Alpha manual challenge, including
  intentionally leaving weak capital unused when fee drag dominated.
- Round 5 product-by-product signal isolation, selected passive making,
  one-tick regime takers, jump-reversion takers, and strict rejection of ML
  overlays that failed leave-one-day-out validation.

The final placement was a strong finish for the team, especially the #56
algorithmic result, and it leaves a clear checklist for Prosperity 5: stronger
simulation tooling, better post-round attribution, earlier risk-budgeting for
manual challenges, and more disciplined separation between exploratory research
and production-ready non-discretionary rules.
