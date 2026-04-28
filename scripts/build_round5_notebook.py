import json
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_PATH = ROOT / "round5_final_strategy.ipynb"
DIAGNOSTICS_PATH = ROOT / "logs" / "round5_diagnostics.json"


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


def load_diagnostics() -> dict:
    if DIAGNOSTICS_PATH.exists():
        return json.loads(DIAGNOSTICS_PATH.read_text())
    return {}


def top_products_table(diagnostics: dict, n: int = 16) -> str:
    totals = diagnostics.get("per_product_totals", {})
    if not totals:
        return "Diagnostics not generated yet."
    lines = ["| Product | Replay PnL |", "|---|---:|"]
    for product, pnl in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:n]:
        lines.append(f"| `{product}` | {pnl:,.1f} |")
    return "\n".join(lines)


def day_table(diagnostics: dict) -> str:
    days = diagnostics.get("day_results", [])
    if not days:
        return "Diagnostics not generated yet."
    lines = ["| Day | Replay PnL |", "|---:|---:|"]
    for day in days:
        lines.append(f"| {day['day']} | {day['total_pnl']:,.1f} |")
    return "\n".join(lines)


def build_notebook() -> dict:
    diagnostics = load_diagnostics()
    combined = diagnostics.get("combined_pnl")
    combined_text = "Diagnostics not generated yet."
    if combined is not None:
        combined_text = f"**{combined:,.1f} XIRECS**"

    fills = diagnostics.get("fills", {})
    fill_text = "Diagnostics not generated yet."
    if fills:
        fill_text = (
            f"{fills.get('cross_fills', 0):,} crossing fills, "
            f"{fills.get('passive_fills', 0):,} passive fills, "
            f"{fills.get('filled_quantity', 0):,} total filled units"
        )

    cells = [
        markdown_cell(
            f"""
            # Round 5: The Final Stretch

            This notebook documents the final `trader.py` submission for the 50
            new Round 5 products plus the Ignith manual Ashflow Alpha allocation.

            ## Final replay summary

            - Combined deterministic replay: {combined_text}
            - Replay horizon: official-style timestamps `0..99,900`, matching the
              1,000-tick log bundle rather than the exploratory full-day files.
            - Fill model: book-crossing plus public-repo-style passive fills from
              bot trades that would have interacted with our improved quote.
            - Fill count: {fill_text}
            - Active algorithmic file: `trader.py`
            - Diagnostics script: `scripts/round5_diagnostics.py`

            {day_table(diagnostics)}

            ## Top replay contributors

            {top_products_table(diagnostics)}
            """
        ),
        markdown_cell(
            """
            ## Public IMC repository lessons used

            I reviewed public Prosperity writeups and code repositories before
            finalizing the Round 5 shape:

            | Source | Applicable lesson |
            |---|---|
            | [Frankfurt Hedgehogs, Prosperity 3, 2nd globally](https://github.com/TimoDiehm/imc-prosperity-3) | Treat Prosperity as a microstructure game first: identify the fair price, improve inside the spread, and use dashboards/backtests to inspect fills. Their writeup emphasizes WallMid/true-price reasoning, inventory clearing, and bot behavior. |
            | [Linear Utility, Prosperity 2, 2nd place](https://github.com/ericcccsliu/imc-prosperity-2) | Build a replay harness, grid search simple parameters, and prefer structural edges over decorative correlations. Their most durable edges came from true-price market making, conversion arbitrage, and spread trades. |
            | [jmerle, Prosperity 2, 9th overall](https://github.com/jmerle/imc-prosperity-2) | In Round 5, de-anonymized flow can dominate. Their writeup found named-trader directional signals and also warns that overfit directional products can lose badly. |
            | [AlphaBaguette, Prosperity 3](https://github.com/Sylvain-Topeza/imc-prosperity-3) | Combine complementary small edges: adaptive market making, informed flow when available, index/spread logic, and strict position limits. |
            | [Prosperity preparation discussion](https://www.reddit.com/r/learnquant/comments/1rvf93p/how_to_actually_compete_and_maybe_win_in_imc/) | The common archetypes are fixed-fair market making, basket/stat-arb spreads, options, location arbitrage, and Round 5 trader-ID flow. |

            The Round 5 trade files in this dataset do **not** reveal buyer/seller
            IDs; every buyer and seller field is blank. Therefore the prior
            "copy the informed trader" trick is not directly available here. The
            final algorithm instead applies the reusable parts of those writeups:
            fair-price market making, fill-aware parameter selection, and only a
            few high-confidence directional overlays.
            """
        ),
        markdown_cell(
            """
            ## Local data findings

            The zip contains three historical days: days 2, 3, and 4. Each day has
            10,000 timestamps for all 50 products.

            Main discoveries:

            - The strongest repeatable edge is passive inside-spread market making
              on selected products, but the official fill log showed that some
              sleeves with good simulated spread capture had poor queue quality.
              The current build prunes those products rather than fitting to a
              single path.
            - `OXYGEN_SHAKE_CHOCOLATE` and
              `OXYGEN_SHAKE_EVENING_BREATH` have jump-reversion events large
              enough to justify crossing the spread. These are deliberately gated
              by a 30-XIREC one-tick move threshold.
            - The earlier `ROBOT_DISHES` jump trigger, snack-pack group overlay,
              and individual pebble makers were removed after the official
              1,000-tick log exposed weak realized execution quality.
            - Pebbles still have a near-exact five-product sum around 50,000, but
              the tradable edge is too thin after spread and queue cost.
            - Products without robust replay contribution are left idle. Unused
              symbols are better than forced variance.
            """
        ),
        code_cell(
            """
            import json
            from pathlib import Path

            diagnostics = json.loads(Path("logs/round5_diagnostics.json").read_text())
            diagnostics["combined_pnl"], diagnostics["fills"]
            """
        ),
        code_cell(
            """
            import pandas as pd

            totals = diagnostics["per_product_totals"]
            pd.Series(totals).sort_values(ascending=False).head(20).to_frame("replay_pnl")
            """
        ),
        markdown_cell(
            """
            ## Trader implementation

            The final `trader.py` is Round 5 only. It does not trade any products
            from previous rounds.

            Strategy layers:

            - **Passive selected makers:** quote one tick better than the best
              displayed bid/ask only when the quote still has positive edge to the
              current book mid after inventory skew.
            - **Jump-reversion takers:** cross only after very large one-tick
              moves in the two oxygen products where this paid across replay.
            - **No anonymous-flow follower:** buyer/seller IDs in the official log
              are blank except for `SUBMISSION`, and cost-aware flow following was
              negative after spread.
            - **Risk controls:** all logic respects the hard 10-unit position
              limit per product, and products without robust evidence are idle.
            """
        ),
        markdown_cell(
            """
            ## Ignith manual strategy

            Submit the following Ashflow Alpha manual orders:

            | Good | Side | % |
            |---|---:|---:|
            | Sulfur reactor | Buy | 16% |
            | Thermalite core | Buy | 14% |
            | Lava cake | Sell | 13% |
            | Pyroflex cells | Sell | 11% |
            | Magma ink | Buy | 8% |
            | Ashes of the Phoenix | Sell | 6% |
            | Volcanic incense | Buy | 5% |
            | Scoria paste | Buy | 4% |
            | Obsidian cutlery | Buy | 3% |

            This uses 80% of the manual budget and pays 89,200 XIRECS in fees.
            The fee rule makes each product's break-even move equal to its
            allocation percentage, so the unused 20% is intentional. It avoids
            forcing capital into weaker headlines where the quadratic fee can
            overwhelm the news edge.
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
                "pygments_lexer": "ipython3",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> None:
    NOTEBOOK_PATH.write_text(json.dumps(build_notebook(), indent=2) + "\n")
    print(f"Wrote {NOTEBOOK_PATH}")


if __name__ == "__main__":
    main()
