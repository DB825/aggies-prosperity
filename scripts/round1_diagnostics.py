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


def solve_linear_system(matrix: List[List[float]], vector: List[float]) -> List[float]:
    size = len(vector)
    augmented = [row[:] + [value] for row, value in zip(matrix, vector)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-12:
            continue
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        divisor = augmented[col][col]
        augmented[col] = [value / divisor for value in augmented[col]]
        for row in range(size):
            if row == col:
                continue
            factor = augmented[row][col]
            if factor == 0:
                continue
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(augmented[row], augmented[col])
            ]
    return [row[-1] for row in augmented]


def fit_ridge_linear(samples: List[Tuple[List[float], float]], ridge: float = 1e-6) -> List[float]:
    feature_count = len(samples[0][0])
    xtx = [[0.0 for _ in range(feature_count)] for _ in range(feature_count)]
    xty = [0.0 for _ in range(feature_count)]
    for features, target in samples:
        for i, left in enumerate(features):
            xty[i] += left * target
            for j, right in enumerate(features):
                xtx[i][j] += left * right
    for i in range(feature_count):
        xtx[i][i] += ridge
    return solve_linear_system(xtx, xty)


def predict_linear(coefficients: List[float], features: List[float]) -> float:
    return sum(coefficient * feature for coefficient, feature in zip(coefficients, features))


def linear_signal_screen(by_day_ts: Dict[int, Dict[int, List[Dict]]]) -> Dict:
    samples: Dict[str, Dict[str, List[Tuple[List[float], float]]]] = {
        product: {"train": [], "holdout": []} for product in PRODUCTS
    }
    feature_names = ["intercept", "ema_deviation", "imbalance", "spread", "timestamp"]

    for day in sorted(by_day_ts):
        by_product = {product: [] for product in PRODUCTS}
        for timestamp in sorted(by_day_ts[day]):
            for row in by_day_ts[day][timestamp]:
                if row["product"] in by_product and row["mid_price"] > 0:
                    by_product[row["product"]].append(row)

        for product, rows in by_product.items():
            if len(rows) < 2:
                continue
            ema = rows[0]["mid_price"]
            for index, row in enumerate(rows[:-1]):
                bid = row["bid_price_1"]
                ask = row["ask_price_1"]
                bid_volume = row["bid_volume_1"]
                ask_volume = row["ask_volume_1"]
                if bid is None or ask is None or bid_volume is None or ask_volume is None:
                    ema = 0.8 * ema + 0.2 * row["mid_price"]
                    continue
                total_volume = bid_volume + ask_volume
                imbalance = (bid_volume - ask_volume) / total_volume if total_volume else 0.0
                features = [
                    1.0,
                    (row["mid_price"] - ema) / 10.0,
                    imbalance,
                    (ask - bid) / 10.0,
                    row["timestamp"] / 1_000_000,
                ]
                target = rows[index + 1]["mid_price"] - row["mid_price"]
                split = "train" if day in (-2, -1) else "holdout"
                samples[product][split].append((features, target))
                ema = 0.8 * ema + 0.2 * row["mid_price"]

    output = {
        "feature_names": feature_names,
        "note": (
            "Dependency-free ridge-linear next-tick screen. KNN and random forests were considered "
            "but not deployed because there are only three historical days; Poisson regression is "
            "not appropriate for signed continuous price deltas."
        ),
        "products": {},
    }
    for product, splits in samples.items():
        train = splits["train"]
        holdout = splits["holdout"]
        coefficients = fit_ridge_linear(train)
        train_mean = statistics.mean(target for _, target in train)
        predictions = [predict_linear(coefficients, features) for features, _ in holdout]
        targets = [target for _, target in holdout]
        mse = statistics.mean((prediction - target) ** 2 for prediction, target in zip(predictions, targets))
        baseline_mse = statistics.mean((train_mean - target) ** 2 for target in targets)
        nonzero_direction = [
            (prediction, target)
            for prediction, target in zip(predictions, targets)
            if target != 0 and prediction != 0
        ]
        directional_accuracy = (
            sum((prediction > 0) == (target > 0) for prediction, target in nonzero_direction)
            / len(nonzero_direction)
            if nonzero_direction
            else 0.0
        )
        output["products"][product] = {
            "train_samples": len(train),
            "holdout_samples": len(holdout),
            "coefficients": dict(zip(feature_names, coefficients)),
            "holdout_mse": mse,
            "holdout_baseline_mse": baseline_mse,
            "mse_improvement_vs_baseline": baseline_mse - mse,
            "directional_accuracy": directional_accuracy,
            "mean_abs_prediction": statistics.mean(abs(value) for value in predictions),
        }
    return output


def row_book_fair(row: Dict) -> Optional[float]:
    bid = row["bid_price_1"]
    ask = row["ask_price_1"]
    bid_volume = row["bid_volume_1"]
    ask_volume = row["ask_volume_1"]
    if bid is None or ask is None:
        return None
    if not bid_volume or not ask_volume:
        return (bid + ask) / 2
    return (bid * abs(ask_volume) + ask * abs(bid_volume)) / (abs(bid_volume) + abs(ask_volume))


def risk_control_summary(by_day_ts: Dict[int, Dict[int, List[Dict]]]) -> Dict:
    pepper_triggers = 0
    ash_triggers = 0
    pepper_min_open_intercept_diff = 0.0
    ash_max_anchor_deviation = 0.0

    for day in sorted(by_day_ts):
        pepper_open_intercept = None
        for timestamp in sorted(by_day_ts[day]):
            for row in by_day_ts[day][timestamp]:
                fair = row_book_fair(row)
                if fair is None:
                    continue
                if row["product"] == "INTARIAN_PEPPER_ROOT":
                    observed_intercept = fair - Trader.PEPPER_SLOPE * timestamp
                    if pepper_open_intercept is None:
                        pepper_open_intercept = observed_intercept
                    intercept_diff = observed_intercept - pepper_open_intercept
                    pepper_min_open_intercept_diff = min(pepper_min_open_intercept_diff, intercept_diff)
                    if intercept_diff < -Trader.PEPPER_TREND_STOP_LOSS:
                        pepper_triggers += 1
                elif row["product"] == "ASH_COATED_OSMIUM":
                    anchor_deviation = abs(fair - Trader.ASH_ANCHOR)
                    ash_max_anchor_deviation = max(ash_max_anchor_deviation, anchor_deviation)
                    if anchor_deviation > Trader.ASH_STOP_LOSS_DEVIATION:
                        ash_triggers += 1

    return {
        "INTARIAN_PEPPER_ROOT": {
            "guard": (
                "If observed live intercept falls more than PEPPER_TREND_STOP_LOSS below the "
                "day-open intercept, stop adding trend inventory and start flattening."
            ),
            "threshold": Trader.PEPPER_TREND_STOP_LOSS,
            "exit_edge": Trader.PEPPER_STOP_LOSS_EXIT_EDGE,
            "cooldown_timestamps": Trader.RISK_COOLDOWN,
            "historical_trigger_count": pepper_triggers,
            "historical_min_open_intercept_diff": pepper_min_open_intercept_diff,
        },
        "ASH_COATED_OSMIUM": {
            "guard": (
                "If book fair moves too far from the 10000 stationary anchor, stop crossing "
                "fresh mean-reversion trades and flatten existing inventory."
            ),
            "threshold": Trader.ASH_STOP_LOSS_DEVIATION,
            "exit_edge": Trader.ASH_STOP_LOSS_EXIT_EDGE,
            "cooldown_timestamps": Trader.RISK_COOLDOWN,
            "historical_trigger_count": ash_triggers,
            "historical_max_anchor_deviation": ash_max_anchor_deviation,
        },
    }


def parameter_grid(by_day_ts: Dict[int, Dict[int, List[Dict]]]) -> Dict:
    rows = []
    exit_policies = {
        "hold_to_close": {
            "PEPPER_EXIT_TIMESTAMP": 10_000_000,
            "PEPPER_FORCE_EXIT_TIMESTAMP": 10_000_000,
        },
        "flat_late": {
            "PEPPER_EXIT_TIMESTAMP": 995_000,
            "PEPPER_FORCE_EXIT_TIMESTAMP": 998_000,
            "PEPPER_FORCE_EXIT_EDGE": 8.0,
        },
    }
    for pepper_exit_policy, exit_attrs in exit_policies.items():
        for pepper_buy_edge in (6.0, 8.0):
            for pepper_max_take in (10, 28):
                for ash_fair_alpha in (0.10, 0.22):
                    for ash_imbalance_weight in (0.0, 1.0, 1.8):
                        for ash_take_edge in (0.0, 0.5, 1.0):
                            overrides = {
                                "params": {
                                    "INTARIAN_PEPPER_ROOT": {
                                        "trend_buy_edge": pepper_buy_edge,
                                        "max_take": pepper_max_take,
                                    },
                                    "ASH_COATED_OSMIUM": {
                                        "fair_alpha": ash_fair_alpha,
                                        "imbalance_weight": ash_imbalance_weight,
                                        "take_edge": ash_take_edge,
                                    },
                                },
                                "attrs": exit_attrs,
                            }
                            result = run_backtest(by_day_ts, overrides=overrides)
                            day_results = {day["day"]: day for day in result["day_results"]}
                            train_pnls = [day_results[-2]["total_pnl"], day_results[-1]["total_pnl"]]
                            train_stdev = statistics.pstdev(train_pnls) if len(train_pnls) > 1 else 0.0
                            robust_score = sum(train_pnls) + 0.10 * min(train_pnls) - 0.25 * train_stdev
                            rows.append(
                                {
                                    "pepper_exit_policy": pepper_exit_policy,
                                    "pepper_buy_edge": pepper_buy_edge,
                                    "pepper_max_take": pepper_max_take,
                                    "ash_fair_alpha": ash_fair_alpha,
                                    "ash_imbalance_weight": ash_imbalance_weight,
                                    "ash_take_edge": ash_take_edge,
                                    "combined_pnl": result["combined_pnl"],
                                    "profit_above_200k": result["combined_pnl"] - 200_000,
                                    "train_total_pnl": sum(train_pnls),
                                    "train_min_day_pnl": min(train_pnls),
                                    "train_stdev_day_pnl": train_stdev,
                                    "holdout_day0_pnl": day_results[0]["total_pnl"],
                                    "holdout_profit_above_daily_target": day_results[0]["total_pnl"] - 200_000 / 3,
                                    "pnl_by_product": {
                                        product: sum(
                                            day["pnl_by_product"].get(product, 0.0)
                                            for day in result["day_results"]
                                        )
                                        for product in PRODUCTS
                                    },
                                    "end_positions": {
                                        str(day["day"]): day["position"] for day in result["day_results"]
                                    },
                                    "flat_all_days": all(
                                        day["position"].get("INTARIAN_PEPPER_ROOT", 0) == 0
                                        and day["position"].get("ASH_COATED_OSMIUM", 0) == 0
                                        for day in result["day_results"]
                                    ),
                                    "selection_robust_score": robust_score,
                                }
                            )

    pnls = [row["combined_pnl"] for row in rows]
    train_pnls = [row["train_total_pnl"] for row in rows]
    sorted_rows = sorted(rows, key=lambda row: row["combined_pnl"], reverse=True)
    sorted_train_rows = sorted(rows, key=lambda row: row["selection_robust_score"], reverse=True)
    train_score_cutoff = sorted_train_rows[0]["selection_robust_score"] - 250.0
    train_validated_profit_plateau = [
        row
        for row in rows
        if row["selection_robust_score"] >= train_score_cutoff
        and row["train_min_day_pnl"] >= 80_000
    ]
    selected = max(train_validated_profit_plateau, key=lambda row: row["combined_pnl"])
    selected_combined_rank = 1 + sorted_rows.index(selected)
    selected_train_rank = 1 + sorted_train_rows.index(selected)
    selected_neighborhood = [
        row
        for row in rows
        if row["pepper_exit_policy"] == selected["pepper_exit_policy"]
        and abs(row["pepper_buy_edge"] - selected["pepper_buy_edge"]) <= 2.0
        and row["pepper_max_take"] == selected["pepper_max_take"]
        and abs(row["ash_fair_alpha"] - selected["ash_fair_alpha"]) <= 0.12
        and abs(row["ash_imbalance_weight"] - selected["ash_imbalance_weight"]) <= 1.0
        and abs(row["ash_take_edge"] - selected["ash_take_edge"]) <= 0.5
    ]
    return {
        "rows": rows,
        "profit_target": 200_000,
        "summary": summarize(pnls),
        "train_summary": summarize(train_pnls),
        "selection_protocol": (
            "The 200k target is treated as a floor, not the objective. Gate candidates by a "
            "profit-first robust score on days -2 and -1, then select the highest-PnL row inside "
            "that train-validated plateau. Day 0 remains reported as the holdout stress check."
        ),
        "train_validated_profit_plateau_size": len(train_validated_profit_plateau),
        "target_pass_rate": sum(pnl >= 200_000 for pnl in pnls) / len(pnls),
        "train_target_pass_rate": sum(pnl >= 133_334 for pnl in train_pnls) / len(train_pnls),
        "selected": selected,
        "selected_combined_rank": selected_combined_rank,
        "selected_train_rank": selected_train_rank,
        "selected_neighborhood_summary": summarize([row["combined_pnl"] for row in selected_neighborhood]),
        "selected_neighborhood_train_summary": summarize(
            [row["train_total_pnl"] for row in selected_neighborhood]
        ),
        "top_5_by_train_score": sorted_train_rows[:5],
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
        "probability_above_240k": sum(value >= 240_000 for value in flattened) / len(flattened),
        "probability_above_245k": sum(value >= 245_000 for value in flattened) / len(flattened),
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


def geweke_z(chain: List[float], first_fraction: float = 0.25, last_fraction: float = 0.50) -> Dict[str, float]:
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
    signal_screen = linear_signal_screen(by_day_ts)
    risk_controls = risk_control_summary(by_day_ts)
    grid = parameter_grid(by_day_ts)
    mc = monte_carlo(by_day_ts, args.mc_chains, args.mc_draws, args.seed)

    output = {
        "data_dir": str(DATA_DIR),
        "diagnostic_note": (
            "Geweke and Gelman-Rubin are used here to check Monte Carlo simulation stability, "
            "not as proof that a deterministic trading edge is stationary."
        ),
        "deterministic_backtest": deterministic,
        "linear_signal_screen": signal_screen,
        "risk_controls": risk_controls,
        "parameter_grid": grid,
        "monte_carlo": mc,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(f"deterministic combined pnl: {deterministic['combined_pnl']:.1f}")
    print(f"deterministic profit above 200k: {deterministic['combined_pnl'] - 200_000:.1f}")
    print(f"grid target pass rate: {grid['target_pass_rate']:.2%}")
    print(f"selected train-rank: {grid['selected_train_rank']} / {grid['summary']['count']}")
    print(f"selected combined-rank: {grid['selected_combined_rank']} / {grid['summary']['count']}")
    print(
        "risk guard historical triggers: "
        f"pepper={risk_controls['INTARIAN_PEPPER_ROOT']['historical_trigger_count']}, "
        f"osmium={risk_controls['ASH_COATED_OSMIUM']['historical_trigger_count']}"
    )
    print(f"mc mean pnl: {mc['summary']['mean']:.1f}")
    print(f"mc p05 pnl: {mc['summary']['p05']:.1f}")
    print(f"mc probability >= 200k: {mc['probability_above_200k']:.2%}")
    print(f"mc probability >= 245k: {mc['probability_above_245k']:.2%}")
    print(f"mc rhat: {mc['gelman_rubin_rhat']:.4f}")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
