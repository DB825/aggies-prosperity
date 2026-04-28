import json
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_PATH = ROOT / "round5_final_strategy.ipynb"
DIAGNOSTICS_PATH = ROOT / "logs" / "round5_diagnostics.json"
ML_RESEARCH_PATH = ROOT / "logs" / "round5_ml_research.json"
PRODUCT_ALPHA_PATH = ROOT / "logs" / "round5_product_alpha.json"
LOG_REPLAY_PATH = ROOT / "logs" / "round5_log_replay.json"


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


def load_ml_research() -> dict:
    if ML_RESEARCH_PATH.exists():
        return json.loads(ML_RESEARCH_PATH.read_text())
    return {}


def load_product_alpha() -> dict:
    if PRODUCT_ALPHA_PATH.exists():
        return json.loads(PRODUCT_ALPHA_PATH.read_text())
    return {}


def load_log_replay() -> dict:
    if LOG_REPLAY_PATH.exists():
        return json.loads(LOG_REPLAY_PATH.read_text())
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


def ml_results_table(ml_research: dict) -> str:
    if not ml_research:
        return "ML research not generated yet."

    lines = [
        "| Model | Tested use | Holdout PnL | Delta vs baseline | Mean target corr | Standalone taker PnL |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for name, result in ml_research.get("tabular_models", {}).items():
        lines.append(
            "| "
            f"`{name}` | passive quote filter | "
            f"{result.get('passive_sum', 0):,.1f} | "
            f"{result.get('passive_delta', 0):,.1f} | "
            f"{result.get('mean_target_corr', 0):.4f} | "
            f"{result.get('taker_sum', 0):,.1f} |"
        )

    transformer = ml_research.get("transformer", {})
    if transformer and not transformer.get("skipped"):
        lines.append(
            "| `tiny_transformer` | passive quote filter | "
            f"{transformer.get('passive_sum', 0):,.1f} | "
            f"{transformer.get('passive_delta', 0):,.1f} | "
            f"{transformer.get('mean_target_corr', 0):.4f} | n/a |"
        )
    return "\n".join(lines)


def product_alpha_table(product_alpha: dict, n: int = 14) -> str:
    robust = product_alpha.get("robust_products", [])
    if not robust:
        return "Product-alpha report not generated yet."
    lines = ["| Product | Family | LOO PnL | Worst fold | Full-sample config |", "|---|---|---:|---:|---|"]
    for item in robust[:n]:
        full_best = item.get("full_best") or {}
        params = full_best.get("params", {})
        strategy = full_best.get("strategy", item["family"])
        lines.append(
            "| "
            f"`{item['product']}` | {item['family']} | "
            f"{item['loo_total']:,.1f} | {item['loo_min']:,.1f} | "
            f"`{strategy} {params}` |"
        )
    return "\n".join(lines)


def log_replay_text(log_replay: dict) -> str:
    if not log_replay:
        return "Official-log replay not generated yet."
    return (
        f"Original submitted bot reported {log_replay.get('reported_profit_for_original_submission', 0):,.1f} "
        f"XIRECS. Replaying the current bot on the same 1,000-tick log gives an estimated "
        f"{log_replay.get('replay_estimated_pnl', 0):,.1f} XIRECS under the anonymous-trade "
        "passive-fill model."
    )


def build_notebook() -> dict:
    diagnostics = load_diagnostics()
    ml_research = load_ml_research()
    product_alpha = load_product_alpha()
    log_replay = load_log_replay()
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

            - A full product-by-product isolation pass found that the strongest
              deployable edge is a mix of passive inside-spread market making
              and simple one-tick regime signals.
            - `ROBOT_IRONING`, `MICROCHIP_OVAL`, `PANEL_1X2`, and
              `SLEEP_POD_NYLON` show robust large-move reversion. `OXYGEN_SHAKE_GARLIC`
              shows large-move momentum.
            - `OXYGEN_SHAKE_CHOCOLATE` and
              `OXYGEN_SHAKE_EVENING_BREATH` have jump-reversion events large
              enough to justify crossing the spread. These are deliberately gated
              by a 30-XIREC one-tick move threshold.
            - Cross-sectional group residual/stat-arb tests were mostly flat or
              negative after paying spread. The script keeps those diagnostics,
              but `trader.py` does not deploy a group residual sleeve.
            - Products without robust replay contribution are left idle. Unused
              symbols are better than forced variance.
            """
        ),
        markdown_cell(
            f"""
            ## Product-by-product alpha isolation

            `scripts/round5_product_alpha.py` evaluates every product separately
            across passive maker grids, one-tick return regimes, rolling
            z-score regimes, book imbalance/microprice takers, and per-product
            ridge ML takers. Parameters are selected on two days and scored on
            the held-out day.

            {product_alpha_table(product_alpha)}

            Category-level residual/stat-arb tests were also evaluated. They did
            not clear the robustness bar, so the final bot keeps the simpler
            per-product signals instead.

            {log_replay_text(log_replay)}
            """
        ),
        markdown_cell(
            f"""
            ## ML, neural net, and transformer research

            I tested ML as a **gate** around the existing strategy, not as an
            unrestricted replacement. The validation is leave-one-day-out:
            train on two historical days, choose thresholds/quote gates only on
            those training days, then score the untouched held-out day.

            Tested families:

            - Ridge regression with product one-hot features.
            - Histogram gradient boosted trees.
            - Multi-layer perceptron neural network.
            - LightGBM boosted trees.
            - Tiny PyTorch transformer over 16-tick order-book sequences.

            {ml_results_table(ml_research)}

            Conclusion: the best advanced model still failed to beat the
            current 64,300 XIRECS baseline on leave-one-day-out replay. The
            predictors show tiny next-tick correlations, but the signal is not
            strong enough to pay spread/queue costs. I therefore did **not**
            add ML logic to `trader.py`; the research harness is saved as
            `scripts/round5_ml_research.py` for further experiments.
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
        code_cell(
            """
            ml_research = json.loads(Path("logs/round5_ml_research.json").read_text())
            {
                "baseline_sum": ml_research["baseline_sum"],
                "best_tabular_passive": max(
                    (
                        (name, result["passive_sum"], result["passive_delta"])
                        for name, result in ml_research["tabular_models"].items()
                    ),
                    key=lambda item: item[1],
                ),
                "transformer_delta": ml_research["transformer"].get("passive_delta"),
            }
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
            - **Per-product learned signal takers:** six simple one-tick
              return regimes are stored as constants in `SIGNAL_PARAMS`. These
              are distilled from the product-alpha scan instead of running a
              heavyweight model live.
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
