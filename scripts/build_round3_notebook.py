import json
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_PATH = ROOT / "round3_algorithmic_options_strategy.ipynb"
DIAGNOSTICS_PATH = ROOT / "logs" / "round3_diagnostics.json"


def markdown_cell(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": textwrap.dedent(source).strip("\n").splitlines(keepends=True),
    }


def code_cell(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": textwrap.dedent(source).strip("\n").splitlines(keepends=True),
    }


def build_notebook() -> dict:
    diagnostics = {}
    if DIAGNOSTICS_PATH.exists():
        diagnostics = json.loads(DIAGNOSTICS_PATH.read_text())

    combined_pnl = diagnostics.get("combined_pnl")
    day_lines = []
    for day in diagnostics.get("day_results", []):
        day_lines.append(
            f"- Historical day {day['day']} (TTE={day['tte_days']}d): "
            f"{day['total_pnl']:.1f} XIRECS, ending option delta {day['ending_option_delta']:.1f}"
        )

    product_totals = diagnostics.get("per_product_totals", {})
    option_total = sum(value for key, value in product_totals.items() if key.startswith("VEV_"))
    product_block = "Diagnostics not generated yet."
    if product_totals:
        product_block = "\n".join(
            [
                f"- `HYDROGEL_PACK`: {product_totals['HYDROGEL_PACK']:.1f} XIRECS",
                f"- `VELVETFRUIT_EXTRACT`: {product_totals['VELVETFRUIT_EXTRACT']:.1f} XIRECS",
                f"- Voucher sleeve: {option_total:.1f} XIRECS",
            ]
        )

    summary_block = "Diagnostics not generated yet. Run `python scripts\\\\round3_diagnostics.py` first."
    if combined_pnl is not None:
        summary_block = "\n".join(
            [f"- Deterministic crossing replay: {combined_pnl:.1f} XIRECS"] + day_lines
        )

    cells = [
        markdown_cell(
            f"""
            # Round 3: Solvenar Options Strategy

            This notebook institutionalizes the latest Round 3 research flow for
            Solvenar's `HYDROGEL_PACK`, `VELVETFRUIT_EXTRACT`, and
            `VELVETFRUIT_EXTRACT_VOUCHER` products.

            ## Working Summary

            {summary_block}

            ## Current PnL Mix

            {product_block}

            ## Latest Model

            - `HYDROGEL_PACK`: stationary microstructure market around the 10,000
              anchor, traded with slow EMA fair value, L1 imbalance adjustment, and
              wide edges.
            - `VELVETFRUIT_EXTRACT`: conservative mean reversion plus partial hedge
              of the voucher sleeve.
            - Core vouchers `VEV_5000` to `VEV_5500`: priced off a shrunk live smile,
              then traded on residual z-scores rather than raw one-vol Black-Scholes
              mispricing.
            - Options execution: adjacent-strike spread trades first, then selective
              single-name trades with `VEV_5000` treated as the strongest residual
              signal.
            - Risk: smaller internal voucher caps, option concentration limits,
              option-delta budget, and persistent drawdown / reduce-only controls.
            """
        ),
        markdown_cell(
            """
            ## Foundational Theory References

            The notebook now cites the classical options papers that anchor the
            current trader design:

            - Black, Fischer, and Myron Scholes (1973), *The Pricing of Options and
              Corporate Liabilities*. Citation context: [Stanford GSB Nobel archive](https://www.gsb100.stanford.edu/stories/professor-myron-scholes-shares-1997-nobel-prize-in-economic-science/)
            - Merton, Robert C. (1973), *Theory of Rational Option Pricing*. Citation
              page: [Harvard Business School](https://www.hbs.edu/faculty/Pages/item.aspx?num=8804)
            - Cox, John C., Stephen A. Ross, and Mark Rubinstein (1979),
              *Option Pricing: A Simplified Approach*. Citation / metadata:
              [IDEAS RePEc](https://ideas.repec.org/a/eee/jfinec/v7y1979i3p229-263.html)
            - Dumas, Bernard, Jeff Fleming, and Robert E. Whaley (1996/1998),
              *Implied Volatility Functions: Empirical Tests*. Working paper:
              [NBER](https://www.nber.org/papers/w5500)

            These papers matter for different reasons:

            - Black-Scholes-Merton gives the no-arbitrage benchmark, plus closed-form
              price and delta.
            - CRR gives the clean discrete-time replication intuition, which is useful
              because our Prosperity environment is discrete, spread-constrained, and
              inventory-limited.
            - Dumas-Fleming-Whaley is the reminder that constant-vol Black-Scholes is
              not literally correct in markets with a smile; that is why the latest
              trader uses a shrunk live smile instead of a single static sigma.
            """
        ),
        markdown_cell(
            """
            ## Is Black-Scholes Applicable Here?

            Short answer: yes as a benchmark, no as a full literal model.

            Why it is applicable:

            - The vouchers behave like short-dated European calls with fixed strike
              and expiry.
            - We need a common fair-value language across strikes, and
              Black-Scholes-Merton gives that in a transparent way.
            - Delta from the model is still useful for controlling directional risk.

            Why it is not the whole story:

            - The observed chain has strike-dependent implied vol, so one constant
              sigma is too crude.
            - Trading is discrete and inventory-constrained; there is no continuous
              hedging.
            - Deep ITM / deep OTM strikes can have unstable implied vols when
              extrinsic value gets tiny relative to the spread.

            Practical conclusion:

            - Use Black-Scholes as the first-order no-arbitrage anchor.
            - Fit a light smile on top of it.
            - Trade residuals and relative-value spreads, not naive one-vol signals.
            """
        ),
        markdown_cell(
            """
            ## External Prosperity References

            The Round 3 design still borrows the strongest recurring ideas from
            successful Prosperity writeups:

            - [TimoDiehm/imc-prosperity-3](https://github.com/TimoDiehm/imc-prosperity-3):
              IV scalping plus lightweight hedge discipline.
            - [CarterT27/imc-prosperity-3](https://github.com/CarterT27/imc-prosperity-3):
              Black-Scholes pricing, rolling vol estimates, and cross-voucher checks.
            - [chrispyroberts/imc-prosperity-3](https://github.com/chrispyroberts/imc-prosperity-3):
              smile fitting in moneyness space.
            - [JamesCole809/IMC-Prosperity-3](https://github.com/JamesCole809/IMC-Prosperity-3):
              caution on deep ITM implied-vol instability.

            The current Solvenar trader keeps that spirit but deliberately stops short
            of a fully reactive smile fit to reduce overfitting risk.
            """
        ),
        code_cell(
            """
            import csv
            import json
            import math
            import statistics
            from collections import defaultdict
            from pathlib import Path

            ROOT = Path.cwd()
            DATA_DIR = ROOT / "data" / "round3"
            TTE_BY_DAY = {0: 8.0, 1: 7.0, 2: 6.0}
            STRIKES = {f"VEV_{k}": k for k in [4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500]}

            def load_rows():
                by_product = defaultdict(lambda: defaultdict(list))
                by_day_ts = defaultdict(lambda: defaultdict(dict))
                for path in sorted(DATA_DIR.glob("prices_round_3_day_*.csv")):
                    with path.open(newline="") as file:
                        for row in csv.DictReader(file, delimiter=";"):
                            product = row["product"]
                            day = int(row["day"])
                            timestamp = int(row["timestamp"])
                            parsed = {
                                "product": product,
                                "day": day,
                                "timestamp": timestamp,
                                "mid_price": float(row["mid_price"]),
                            }
                            for level in (1, 2, 3):
                                for side in ("bid", "ask"):
                                    parsed[f"{side}_price_{level}"] = (
                                        float(row[f"{side}_price_{level}"]) if row[f"{side}_price_{level}"] else None
                                    )
                                    parsed[f"{side}_volume_{level}"] = (
                                        float(row[f"{side}_volume_{level}"]) if row[f"{side}_volume_{level}"] else None
                                    )
                            by_product[product][day].append(parsed)
                            by_day_ts[day][timestamp][product] = parsed
                return by_product, by_day_ts

            by_product, by_day_ts = load_rows()
            sorted(by_product)
            """
        ),
        code_cell(
            """
            def product_summary(product):
                rows = []
                for day in sorted(by_product[product]):
                    rows.extend(by_product[product][day])
                mids = [row["mid_price"] for row in rows]
                spreads = [
                    row["ask_price_1"] - row["bid_price_1"]
                    for row in rows
                    if row["ask_price_1"] is not None and row["bid_price_1"] is not None
                ]
                returns = [mids[i + 1] - mids[i] for i in range(len(mids) - 1)]
                return {
                    "rows": len(rows),
                    "mean_mid": round(statistics.mean(mids), 3),
                    "stdev_mid": round(statistics.pstdev(mids), 3),
                    "mean_spread": round(statistics.mean(spreads), 3),
                    "return_stdev": round(statistics.pstdev(returns), 3),
                    "open": mids[0],
                    "close": mids[-1],
                }

            summaries = {
                product: product_summary(product)
                for product in ["HYDROGEL_PACK", "VELVETFRUIT_EXTRACT", "VEV_5000", "VEV_5100", "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"]
            }
            summaries
            """
        ),
        code_cell(
            """
            def norm_cdf(x):
                return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

            def bs_call(spot, strike, tte, sigma):
                if sigma <= 1e-9 or tte <= 0:
                    return max(0.0, spot - strike)
                vol_term = sigma * math.sqrt(tte)
                d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tte) / vol_term
                d2 = d1 - vol_term
                return spot * norm_cdf(d1) - strike * norm_cdf(d2)

            def implied_vol(price, spot, strike, tte):
                intrinsic = max(0.0, spot - strike)
                extrinsic = price - intrinsic
                if extrinsic <= 0.75:
                    return None
                lo, hi = 1e-6, 0.10
                for _ in range(80):
                    mid = (lo + hi) / 2
                    if bs_call(spot, strike, tte, mid) > price:
                        hi = mid
                    else:
                        lo = mid
                return (lo + hi) / 2

            iv_summary = {}
            for symbol, strike in STRIKES.items():
                if strike in (6000, 6500):
                    continue
                values = []
                for day in sorted(by_day_ts):
                    tte = TTE_BY_DAY[day]
                    for timestamp in sorted(by_day_ts[day]):
                        spot = by_day_ts[day][timestamp]["VELVETFRUIT_EXTRACT"]["mid_price"]
                        price = by_day_ts[day][timestamp][symbol]["mid_price"]
                        sigma = implied_vol(price, spot, strike, tte)
                        if sigma is not None:
                            values.append(sigma)
                iv_summary[symbol] = {
                    "mean_iv": round(statistics.mean(values), 5) if values else None,
                    "stdev_iv": round(statistics.pstdev(values), 5) if values else None,
                    "samples": len(values),
                }

            iv_summary
            """
        ),
        code_cell(
            """
            BASE_VOL = {
                4000: 1e-6,
                4500: 1e-6,
                5000: 0.01220,
                5100: 0.01210,
                5200: 0.01225,
                5300: 0.01238,
                5400: 0.01155,
                5500: 0.01255,
                6000: 1e-6,
                6500: 1e-6,
            }

            def ac1(series):
                if len(series) < 3:
                    return 0.0
                mean = statistics.mean(series)
                num = sum((series[i] - mean) * (series[i - 1] - mean) for i in range(1, len(series)))
                den = sum((value - mean) ** 2 for value in series)
                return num / den if den else 0.0

            residual_stats = {}
            for symbol, strike in STRIKES.items():
                values = []
                for day in sorted(by_day_ts):
                    for timestamp in sorted(by_day_ts[day]):
                        snap = by_day_ts[day][timestamp]
                        spot = snap["VELVETFRUIT_EXTRACT"]["mid_price"]
                        fair = bs_call(spot, strike, TTE_BY_DAY[day], BASE_VOL[strike])
                        values.append(snap[symbol]["mid_price"] - fair)
                residual_stats[symbol] = {
                    "mean": round(statistics.mean(values), 4),
                    "sd": round(statistics.pstdev(values), 4),
                    "ac1": round(ac1(values), 4),
                }

            residual_stats
            """
        ),
        code_cell(
            """
            spread_stats = {}
            for left_symbol, right_symbol in [("VEV_5100", "VEV_5200"), ("VEV_5200", "VEV_5300"), ("VEV_5300", "VEV_5400"), ("VEV_5400", "VEV_5500")]:
                spreads = []
                for day in sorted(by_day_ts):
                    for timestamp in sorted(by_day_ts[day]):
                        snap = by_day_ts[day][timestamp]
                        spreads.append(snap[left_symbol]["mid_price"] - snap[right_symbol]["mid_price"])
                spread_stats[f"{left_symbol}-{right_symbol}"] = {
                    "mean": round(statistics.mean(spreads), 4),
                    "sd": round(statistics.pstdev(spreads), 4),
                    "ac1": round(ac1(spreads), 4),
                }

            spread_stats
            """
        ),
        markdown_cell(
            """
            ## Interpretation

            The latest trader model follows directly from the diagnostics above:

            - Residuals around a Black-Scholes anchor are persistent for the core
              strikes, so the trader treats them as state variables instead of
              ignoring them.
            - Adjacent-strike spreads are also persistent, which makes paired
              relative-value trades attractive when one strike looks cheap and the
              neighboring strike looks rich.
            - A fixed one-vol Black-Scholes model is still useful, but mainly as the
              first pass from which we build residual signals.
            - The live trader therefore uses a shrunk live smile, not a fully free
              smile fit and not a pure fixed-vol model.
            """
        ),
        code_cell(
            """
            from trader import Trader

            latest_model = {
                "core_option_symbols": Trader.CORE_OPTION_SYMBOLS,
                "base_option_vols": Trader.OPTION_VOLS,
                "live_smile_weight": Trader.OPTION_LIVE_SMILE_WEIGHT,
                "residual_alpha": Trader.OPTION_RESIDUAL_ALPHA,
                "residual_entry_z": Trader.OPTION_RESIDUAL_ENTRY_Z,
                "vev_5000_entry_z": Trader.OPTION_V5000_ENTRY_Z,
                "pair_gap_z": Trader.OPTION_PAIR_GAP_Z,
                "pair_target_delta": Trader.OPTION_PAIR_TARGET_DELTA,
                "delta_budget": Trader.OPTION_DELTA_BUDGET,
                "same_side_core_limit": Trader.OPTION_SAME_SIDE_CORE_LIMIT,
                "inventory_limits": {symbol: Trader.INTERNAL_LIMITS[symbol] for symbol in Trader.CORE_OPTION_SYMBOLS},
            }
            latest_model
            """
        ),
        code_cell(
            """
            diagnostics_path = ROOT / "logs" / "round3_diagnostics.json"
            diagnostics = json.loads(diagnostics_path.read_text()) if diagnostics_path.exists() else {}
            diagnostics
            """
        ),
        markdown_cell(
            """
            ## Manual Bio-Pod Challenge

            The local capsule still does **not** include the Celestial Gardeners'
            Guild reserve-price table or the Bio-Pod conversion schedule needed for
            a numeric manual submission.

            Practical conclusion:

            - The notebook now fully reflects the latest algorithmic trader and its
              theory references.
            - The manual section still needs the actual Guild page data before we can
              lock in final offer prices.
            - The Magritte clue remains a good reminder not to trust labels more than
              settlement mechanics.
            """
        ),
    ]

    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "version": "3.13",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> None:
    notebook = build_notebook()
    NOTEBOOK_PATH.write_text(json.dumps(notebook, indent=2))
    print(f"Wrote notebook to {NOTEBOOK_PATH}")


if __name__ == "__main__":
    main()
