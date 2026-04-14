import argparse
import copy
import csv
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datamodel import Listing, Observation, Order, OrderDepth, TradingState
from trader import Trader


PRODUCTS = ("ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT")
DATA_DIR = ROOT / "data" / "round1"
OUTPUT_PATH = ROOT / "logs" / "round1_diagnostics.json"
NORMAL_AD_CRITICAL_VALUES = {
    "15%": 0.576,
    "10%": 0.656,
    "5%": 0.787,
    "2.5%": 0.918,
    "1%": 1.092,
}


def load_price_rows() -> Dict[int, Dict[int, List[Dict]]]:
    by_day_ts: Dict[int, Dict[int, List[Dict]]] = {}
    for path in sorted(DATA_DIR.glob("prices_round_1_day_*.csv")):
        with path.open(newline="") as file:
            for row in csv.DictReader(file, delimiter=";"):
                parsed = {
                    "day": int(row["day"]),
                    "timestamp": int(row["timestamp"]),
                    "product": row["product"],
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
                by_day_ts.setdefault(parsed["day"], {}).setdefault(parsed["timestamp"], []).append(parsed)
    return by_day_ts


def build_depths(rows: Iterable[Dict]) -> Tuple[Dict[str, OrderDepth], Dict[str, float]]:
    depths: Dict[str, OrderDepth] = {}
    mids: Dict[str, float] = {}
    for row in rows:
        product = row["product"]
        depth = OrderDepth()
        for level in (1, 2, 3):
            bid_price = row[f"bid_price_{level}"]
            bid_volume = row[f"bid_volume_{level}"]
            ask_price = row[f"ask_price_{level}"]
            ask_volume = row[f"ask_volume_{level}"]
            if bid_price is not None:
                depth.buy_orders[int(bid_price)] = int(bid_volume)
            if ask_price is not None:
                depth.sell_orders[int(ask_price)] = -int(ask_volume)
        depths[product] = depth
        mids[product] = row["mid_price"]
    return depths, mids


def make_trader(overrides: Optional[Dict] = None) -> Trader:
    trader = Trader()
    trader.PARAMS = copy.deepcopy(Trader.PARAMS)
    if not overrides:
        return trader

    for product, values in overrides.get("params", {}).items():
        trader.PARAMS[product].update(values)
    for name, value in overrides.get("attrs", {}).items():
        setattr(trader, name, value)
    return trader


def apply_fills(
    orders: Iterable[Order],
    depths: Dict[str, OrderDepth],
    cash: Dict[str, float],
    position: Dict[str, int],
    execution: Optional[Dict] = None,
    rng: Optional[random.Random] = None,
) -> Tuple[Dict[str, float], Dict[str, int], Dict[str, int]]:
    stats = {"filled_orders": 0, "filled_quantity": 0}
    fill_probability = 1.0
    max_adverse_slippage = 0
    if execution:
        fill_probability = execution.get("cross_fill_probability", fill_probability)
        max_adverse_slippage = execution.get("max_adverse_slippage", max_adverse_slippage)

    for order in orders:
        if rng and rng.random() > fill_probability:
            continue

        depth = depths[order.symbol]
        if order.quantity > 0:
            quantity_left = order.quantity
            for price in sorted(list(depth.sell_orders)):
                if price > order.price or quantity_left <= 0:
                    break
                fill = min(quantity_left, -depth.sell_orders[price])
                if fill > 0:
                    slippage = rng.randint(0, max_adverse_slippage) if rng else 0
                    cash[order.symbol] -= (price + slippage) * fill
                    position[order.symbol] += fill
                    quantity_left -= fill
                    depth.sell_orders[price] += fill
                    stats["filled_orders"] += 1
                    stats["filled_quantity"] += fill
        elif order.quantity < 0:
            quantity_left = -order.quantity
            for price in sorted(list(depth.buy_orders), reverse=True):
                if price < order.price or quantity_left <= 0:
                    break
                fill = min(quantity_left, depth.buy_orders[price])
                if fill > 0:
                    slippage = rng.randint(0, max_adverse_slippage) if rng else 0
                    cash[order.symbol] += (price - slippage) * fill
                    position[order.symbol] -= fill
                    quantity_left -= fill
                    depth.buy_orders[price] -= fill
                    stats["filled_orders"] += 1
                    stats["filled_quantity"] += fill
    return cash, position, stats


def run_backtest(
    by_day_ts: Dict[int, Dict[int, List[Dict]]],
    overrides: Optional[Dict] = None,
    execution: Optional[Dict] = None,
    seed: Optional[int] = None,
) -> Dict:
    rng = random.Random(seed) if seed is not None else None
    day_results = []
    total_filled_orders = 0
    total_filled_quantity = 0

    for day in sorted(by_day_ts):
        trader = make_trader(overrides)
        trader_data = ""
        cash = {product: 0.0 for product in PRODUCTS}
        position = {product: 0 for product in PRODUCTS}
        last_mid: Dict[str, float] = {}
        listings = {product: Listing(product, product, "XIRECS") for product in PRODUCTS}

        for timestamp in sorted(by_day_ts[day]):
            depths, mids = build_depths(by_day_ts[day][timestamp])
            last_mid.update({product: mid for product, mid in mids.items() if mid > 0})
            state = TradingState(
                trader_data,
                timestamp,
                listings,
                depths,
                {product: [] for product in PRODUCTS},
                {product: [] for product in PRODUCTS},
                dict(position),
                Observation({}, {}),
            )
            result, _, trader_data = trader.run(state)
            orders = [order for product_orders in result.values() for order in product_orders]
            cash, position, fill_stats = apply_fills(orders, depths, cash, position, execution, rng)
            total_filled_orders += fill_stats["filled_orders"]
            total_filled_quantity += fill_stats["filled_quantity"]

        mark_noise = execution.get("mark_noise", 0.0) if execution else 0.0
        pnl_by_product = {}
        for product in PRODUCTS:
            mark = last_mid[product]
            if rng and mark_noise > 0:
                mark += rng.gauss(0.0, mark_noise)
            pnl_by_product[product] = cash[product] + position[product] * mark

        day_results.append(
            {
                "day": day,
                "pnl_by_product": pnl_by_product,
                "position": dict(position),
                "total_pnl": sum(pnl_by_product.values()),
            }
        )

    return {
        "day_results": day_results,
        "combined_pnl": sum(day["total_pnl"] for day in day_results),
        "filled_orders": total_filled_orders,
        "filled_quantity": total_filled_quantity,
    }


def summarize(values: List[float]) -> Dict[str, float]:
    ordered = sorted(values)

    def percentile(p: float) -> float:
        if not ordered:
            return float("nan")
        index = (len(ordered) - 1) * p
        low = math.floor(index)
        high = math.ceil(index)
        if low == high:
            return ordered[low]
        return ordered[low] * (high - index) + ordered[high] * (index - low)

    return {
        "count": len(values),
        "min": min(values),
        "p05": percentile(0.05),
        "p10": percentile(0.10),
        "median": percentile(0.50),
        "mean": statistics.mean(values),
        "p90": percentile(0.90),
        "p95": percentile(0.95),
        "max": max(values),
        "stdev": statistics.pstdev(values) if len(values) > 1 else 0.0,
    }


def parameter_grid(by_day_ts: Dict[int, Dict[int, List[Dict]]]) -> Dict:
    rows = []
    for pepper_buy_edge in (4.0, 8.0, 12.0):
        for pepper_exit_edge in (6.0, 8.0, 10.0):
            for ash_take_edge in (3.0, 4.0, 5.0):
                overrides = {
                    "params": {
                        "INTARIAN_PEPPER_ROOT": {
                            "trend_buy_edge": pepper_buy_edge,
                            "exit_sell_edge": pepper_exit_edge,
                        },
                        "ASH_COATED_OSMIUM": {"take_edge": ash_take_edge},
                    }
                }
                result = run_backtest(by_day_ts, overrides=overrides)
                rows.append(
                    {
                        "pepper_buy_edge": pepper_buy_edge,
                        "pepper_exit_edge": pepper_exit_edge,
                        "ash_take_edge": ash_take_edge,
                        "combined_pnl": result["combined_pnl"],
                    }
                )

    pnls = [row["combined_pnl"] for row in rows]
    selected = next(
        row
        for row in rows
        if row["pepper_buy_edge"] == 8.0 and row["pepper_exit_edge"] == 8.0 and row["ash_take_edge"] == 4.0
    )
    sorted_rows = sorted(rows, key=lambda row: row["combined_pnl"], reverse=True)
    selected_rank = 1 + sorted_rows.index(selected)
    return {
        "rows": rows,
        "summary": summarize(pnls),
        "target_pass_rate": sum(pnl >= 200_000 for pnl in pnls) / len(pnls),
        "selected": selected,
        "selected_rank": selected_rank,
        "top_5": sorted_rows[:5],
        "bottom_5": sorted_rows[-5:],
    }


def monte_carlo(
    by_day_ts: Dict[int, Dict[int, List[Dict]]],
    chains: int,
    draws: int,
    seed: int,
) -> Dict:
    all_chains = []
    stress = {
        "cross_fill_probability": 0.97,
        "max_adverse_slippage": 1,
        "mark_noise": 3.0,
    }
    for chain in range(chains):
        values = []
        for draw in range(draws):
            result = run_backtest(by_day_ts, execution=stress, seed=seed + chain * 10_000 + draw)
            values.append(result["combined_pnl"])
        all_chains.append(values)

    flattened = [value for chain in all_chains for value in chain]
    return {
        "stress_assumptions": stress,
        "chains": all_chains,
        "summary": summarize(flattened),
        "probability_above_200k": sum(value >= 200_000 for value in flattened) / len(flattened),
        "gelman_rubin_rhat": gelman_rubin_rhat(all_chains),
        "geweke": [geweke_z(chain) for chain in all_chains],
        "anderson_darling_normality": anderson_darling_normal(flattened),
        "kolmogorov_smirnov_normality": kolmogorov_smirnov_normal(flattened),
    }


def gelman_rubin_rhat(chains: List[List[float]]) -> float:
    m = len(chains)
    n = min(len(chain) for chain in chains)
    trimmed = [chain[:n] for chain in chains]
    chain_means = [statistics.mean(chain) for chain in trimmed]
    chain_variances = [statistics.variance(chain) if len(chain) > 1 else 0.0 for chain in trimmed]
    within = statistics.mean(chain_variances)
    between = n * statistics.variance(chain_means) if m > 1 else 0.0
    if within == 0:
        return 1.0 if between == 0 else float("inf")
    variance_hat = ((n - 1) / n) * within + between / n
    return max(1.0, math.sqrt(variance_hat / within))


def geweke_z(chain: List[float], first_fraction: float = 0.10, last_fraction: float = 0.50) -> Dict[str, float]:
    first_count = max(2, int(len(chain) * first_fraction))
    last_count = max(2, int(len(chain) * last_fraction))
    first = chain[:first_count]
    last = chain[-last_count:]
    first_var = statistics.variance(first) if len(first) > 1 else 0.0
    last_var = statistics.variance(last) if len(last) > 1 else 0.0
    standard_error = math.sqrt(first_var / len(first) + last_var / len(last))
    z_score = 0.0 if standard_error == 0 else (statistics.mean(first) - statistics.mean(last)) / standard_error
    return {
        "first_count": first_count,
        "last_count": last_count,
        "z_score": z_score,
        "passes_abs_z_lt_2": abs(z_score) < 2.0,
    }


def normal_cdf(value: float, mean: float, stdev: float) -> float:
    if stdev <= 0:
        return 0.5
    z = (value - mean) / (stdev * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


def anderson_darling_normal(values: List[float]) -> Dict:
    ordered = sorted(values)
    n = len(ordered)
    mean = statistics.mean(ordered)
    stdev = statistics.pstdev(ordered)
    epsilon = 1e-12
    total = 0.0
    for i, value in enumerate(ordered, start=1):
        cdf_left = min(1 - epsilon, max(epsilon, normal_cdf(value, mean, stdev)))
        cdf_right = min(1 - epsilon, max(epsilon, normal_cdf(ordered[n - i], mean, stdev)))
        total += (2 * i - 1) * (math.log(cdf_left) + math.log(1 - cdf_right))
    statistic = -n - total / n
    corrected = statistic * (1 + 0.75 / n + 2.25 / (n * n))
    return {
        "statistic": statistic,
        "corrected_statistic": corrected,
        "critical_values_normal": NORMAL_AD_CRITICAL_VALUES,
        "reject_normal_at_5pct": corrected > NORMAL_AD_CRITICAL_VALUES["5%"],
    }


def kolmogorov_smirnov_normal(values: List[float]) -> Dict[str, float]:
    ordered = sorted(values)
    n = len(ordered)
    mean = statistics.mean(ordered)
    stdev = statistics.pstdev(ordered)
    d_stat = 0.0
    for i, value in enumerate(ordered, start=1):
        cdf = normal_cdf(value, mean, stdev)
        d_plus = i / n - cdf
        d_minus = cdf - (i - 1) / n
        d_stat = max(d_stat, d_plus, d_minus)
    # Large-sample approximation for the one-sample K-S test.
    en = math.sqrt(n)
    p_value = 2 * sum((-1) ** (k - 1) * math.exp(-2 * k * k * (d_stat * en) ** 2) for k in range(1, 50))
    return {
        "d_statistic": d_stat,
        "approx_p_value": max(0.0, min(1.0, p_value)),
        "reject_normal_at_5pct": p_value < 0.05,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Round 1 backtest and robustness diagnostics.")
    parser.add_argument("--mc-chains", type=int, default=4)
    parser.add_argument("--mc-draws", type=int, default=40)
    parser.add_argument("--seed", type=int, default=825)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    by_day_ts = load_price_rows()
    deterministic = run_backtest(by_day_ts)
    grid = parameter_grid(by_day_ts)
    mc = monte_carlo(by_day_ts, args.mc_chains, args.mc_draws, args.seed)

    output = {
        "data_dir": str(DATA_DIR),
        "diagnostic_note": (
            "Geweke and Gelman-Rubin are used here to check Monte Carlo simulation stability, "
            "not as proof that a deterministic trading edge is stationary."
        ),
        "deterministic_backtest": deterministic,
        "parameter_grid": grid,
        "monte_carlo": mc,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(f"deterministic combined pnl: {deterministic['combined_pnl']:.1f}")
    print(f"grid target pass rate: {grid['target_pass_rate']:.2%}")
    print(f"mc mean pnl: {mc['summary']['mean']:.1f}")
    print(f"mc p05 pnl: {mc['summary']['p05']:.1f}")
    print(f"mc probability >= 200k: {mc['probability_above_200k']:.2%}")
    print(f"mc rhat: {mc['gelman_rubin_rhat']:.4f}")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
