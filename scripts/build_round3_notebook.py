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
            f"{day['total_pnl']:.1f} XIRECS"
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

            This notebook institutionalizes the Round 3 research flow for Solvenar's
            `HYDROGEL_PACK`, `VELVETFRUIT_EXTRACT`, and `VELVETFRUIT_EXTRACT_VOUCHER`
            products.

            ## Working Summary

            {summary_block}

            ## Core Design

            - `HYDROGEL_PACK`: stationary microstructure market with a 10,000 anchor,
              slow EMA smoothing, order-book imbalance adjustment, and wide crossing
              thresholds to avoid overtrading noise.
            - `VELVETFRUIT_EXTRACT`: slower mean reversion than Hydrogel, so the
              strategy uses a very conservative edge and mostly leaves the product
              for only the cleanest dislocations.
            - `VEV_*` vouchers: Black-Scholes call pricing with strike-specific
              volatility calibration from historical TTE 8/7/6 days, then rolled
              forward to live TTE 5 days. Inventory is controlled with internal
              caps and a portfolio delta budget rather than full delta hedging.
            - Live quoting: the trader still posts mean-reversion quotes around fair
              value, but the local replay here only credits immediate crossings.
            """
        ),
        markdown_cell(
            """
            ## External References Used

            The Round 3 design deliberately borrows the strongest recurring ideas
            from successful Prosperity writeups while adapting them to Solvenar's
            product set and historical capsule:

            - [TimoDiehm/imc-prosperity-3](https://github.com/TimoDiehm/imc-prosperity-3):
              emphasized IV scalping plus only a lightweight underlying hedge when
              explicit delta hedging would be too expensive in spread.
            - [CarterT27/imc-prosperity-3](https://github.com/CarterT27/imc-prosperity-3):
              used Black-Scholes pricing, a rolling volatility estimate, and
              cross-voucher arbitrage checks.
            - [chrispyroberts/imc-prosperity-3](https://github.com/chrispyroberts/imc-prosperity-3):
              fit a quadratic volatility smile in moneyness space and ran an
              aggressive fair-value market maker.
            - [JamesCole809/IMC-Prosperity-3](https://github.com/JamesCole809/IMC-Prosperity-3):
              called out the instability of deep ITM implied vols when extrinsic
              value is close to zero, which matters here for `VEV_4000` and
              `VEV_4500`.

            The Solvenar implementation keeps the option-theory backbone, but uses
            a simpler fixed strike-vol calibration because the local Round 3 chain
            is more stable under that approach than under a fully reactive live smile fit.
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
                for product in ["HYDROGEL_PACK", "VELVETFRUIT_EXTRACT", "VEV_5000", "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"]
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
                if price <= intrinsic + 1e-9:
                    return 0.0
                lo, hi = 1e-6, 1.0
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
                        extrinsic = price - max(0.0, spot - strike)
                        sigma = implied_vol(price, spot, strike, tte)
                        if extrinsic > 1 and 0.005 < sigma < 0.1:
                            values.append(sigma)
                iv_summary[symbol] = {
                    "mean_iv": round(statistics.mean(values), 5) if values else None,
                    "stdev_iv": round(statistics.pstdev(values), 5) if values else None,
                    "samples": len(values),
                }

            iv_summary
            """
        ),
        markdown_cell(
            """
            ## Chosen Voucher Model

            The local data supports a practical compromise:

            - Use Black-Scholes call pricing.
            - Keep strike-specific volatility anchors for the tradable strikes.
            - Treat `VEV_4000` and `VEV_4500` mostly as intrinsic-value instruments
              because their extrinsic value is tiny relative to spread noise.
            - Ignore the temptation to fit a very reactive smile at every timestamp:
              it is elegant, but on this dataset a fixed calibration was more stable
              in replay than a fully dynamic quadratic smile.
            """
        ),
        code_cell(
            """
            from trader import Trader

            calibration = {
                "option_vols": Trader.OPTION_VOLS,
                "option_take_edges": Trader.OPTION_TAKE_EDGES,
                "option_internal_limits": Trader.INTERNAL_LIMITS,
                "hydrogel_take_edge": Trader.HYDROGEL_TAKE_EDGE,
                "velvet_take_edge": Trader.VELVET_TAKE_EDGE,
                "live_tte_days": Trader.LIVE_TTE_DAYS,
            }
            calibration
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

            The local capsule included the thematic image
            `data/round3/La_trahison_des_images.png`, but it did **not** include the
            Celestial Gardeners' Guild reserve-price table or the Bio-Pod conversion
            schedule needed for a numeric manual submission.

            Practical conclusion:

            - The notebook records the algorithmic work completely.
            - For the manual section, we should only lock in final offer prices after
              capturing the actual Guild page inputs from the platform.
            - The Magritte clue is still useful as a reminder not to trust the name
              of an object over the mechanism that actually settles profit.
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
