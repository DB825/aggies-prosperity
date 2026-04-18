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

from datamodel import Listing, Observation, Order, OrderDepth, Trade, TradingState
from trader import Trader


PRODUCTS = ("ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT")
DATA_DIR = ROOT / "data" / "round2"
OUTPUT_PATH = ROOT / "logs" / "round2_diagnostics.json"
NORMAL_AD_CRITICAL_VALUES = {
    "15%": 0.576,
    "10%": 0.656,
    "5%": 0.787,
    "2.5%": 0.918,
    "1%": 1.092,
}


def load_price_rows() -> Dict[int, Dict[int, List[Dict]]]:
    by_day_ts: Dict[int, Dict[int, List[Dict]]] = {}
    for path in sorted(DATA_DIR.glob("prices_round_2_day_*.csv")):
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


def load_trade_rows() -> Dict[int, Dict[int, Dict[str, List[Trade]]]]:
    by_day_ts: Dict[int, Dict[int, Dict[str, List[Trade]]]] = {}
    for path in sorted(DATA_DIR.glob("trades_round_2_day_*.csv")):
        day = int(path.stem.rsplit("_day_", 1)[1])
        with path.open(newline="") as file:
            for row in csv.DictReader(file, delimiter=";"):
                trade = Trade(
                    row["symbol"],
                    int(float(row["price"])),
                    int(row["quantity"]),
                    row.get("buyer") or None,
                    row.get("seller") or None,
                    int(row["timestamp"]),
                )
                by_day_ts.setdefault(day, {}).setdefault(trade.timestamp, {}).setdefault(trade.symbol, []).append(trade)
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


def scale_price_rows(by_day_ts: Dict[int, Dict[int, List[Dict]]], volume_scale: float) -> Dict[int, Dict[int, List[Dict]]]:
    scaled: Dict[int, Dict[int, List[Dict]]] = {}
    for day, by_ts in by_day_ts.items():
        for timestamp, rows in by_ts.items():
            scaled_rows = []
            for row in rows:
                copied = dict(row)
                for level in (1, 2, 3):
                    for side in ("bid", "ask"):
                        key = f"{side}_volume_{level}"
                        if copied[key] is not None:
                            copied[key] = max(1, int(round(abs(copied[key]) * volume_scale)))
                scaled_rows.append(copied)
            scaled.setdefault(day, {})[timestamp] = scaled_rows
    return scaled


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
    trade_by_day_ts: Optional[Dict[int, Dict[int, Dict[str, List[Trade]]]]] = None,
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
            if trade_by_day_ts:
                timestamp_trades = trade_by_day_ts.get(day, {}).get(timestamp, {})
                market_trades = {product: timestamp_trades.get(product, []) for product in PRODUCTS}
            else:
                market_trades = {product: [] for product in PRODUCTS}
            state = TradingState(
                trader_data,
                timestamp,
                listings,
                depths,
                {product: [] for product in PRODUCTS},
                market_trades,
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
                split = "train" if day in (-1, 0) else "holdout"
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


def parameter_grid(
    by_day_ts: Dict[int, Dict[int, List[Dict]]],
    trade_by_day_ts: Optional[Dict[int, Dict[int, Dict[str, List[Trade]]]]] = None,
) -> Dict:
    active_default = run_backtest(by_day_ts, trade_by_day_ts=trade_by_day_ts)
    active_default_quote = run_backtest(by_day_ts)
    restored_overrides = {
        "params": {
            "INTARIAN_PEPPER_ROOT": {"max_take": 10},
            "ASH_COATED_OSMIUM": {
                "imbalance_weight": 0.4,
                "cross_inventory_skew": 1.0,
                "inventory_imbalance_threshold": 10_000,
                "anchor_pull": 0.0,
            },
        }
    }
    reference = run_backtest(by_day_ts, trade_by_day_ts=trade_by_day_ts, overrides=restored_overrides)
    reference_quote = run_backtest(by_day_ts, overrides=restored_overrides)
    reference_day_pnls = [day["total_pnl"] for day in reference["day_results"]]
    reference_min_day = min(reference_day_pnls)
    reference_ash_abs_end = max(
        abs(day["position"].get("ASH_COATED_OSMIUM", 0)) for day in reference["day_results"]
    )

    rows = []

    def score_result(name: str, family: str, overrides: Dict, result: Dict) -> None:
        quote_result = run_backtest(by_day_ts, overrides=overrides) if trade_by_day_ts else result
        day_pnls = [day["total_pnl"] for day in result["day_results"]]
        quote_day_pnls = [day["total_pnl"] for day in quote_result["day_results"]]
        train_pnls = [day["total_pnl"] for day in result["day_results"] if day["day"] in (-1, 0)]
        holdout = next(day for day in result["day_results"] if day["day"] == 1)
        quote_holdout = next(day for day in quote_result["day_results"] if day["day"] == 1)
        train_stdev = statistics.pstdev(train_pnls) if len(train_pnls) > 1 else 0.0
        ash_abs_end = max(
            abs(day["position"].get("ASH_COATED_OSMIUM", 0)) for day in result["day_results"]
        )
        quote_ash_abs_end = max(
            abs(day["position"].get("ASH_COATED_OSMIUM", 0)) for day in quote_result["day_results"]
        )
        min_day = min(day_pnls)
        quote_min_day = min(quote_day_pnls)
        quote_delta = quote_result["combined_pnl"] - reference_quote["combined_pnl"]
        trade_delta = result["combined_pnl"] - reference["combined_pnl"]
        risk_penalty = 2.0 * max(0, ash_abs_end - reference_ash_abs_end)
        risk_penalty += 2.0 * max(0, quote_ash_abs_end - reference_ash_abs_end)
        risk_penalty += 0.25 * max(0.0, reference_min_day - min_day)
        risk_penalty += 0.25 * max(0.0, min(reference_day_pnls) - quote_min_day)
        robust_score = min(quote_delta, trade_delta) + 0.25 * (quote_delta + trade_delta) - risk_penalty
        rows.append(
            {
                "name": name,
                "family": family,
                "overrides": overrides,
                "combined_pnl": result["combined_pnl"],
                "quote_only_pnl": quote_result["combined_pnl"],
                "quote_only_delta_vs_restored": quote_delta,
                "trade_aware_delta_vs_restored": trade_delta,
                "profit_above_200k": result["combined_pnl"] - 200_000,
                "train_total_pnl": sum(train_pnls),
                "train_min_day_pnl": min(train_pnls),
                "train_stdev_day_pnl": train_stdev,
                "holdout_day1_pnl": holdout["total_pnl"],
                "quote_only_holdout_day1_pnl": quote_holdout["total_pnl"],
                "min_day_pnl": min_day,
                "quote_only_min_day_pnl": quote_min_day,
                "pnl_by_product": {
                    product: sum(day["pnl_by_product"].get(product, 0.0) for day in result["day_results"])
                    for product in PRODUCTS
                },
                "quote_only_pnl_by_product": {
                    product: sum(day["pnl_by_product"].get(product, 0.0) for day in quote_result["day_results"])
                    for product in PRODUCTS
                },
                "end_positions": {str(day["day"]): day["position"] for day in result["day_results"]},
                "quote_only_end_positions": {
                    str(day["day"]): day["position"] for day in quote_result["day_results"]
                },
                "max_abs_end_osmium_inventory": ash_abs_end,
                "quote_only_max_abs_end_osmium_inventory": quote_ash_abs_end,
                "selection_robust_score": robust_score,
            }
        )

    score_result("active_default", "active", {}, active_default)
    score_result("restored_second_iteration", "reference", restored_overrides, reference)

    pepper_candidates = []
    for intercept_alpha in (0.18,):
        for trend_buy_edge in (7.0, 8.0, 9.0):
            for max_take in (8, 10, 12):
                for max_make in (24, 30):
                    overrides = {
                        "params": {
                            "INTARIAN_PEPPER_ROOT": {
                                "intercept_alpha": intercept_alpha,
                                "trend_buy_edge": trend_buy_edge,
                                "max_take": max_take,
                                "max_make": max_make,
                            }
                        }
                    }
                    result = run_backtest(by_day_ts, trade_by_day_ts=trade_by_day_ts, overrides=overrides)
                    name = f"pepper_a{intercept_alpha}_edge{trend_buy_edge}_take{max_take}_make{max_make}"
                    score_result(name, "pepper_only", overrides, result)
                    pepper_candidates.append(rows[-1])

    ash_candidates = []
    for fair_alpha in (0.08, 0.10, 0.12):
        for imbalance_weight in (0.3, 0.4, 0.5):
            for cross_inventory_skew in (1.1, 1.25, 1.4):
                for anchor_pull in (0.0, 0.02, 0.04, 0.06):
                    overrides = {
                        "params": {
                            "ASH_COATED_OSMIUM": {
                                "fair_alpha": fair_alpha,
                                "imbalance_weight": imbalance_weight,
                                "cross_inventory_skew": cross_inventory_skew,
                                "anchor_pull": anchor_pull,
                                "inventory_imbalance_threshold": 10_000,
                            }
                        }
                    }
                    result = run_backtest(by_day_ts, trade_by_day_ts=trade_by_day_ts, overrides=overrides)
                    name = f"ash_a{fair_alpha}_imb{imbalance_weight}_x{cross_inventory_skew}_ap{anchor_pull}"
                    score_result(name, "osmium_only", overrides, result)
                    ash_candidates.append(rows[-1])

    top_pepper = sorted(pepper_candidates, key=lambda row: row["selection_robust_score"], reverse=True)[:5]
    top_ash = sorted(ash_candidates, key=lambda row: row["selection_robust_score"], reverse=True)[:8]
    for pepper_row in top_pepper:
        for ash_row in top_ash:
            overrides = {"params": {}}
            for source in (pepper_row["overrides"], ash_row["overrides"]):
                for product, values in source.get("params", {}).items():
                    overrides["params"].setdefault(product, {}).update(values)
            result = run_backtest(by_day_ts, trade_by_day_ts=trade_by_day_ts, overrides=overrides)
            score_result(
                f"combo_{pepper_row['name']}__{ash_row['name']}",
                "combined_top_pairs",
                overrides,
                result,
            )

    pnls = [row["combined_pnl"] for row in rows]
    train_pnls = [row["train_total_pnl"] for row in rows]
    sorted_rows = sorted(rows, key=lambda row: row["combined_pnl"], reverse=True)
    sorted_robust_rows = sorted(rows, key=lambda row: row["selection_robust_score"], reverse=True)
    risk_validated = [
        row
        for row in rows
        if row["quote_only_delta_vs_restored"] >= 0
        and row["trade_aware_delta_vs_restored"] >= 0
        and row["min_day_pnl"] >= reference_min_day - 150.0
        and row["quote_only_min_day_pnl"] >= min(reference_day_pnls) - 150.0
        and row["max_abs_end_osmium_inventory"] <= reference_ash_abs_end + 4
        and row["quote_only_max_abs_end_osmium_inventory"] <= reference_ash_abs_end + 4
        and row["holdout_day1_pnl"] >= reference["day_results"][2]["total_pnl"] - 150.0
        and row["quote_only_holdout_day1_pnl"] >= reference_quote["day_results"][2]["total_pnl"] - 150.0
    ]
    selected = max(risk_validated, key=lambda row: row["selection_robust_score"])
    risk_capped = [row for row in rows if row["max_abs_end_osmium_inventory"] <= 74]
    risk_capped_sorted = sorted(risk_capped, key=lambda r: r["combined_pnl"], reverse=True)
    return {
        "rows_evaluated": len(rows),
        "profit_target": 200_000,
        "active_default": rows[0],
        "active_default_quote_only": {
            "combined_pnl": active_default_quote["combined_pnl"],
            "day_results": active_default_quote["day_results"],
        },
        "restored_second_iteration": rows[1],
        "summary": summarize(pnls),
        "train_summary": summarize(train_pnls),
        "selection_protocol": (
            "Round 2 has only three historical days, so selection is constrained. "
            "Candidates are screened on deterministic PnL, train days -1 and 0, "
            "day-1 holdout, minimum-day PnL, and terminal osmium inventory. The chosen "
            "candidate must improve PnL without materially worsening those risk proxies."
        ),
        "risk_validated_size": len(risk_validated),
        "target_pass_rate": sum(pnl >= 200_000 for pnl in pnls) / len(pnls),
        "selected": selected,
        "selected_combined_rank": 1 + sorted_rows.index(selected),
        "selected_robust_rank": 1 + sorted_robust_rows.index(selected),
        "top_10_by_robust_score": sorted_robust_rows[:10],
        "top_10_by_pnl": sorted_rows[:10],
        "top_10_risk_capped": risk_capped_sorted[:10],
        "risk_capped_size": len(risk_capped),
        "bottom_5_by_pnl": sorted_rows[-5:],
    }


def monte_carlo(
    by_day_ts: Dict[int, Dict[int, List[Dict]]],
    trade_by_day_ts: Optional[Dict[int, Dict[int, Dict[str, List[Trade]]]]],
    chains: int,
    draws: int,
    seed: int,
    overrides: Optional[Dict] = None,
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
            result = run_backtest(
                by_day_ts,
                trade_by_day_ts=trade_by_day_ts,
                overrides=overrides,
                execution=stress,
                seed=seed + chain * 10_000 + draw,
            )
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


def volume_sensitivity(by_day_ts: Dict[int, Dict[int, List[Dict]]]) -> Dict[str, Dict]:
    output = {}
    for scale in (0.8, 1.0, 1.25):
        result = run_backtest(scale_price_rows(by_day_ts, scale))
        output[str(scale)] = {
            "day_results": result["day_results"],
            "combined_pnl": result["combined_pnl"],
            "filled_orders": result["filled_orders"],
            "filled_quantity": result["filled_quantity"],
            "osmium_pnl": sum(day["pnl_by_product"]["ASH_COATED_OSMIUM"] for day in result["day_results"]),
            "min_day_pnl": min(day["total_pnl"] for day in result["day_results"]),
            "end_osmium_inventory": [
                day["position"]["ASH_COATED_OSMIUM"] for day in result["day_results"]
            ],
        }
    return output


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
    parser = argparse.ArgumentParser(description="Run Round 2 backtest and robustness diagnostics.")
    parser.add_argument("--mc-chains", type=int, default=4)
    parser.add_argument("--mc-draws", type=int, default=40)
    parser.add_argument("--seed", type=int, default=825)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    by_day_ts = load_price_rows()
    trade_by_day_ts = load_trade_rows()
    deterministic = run_backtest(by_day_ts)
    trade_aware = run_backtest(by_day_ts, trade_by_day_ts=trade_by_day_ts)
    signal_screen = linear_signal_screen(by_day_ts)
    risk_controls = risk_control_summary(by_day_ts)
    grid = parameter_grid(by_day_ts, trade_by_day_ts=trade_by_day_ts)
    selected_overrides = grid["selected"]["overrides"]
    selected_deterministic = run_backtest(
        by_day_ts,
        trade_by_day_ts=trade_by_day_ts,
        overrides=selected_overrides,
    )
    volume = volume_sensitivity(by_day_ts)
    mc = monte_carlo(
        by_day_ts,
        None,
        args.mc_chains,
        args.mc_draws,
        args.seed,
        overrides=selected_overrides,
    )

    output = {
        "data_dir": str(DATA_DIR),
        "diagnostic_note": (
            "Geweke and Gelman-Rubin are used here to check Monte Carlo simulation stability, "
            "not as proof that a deterministic trading edge is stationary."
        ),
        "deterministic_backtest": deterministic,
        "trade_aware_backtest": trade_aware,
        "selected_deterministic_backtest": selected_deterministic,
        "linear_signal_screen": signal_screen,
        "risk_controls": risk_controls,
        "parameter_grid": grid,
        "volume_sensitivity": volume,
        "monte_carlo": mc,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(f"deterministic combined pnl: {deterministic['combined_pnl']:.1f}")
    print(f"deterministic profit above 200k: {deterministic['combined_pnl'] - 200_000:.1f}")
    print(f"trade-aware combined pnl: {trade_aware['combined_pnl']:.1f}")
    print(f"grid target pass rate: {grid['target_pass_rate']:.2%}")
    print(f"selected robust-rank: {grid['selected_robust_rank']} / {grid['summary']['count']}")
    print(f"selected combined-rank: {grid['selected_combined_rank']} / {grid['summary']['count']}")
    print(f"selected deterministic pnl: {selected_deterministic['combined_pnl']:.1f}")
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
