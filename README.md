# Aggies Prosperity

Tutorial-round Prosperity 4 trading submission for `EMERALDS` and `TOMATOES`.

Upload `trader.py` to the Prosperity simulator. The local `datamodel.py` mirrors the
competition interface closely enough for syntax checks and lightweight local tests.

## Strategy

- Respects the 80-unit position limit for both tutorial products.
- Maintains a compact fair-value estimate in `traderData`.
- Buys asks that are clearly below fair value and sells bids that are clearly above
  fair value.
- Places conservative passive quotes around fair value when there is remaining
  position capacity.
