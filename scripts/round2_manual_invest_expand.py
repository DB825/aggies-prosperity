import json
import math
from pathlib import Path
from typing import Callable, Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "logs" / "round2_manual_invest_expand.json"
NOTEBOOK_PATH = ROOT / "round2_manual_invest_expand_strategy.ipynb"
BUDGET = 50_000


def research(percent: float) -> float:
    return 200_000 * math.log1p(percent) / math.log1p(100)


def scale(percent: float) -> float:
    return 7 * percent / 100


def speed_multiplier_from_percentile(percentile_beaten: float) -> float:
    return 0.1 + 0.8 * max(0.0, min(1.0, percentile_beaten))


def budget_used(research_percent: float, scale_percent: float, speed_percent: float) -> float:
    return BUDGET * (research_percent + scale_percent + speed_percent) / 100


def net_pnl(
    research_percent: float,
    scale_percent: float,
    speed_percent: float,
    percentile_beaten: float,
) -> float:
    multiplier = speed_multiplier_from_percentile(percentile_beaten)
    gross = research(research_percent) * scale(scale_percent) * multiplier
    return gross - budget_used(research_percent, scale_percent, speed_percent)


def best_integer_research_scale(speed_percent: int) -> Dict:
    remaining = 100 - speed_percent
    best = None
    for research_percent in range(remaining + 1):
        scale_percent = remaining - research_percent
        gross_base = research(research_percent) * scale(scale_percent)
        if best is None or gross_base > best["gross_base"]:
            best = {
                "research": research_percent,
                "scale": scale_percent,
                "speed": speed_percent,
                "gross_base": gross_base,
            }
    return best


def continuous_research_scale(speed_percent: float) -> Dict:
    remaining = 100 - speed_percent
    low = 0.0
    high = remaining
    for _ in range(100):
        mid = (low + high) / 2
        condition = (remaining - mid) - (1 + mid) * math.log1p(mid)
        if condition > 0:
            low = mid
        else:
            high = mid
    research_percent = (low + high) / 2
    return {
        "research": research_percent,
        "scale": remaining - research_percent,
        "speed": speed_percent,
        "gross_base": research(research_percent) * scale(remaining - research_percent),
    }


def percentile_needed_for_target(gross_base: float, target_net: float) -> float:
    required_multiplier = (target_net + BUDGET) / gross_base
    return (required_multiplier - 0.1) / 0.8


def evaluate_allocation(allocation: Dict, percentiles: List[float]) -> Dict:
    rows = []
    for percentile in percentiles:
        multiplier = speed_multiplier_from_percentile(percentile)
        gross = allocation["gross_base"] * multiplier
        rows.append(
            {
                "percentile_beaten": percentile,
                "speed_multiplier": multiplier,
                "gross_pnl": gross,
                "net_pnl": gross - BUDGET,
            }
        )
    return {
        **allocation,
        "pnl_by_speed_percentile": rows,
        "percentile_needed_for_200k": percentile_needed_for_target(allocation["gross_base"], 200_000),
        "percentile_needed_for_300k": percentile_needed_for_target(allocation["gross_base"], 300_000),
        "top_rank_net_pnl": allocation["gross_base"] * 0.9 - BUDGET,
    }


def normal_cdf(mean: float, stdev: float) -> Callable[[float], float]:
    def cdf(value: float) -> float:
        return 0.5 * (1 + math.erf((value - mean) / (stdev * math.sqrt(2))))

    return cdf


def balanced_integer_crowd_cdf(value: float) -> float:
    if value < 25:
        return 0.15 * value / 25
    if value <= 34:
        return 0.15
    if value < 45:
        return 0.70
    return min(1.0, 0.70 + 0.30 * (value - 45) / 35)


def scenario_scan(name: str, cdf: Callable[[float], float]) -> Dict:
    rows = []
    for speed_percent in range(0, 81):
        allocation = best_integer_research_scale(speed_percent)
        percentile = cdf(speed_percent)
        multiplier = speed_multiplier_from_percentile(percentile)
        rows.append(
            {
                **allocation,
                "percentile_beaten": percentile,
                "speed_multiplier": multiplier,
                "net_pnl": allocation["gross_base"] * multiplier - BUDGET,
            }
        )
    rows = sorted(rows, key=lambda row: row["net_pnl"], reverse=True)
    return {"scenario": name, "top_10": rows[:10]}


def markdown_table(headers: List[str], rows: List[List]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def build_notebook(output: Dict) -> Dict:
    recommendation = output["recommended_allocation"]
    percentile_rows = recommendation["pnl_by_speed_percentile"]
    recommendation_table = markdown_table(
        ["Research", "Scale", "Speed", "Budget Used"],
        [[
            f"{recommendation['research']}%",
            f"{recommendation['scale']}%",
            f"{recommendation['speed']}%",
            f"{BUDGET:,} XIRECs",
        ]],
    )
    pnl_table = markdown_table(
        ["Percentile Beaten", "Speed Multiplier", "Net PnL"],
        [
            [
                f"{100 * row['percentile_beaten']:.0f}%",
                f"{row['speed_multiplier']:.2f}",
                f"{row['net_pnl']:,.0f}",
            ]
            for row in percentile_rows
        ],
    )
    alternative_table = markdown_table(
        ["Allocation", "Top-Rank Net", "Beat % For 200k", "Net At 70% Beaten"],
        [
            [
                f"{row['research']}/{row['scale']}/{row['speed']}",
                f"{row['top_rank_net_pnl']:,.0f}",
                f"{100 * row['percentile_needed_for_200k']:.1f}%",
                f"{row['net_at_70pct']:,.0f}",
            ]
            for row in output["candidate_allocations"]
        ],
    )
    scenario_table = markdown_table(
        ["Scenario", "Best Research/Scale/Speed", "Assumed % Beaten", "Net PnL"],
        [
            [
                scenario["scenario"],
                f"{scenario['top_10'][0]['research']}/{scenario['top_10'][0]['scale']}/{scenario['top_10'][0]['speed']}",
                f"{100 * scenario['top_10'][0]['percentile_beaten']:.1f}%",
                f"{scenario['top_10'][0]['net_pnl']:,.0f}",
            ]
            for scenario in output["scenario_scans"]
        ],
    )
    cells = [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "# Round 2 Manual Challenge: Invest & Expand\n",
                "\n",
                "This notebook solves the 50,000 XIREC allocation problem across Research, Scale, and Speed. Research and Scale are deterministic production functions; Speed is strategic because it depends on rank against other players.\n",
            ],
        },
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "## Recommended Submission\n",
                "\n",
                recommendation_table,
                "\n\n",
                "Submit **Research 16%, Scale 49%, Speed 35%**. If the interface accepts decimals, the continuous Research/Scale optimum at 35% Speed is about Research 16.17%, Scale 48.83%, Speed 35.00%; the integer version is easier to submit and nearly identical.\n",
            ],
        },
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "## Why this split\n",
                "\n",
                "For a fixed Speed percentage, the optimal deterministic split uses the rest of the budget and solves `(remaining - research) = (1 + research) ln(1 + research)`. That keeps Research in the logarithmic sweet spot and allocates the rest to linear Scale.\n",
                "\n",
                "Speed is the game-theory leg. A low Speed allocation has a better Research/Scale engine but can lose the rank tournament. A very high Speed allocation can win rank but starves Research and Scale. The 35% Speed recommendation is designed to sit one point above the common integer-balanced `33/33/34` style allocation while keeping enough capital in Scale to make the edge pay.\n",
            ],
        },
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "## Speed Rank Sensitivity\n",
                "\n",
                pnl_table,
                "\n\n",
                f"The recommended allocation clears 200,000 XIRECs if it beats at least **{100 * recommendation['percentile_needed_for_200k']:.1f}%** of Speed investments. At a 70th-percentile Speed rank, it scores about **{next(row['net_pnl'] for row in percentile_rows if abs(row['percentile_beaten'] - 0.7) < 1e-9):,.0f} XIRECs**.\n",
            ],
        },
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "## Alternatives Considered\n",
                "\n",
                alternative_table,
                "\n\n",
                "`18/57/25` has the best payoff around a true median Speed rank, but it is vulnerable to the obvious balanced-crowd response. `15/45/40` buys more rank insurance, but needs a much stronger rank just to reach the same expected score. `16/49/35` is the best compromise against a field likely clustered around 34% Speed.\n",
            ],
        },
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "## Scenario Scan\n",
                "\n",
                scenario_table,
                "\n\n",
                "The scenario scan is not a forecast; it is a way to avoid fooling ourselves. If the whole field is extremely Speed-heavy, the manual game becomes rent-dissipating and no allocation is especially attractive. If many players submit balanced or moderately optimized allocations, 35% Speed is the practical edge point.\n",
            ],
        },
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "from pathlib import Path\n",
                "import json\n",
                "\n",
                "diagnostics = json.loads(Path('logs/round2_manual_invest_expand.json').read_text())\n",
                "diagnostics['recommended_allocation']\n",
            ],
        },
    ]
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "pygments_lexer": "ipython3"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> None:
    percentiles = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    recommended = evaluate_allocation(best_integer_research_scale(35), percentiles)
    continuous_at_35 = continuous_research_scale(35)
    candidate_speeds = [20, 25, 30, 35, 40]
    candidate_allocations = []
    for speed_percent in candidate_speeds:
        evaluated = evaluate_allocation(best_integer_research_scale(speed_percent), percentiles)
        evaluated["net_at_70pct"] = net_pnl(
            evaluated["research"],
            evaluated["scale"],
            evaluated["speed"],
            0.7,
        )
        candidate_allocations.append(evaluated)

    output = {
        "challenge": "Round 2 manual Invest & Expand",
        "budget": BUDGET,
        "recommended_submission": {
            "research_percent": recommended["research"],
            "scale_percent": recommended["scale"],
            "speed_percent": recommended["speed"],
        },
        "recommended_allocation": recommended,
        "continuous_optimum_given_35_speed": continuous_at_35,
        "candidate_allocations": candidate_allocations,
        "scenario_scans": [
            scenario_scan("uniform speed field", lambda value: value / 100),
            scenario_scan("balanced integer crowd", balanced_integer_crowd_cdf),
            scenario_scan("normal field around 33% speed", normal_cdf(33, 10)),
            scenario_scan("normal field around 40% speed", normal_cdf(40, 12)),
        ],
        "notes": [
            "Final recommendation uses integer percentages because the manual UI is easiest to submit that way.",
            "Speed percentile means the fraction of participants whose Speed investment is lower than ours.",
            "If many players over-invest in Speed, the rank tournament dissipates value; the recommendation is targeted at beating balanced and moderately optimized allocations without starving Research/Scale.",
        ],
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    NOTEBOOK_PATH.write_text(json.dumps(build_notebook(output), indent=2), encoding="utf-8")
    print(f"recommended: {recommended['research']}/{recommended['scale']}/{recommended['speed']}")
    print(f"percentile needed for 200k: {100 * recommended['percentile_needed_for_200k']:.1f}%")
    print(f"net at 70th percentile: {net_pnl(recommended['research'], recommended['scale'], recommended['speed'], 0.7):,.0f}")
    print(f"wrote {OUTPUT_PATH}")
    print(f"wrote {NOTEBOOK_PATH}")


if __name__ == "__main__":
    main()
