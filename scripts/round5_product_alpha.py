import argparse
import csv
import json
import math
import sys
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trader import Trader


DATA_DIR = ROOT / "data" / "round5"
OUTPUT_PATH = ROOT / "logs" / "round5_product_alpha.json"
POSITION_LIMIT = Trader.POSITION_LIMIT

PRODUCT_GROUPS = {
    "galaxy_sounds": [
        "GALAXY_SOUNDS_DARK_MATTER",
        "GALAXY_SOUNDS_BLACK_HOLES",
        "GALAXY_SOUNDS_PLANETARY_RINGS",
        "GALAXY_SOUNDS_SOLAR_WINDS",
        "GALAXY_SOUNDS_SOLAR_FLAMES",
    ],
    "sleep_pods": [
        "SLEEP_POD_SUEDE",
        "SLEEP_POD_LAMB_WOOL",
        "SLEEP_POD_POLYESTER",
        "SLEEP_POD_NYLON",
        "SLEEP_POD_COTTON",
    ],
    "microchips": [
        "MICROCHIP_CIRCLE",
        "MICROCHIP_OVAL",
        "MICROCHIP_SQUARE",
        "MICROCHIP_RECTANGLE",
        "MICROCHIP_TRIANGLE",
    ],
    "pebbles": ["PEBBLES_XS", "PEBBLES_S", "PEBBLES_M", "PEBBLES_L", "PEBBLES_XL"],
    "robots": [
        "ROBOT_VACUUMING",
        "ROBOT_MOPPING",
        "ROBOT_DISHES",
        "ROBOT_LAUNDRY",
        "ROBOT_IRONING",
    ],
    "uv_visors": [
        "UV_VISOR_YELLOW",
        "UV_VISOR_AMBER",
        "UV_VISOR_ORANGE",
        "UV_VISOR_RED",
        "UV_VISOR_MAGENTA",
    ],
    "translators": [
        "TRANSLATOR_SPACE_GRAY",
        "TRANSLATOR_ASTRO_BLACK",
        "TRANSLATOR_ECLIPSE_CHARCOAL",
        "TRANSLATOR_GRAPHITE_MIST",
        "TRANSLATOR_VOID_BLUE",
    ],
    "panels": ["PANEL_1X2", "PANEL_2X2", "PANEL_1X4", "PANEL_2X4", "PANEL_4X4"],
    "oxygen": [
        "OXYGEN_SHAKE_MORNING_BREATH",
        "OXYGEN_SHAKE_EVENING_BREATH",
        "OXYGEN_SHAKE_MINT",
        "OXYGEN_SHAKE_CHOCOLATE",
        "OXYGEN_SHAKE_GARLIC",
    ],
    "snackpacks": [
        "SNACKPACK_CHOCOLATE",
        "SNACKPACK_VANILLA",
        "SNACKPACK_PISTACHIO",
        "SNACKPACK_STRAWBERRY",
        "SNACKPACK_RASPBERRY",
    ],
}


def load_prices(max_timestamp: int) -> pd.DataFrame:
    frame = pd.concat(
        [pd.read_csv(path, sep=";") for path in sorted(DATA_DIR.glob("prices_round_5_day_*.csv"))],
        ignore_index=True,
    )
    frame = frame[frame["timestamp"] <= max_timestamp].copy()
    numeric_columns = [
        "bid_price_1",
        "bid_volume_1",
        "bid_price_2",
        "bid_volume_2",
        "bid_price_3",
        "bid_volume_3",
        "ask_price_1",
        "ask_volume_1",
        "ask_price_2",
        "ask_volume_2",
        "ask_price_3",
        "ask_volume_3",
        "mid_price",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.sort_values(["product", "day", "timestamp"]).reset_index(drop=True)

    frame["spread"] = frame["ask_price_1"] - frame["bid_price_1"]
    frame["microprice"] = (
        frame["ask_price_1"] * frame["bid_volume_1"] + frame["bid_price_1"] * frame["ask_volume_1"]
    ) / (frame["bid_volume_1"] + frame["ask_volume_1"])
    frame["micro_delta"] = frame["microprice"] - frame["mid_price"]
    frame["imbalance_1"] = (frame["bid_volume_1"] - frame["ask_volume_1"]) / (
        frame["bid_volume_1"] + frame["ask_volume_1"]
    )
    bid_volume = frame[["bid_volume_1", "bid_volume_2", "bid_volume_3"]].fillna(0).sum(axis=1)
    ask_volume = frame[["ask_volume_1", "ask_volume_2", "ask_volume_3"]].fillna(0).sum(axis=1)
    frame["imbalance_3"] = (bid_volume - ask_volume) / (bid_volume + ask_volume).replace(0, np.nan)

    grouped = frame.groupby(["day", "product"], sort=False)
    for lag in [1, 2, 3, 5, 10, 20, 50, 100]:
        frame[f"return_{lag}"] = grouped["mid_price"].diff(lag)
    for window in [10, 20, 50, 100, 200]:
        rolling = grouped["mid_price"].transform(lambda series: series.rolling(window, min_periods=2).mean())
        frame[f"z_{window}"] = frame["mid_price"] - rolling
    for horizon in [1, 3, 5]:
        frame[f"future_{horizon}"] = grouped["mid_price"].shift(-horizon) - frame["mid_price"]
    return frame


def load_trades() -> Dict[Tuple[int, int, str], List[Tuple[float, int]]]:
    trades: Dict[Tuple[int, int, str], List[Tuple[float, int]]] = defaultdict(list)
    for path in sorted(DATA_DIR.glob("trades_round_5_day_*.csv")):
        day = int(path.stem.split("_day_")[-1])
        with path.open(newline="") as file:
            for row in csv.DictReader(file, delimiter=";"):
                trades[(day, int(row["timestamp"]), row["symbol"])].append(
                    (float(row["price"]), int(float(row["quantity"])))
                )
    return trades


def day_pnl(values: Dict[int, float], days: Iterable[int]) -> float:
    return float(sum(values.get(day, 0.0) for day in days))


def loo_summary(candidates: List[Dict], days: List[int]) -> Dict:
    if not candidates:
        return {}
    folds = []
    for test_day in days:
        train_days = [day for day in days if day != test_day]
        best = max(candidates, key=lambda candidate: day_pnl(candidate["day_pnl"], train_days))
        folds.append(
            {
                "test_day": test_day,
                "train_pnl": day_pnl(best["day_pnl"], train_days),
                "test_pnl": best["day_pnl"].get(test_day, 0.0),
                "strategy": best["strategy"],
                "params": best["params"],
            }
        )
    full_best = max(candidates, key=lambda candidate: day_pnl(candidate["day_pnl"], days))
    return {
        "loo_total": float(sum(fold["test_pnl"] for fold in folds)),
        "loo_min": float(min(fold["test_pnl"] for fold in folds)),
        "folds": folds,
        "full_best": {
            "total": day_pnl(full_best["day_pnl"], days),
            "day_pnl": full_best["day_pnl"],
            "strategy": full_best["strategy"],
            "params": full_best["params"],
        },
    }


def price_levels(row: pd.Series, side: str) -> List[Tuple[int, int]]:
    levels = []
    for level in [1, 2, 3]:
        price = row[f"{side}_price_{level}"]
        volume = row[f"{side}_volume_{level}"]
        if not pd.isna(price) and not pd.isna(volume):
            levels.append((int(price), int(volume)))
    return levels


def cross_to_target(row: pd.Series, position: int, cash: float, target: int) -> Tuple[int, float]:
    target = max(-POSITION_LIMIT, min(POSITION_LIMIT, int(target)))
    delta = target - position
    if delta > 0:
        for price, volume in price_levels(row, "ask"):
            if delta <= 0:
                break
            fill = min(delta, volume)
            cash -= price * fill
            position += fill
            delta -= fill
    elif delta < 0:
        sell_left = -delta
        for price, volume in price_levels(row, "bid"):
            if sell_left <= 0:
                break
            fill = min(sell_left, volume)
            cash += price * fill
            position -= fill
            sell_left -= fill
    return position, cash


def cross_arrays(
    idx: int,
    bid_prices: List[np.ndarray],
    bid_volumes: List[np.ndarray],
    ask_prices: List[np.ndarray],
    ask_volumes: List[np.ndarray],
    position: int,
    cash: float,
    target: int,
) -> Tuple[int, float]:
    target = max(-POSITION_LIMIT, min(POSITION_LIMIT, int(target)))
    delta = target - position
    if delta > 0:
        for prices, volumes in zip(ask_prices, ask_volumes):
            price = prices[idx]
            volume = volumes[idx]
            if delta <= 0:
                break
            if np.isnan(price) or np.isnan(volume):
                continue
            fill = min(delta, int(volume))
            cash -= int(price) * fill
            position += fill
            delta -= fill
    elif delta < 0:
        sell_left = -delta
        for prices, volumes in zip(bid_prices, bid_volumes):
            price = prices[idx]
            volume = volumes[idx]
            if sell_left <= 0:
                break
            if np.isnan(price) or np.isnan(volume):
                continue
            fill = min(sell_left, int(volume))
            cash += int(price) * fill
            position -= fill
            sell_left -= fill
    return position, cash


def simulate_maker(
    product_frame: pd.DataFrame,
    trades: Dict[Tuple[int, int, str], List[Tuple[float, int]]],
    edge: float,
    skew: float,
) -> Dict[int, float]:
    output: Dict[int, float] = {}
    product = str(product_frame["product"].iloc[0])
    for day, day_frame in product_frame.groupby("day", sort=True):
        cash = 0.0
        position = 0
        last_mid = 0.0
        for row in day_frame.itertuples(index=False):
            best_bid = int(row.bid_price_1)
            best_ask = int(row.ask_price_1)
            mid = float(row.mid_price)
            last_mid = mid
            adjusted_fair = mid - skew * position
            bid_price = min(best_bid + 1, math.floor(adjusted_fair - edge))
            ask_price = max(best_ask - 1, math.ceil(adjusted_fair + edge))
            buy_quantity = max(0, POSITION_LIMIT - position) if bid_price < best_ask and bid_price < ask_price else 0
            sell_quantity = max(0, POSITION_LIMIT + position) if ask_price > best_bid and bid_price < ask_price else 0

            for trade_price, trade_quantity in trades.get((int(day), int(row.timestamp), product), []):
                if trade_price <= mid and buy_quantity > 0 and bid_price >= trade_price:
                    fill = min(buy_quantity, trade_quantity)
                    cash -= bid_price * fill
                    position += fill
                    buy_quantity -= fill
                elif trade_price >= mid and sell_quantity > 0 and ask_price <= trade_price:
                    fill = min(sell_quantity, trade_quantity)
                    cash += ask_price * fill
                    position -= fill
                    sell_quantity -= fill
        output[int(day)] = float(cash + position * last_mid)
    return output


def simulate_signal(product_frame: pd.DataFrame, signal_column: str, threshold: float, mode: str, sticky: bool) -> Dict[int, float]:
    output: Dict[int, float] = {}
    for day, day_frame in product_frame.groupby("day", sort=True):
        day_frame = day_frame.sort_values("timestamp")
        signals = day_frame[signal_column].to_numpy(float)
        mids = day_frame["mid_price"].to_numpy(float)
        bid_prices = [day_frame[f"bid_price_{level}"].to_numpy(float) for level in [1, 2, 3]]
        bid_volumes = [day_frame[f"bid_volume_{level}"].to_numpy(float) for level in [1, 2, 3]]
        ask_prices = [day_frame[f"ask_price_{level}"].to_numpy(float) for level in [1, 2, 3]]
        ask_volumes = [day_frame[f"ask_volume_{level}"].to_numpy(float) for level in [1, 2, 3]]
        cash = 0.0
        position = 0
        target = 0
        for idx, signal in enumerate(signals):
            if np.isnan(signal):
                signal = 0.0
            if abs(signal) > threshold:
                direction = 1 if signal > 0 else -1
                if mode == "reversion":
                    direction *= -1
                target = direction * POSITION_LIMIT
            elif not sticky:
                target = 0
            position, cash = cross_arrays(
                idx,
                bid_prices,
                bid_volumes,
                ask_prices,
                ask_volumes,
                position,
                cash,
                target,
            )
        output[int(day)] = float(cash + position * mids[-1])
    return output


def simulate_ml_product(product_frame: pd.DataFrame, days: List[int]) -> Dict:
    feature_columns = [
        "spread",
        "micro_delta",
        "imbalance_1",
        "imbalance_3",
        "return_1",
        "return_3",
        "return_10",
        "return_50",
        "z_20",
        "z_50",
        "z_100",
    ]
    frame = product_frame.dropna(subset=feature_columns + ["future_1"]).copy()
    if len(frame) < 200:
        return {}

    folds = []
    thresholds = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 14.0, 20.0]
    for test_day in days:
        train = frame[frame["day"] != test_day]
        test = frame[frame["day"] == test_day]
        if train.empty or test.empty:
            continue
        model = make_pipeline(StandardScaler(), Ridge(alpha=25.0))
        model.fit(train[feature_columns], train["future_1"])
        train_predictions = model.predict(train[feature_columns])
        test_predictions = model.predict(test[feature_columns])
        train_candidates = []
        for threshold in thresholds:
            train_candidates.append((simulate_prediction_taker(train, train_predictions, threshold), threshold))
        best_train_pnl, threshold = max(train_candidates, key=lambda item: item[0])
        test_pnl = simulate_prediction_taker(test, test_predictions, threshold)
        corr = float(np.corrcoef(test_predictions, test["future_1"])[0, 1])
        if math.isnan(corr):
            corr = 0.0
        folds.append(
            {
                "test_day": int(test_day),
                "train_pnl": float(best_train_pnl),
                "test_pnl": float(test_pnl),
                "threshold": threshold,
                "corr": corr,
            }
        )
    if not folds:
        return {}
    return {
        "loo_total": float(sum(fold["test_pnl"] for fold in folds)),
        "loo_min": float(min(fold["test_pnl"] for fold in folds)),
        "mean_corr": float(np.mean([fold["corr"] for fold in folds])),
        "folds": folds,
    }


def simulate_prediction_taker(frame: pd.DataFrame, predictions: np.ndarray, threshold: float) -> float:
    local = frame.copy()
    local["prediction"] = predictions
    total = 0.0
    for _, day_frame in local.sort_values(["day", "timestamp"]).groupby("day", sort=True):
        predictions_array = day_frame["prediction"].to_numpy(float)
        mids = day_frame["mid_price"].to_numpy(float)
        bid_prices = [day_frame[f"bid_price_{level}"].to_numpy(float) for level in [1, 2, 3]]
        bid_volumes = [day_frame[f"bid_volume_{level}"].to_numpy(float) for level in [1, 2, 3]]
        ask_prices = [day_frame[f"ask_price_{level}"].to_numpy(float) for level in [1, 2, 3]]
        ask_volumes = [day_frame[f"ask_volume_{level}"].to_numpy(float) for level in [1, 2, 3]]
        cash = 0.0
        position = 0
        for idx, prediction in enumerate(predictions_array):
            target = 0
            if prediction > threshold:
                target = POSITION_LIMIT
            elif prediction < -threshold:
                target = -POSITION_LIMIT
            position, cash = cross_arrays(
                idx,
                bid_prices,
                bid_volumes,
                ask_prices,
                ask_volumes,
                position,
                cash,
                target,
            )
        total += cash + position * mids[-1]
    return float(total)


def product_candidates(product_frame: pd.DataFrame, trades: Dict[Tuple[int, int, str], List[Tuple[float, int]]]) -> Dict[str, List[Dict]]:
    maker_candidates = []
    for edge in [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0]:
        for skew in [0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0]:
            maker_candidates.append(
                {
                    "strategy": "maker",
                    "params": {"edge": edge, "skew": skew},
                    "day_pnl": simulate_maker(product_frame, trades, edge, skew),
                }
            )

    signal_candidates = []
    for threshold in [5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 75.0, 100.0]:
        for mode in ["reversion", "momentum"]:
            for sticky in [True, False]:
                signal_candidates.append(
                    {
                        "strategy": "return_1",
                        "params": {"threshold": threshold, "mode": mode, "sticky": sticky},
                        "day_pnl": simulate_signal(product_frame, "return_1", threshold, mode, sticky),
                    }
                )
    for lag in [3, 5, 10, 20, 50, 100]:
        for threshold in [5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 75.0]:
            for mode in ["reversion", "momentum"]:
                signal_candidates.append(
                    {
                        "strategy": f"return_{lag}",
                        "params": {"threshold": threshold, "mode": mode, "sticky": False},
                        "day_pnl": simulate_signal(product_frame, f"return_{lag}", threshold, mode, False),
                    }
                )
    for window in [10, 20, 50, 100, 200]:
        for threshold in [5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 75.0, 100.0]:
            signal_candidates.append(
                {
                    "strategy": f"z_{window}",
                    "params": {"threshold": threshold, "mode": "reversion", "sticky": False},
                    "day_pnl": simulate_signal(product_frame, f"z_{window}", threshold, "reversion", False),
                }
            )
    for signal in ["micro_delta", "imbalance_1", "imbalance_3"]:
        for threshold in ([0.5, 1.0, 2.0, 3.0, 5.0] if signal == "micro_delta" else [0.2, 0.4, 0.6, 0.8]):
            for mode in ["reversion", "momentum"]:
                signal_candidates.append(
                    {
                        "strategy": signal,
                        "params": {"threshold": threshold, "mode": mode, "sticky": False},
                        "day_pnl": simulate_signal(product_frame, signal, threshold, mode, False),
                    }
                )
    return {"maker": maker_candidates, "signals": signal_candidates}


def simulate_group_residual(group_frame: pd.DataFrame, products: List[str], window: int, threshold: float) -> Dict[int, float]:
    output: Dict[int, float] = {}
    pivot = group_frame.pivot_table(index=["day", "timestamp"], columns="product", values="mid_price")
    rolling = pivot.groupby(level=0).transform(lambda series: series.rolling(window, min_periods=5).mean())
    residuals = pivot - rolling
    cross_residuals = residuals.sub(residuals.mean(axis=1), axis=0)

    for day in sorted(group_frame["day"].unique()):
        day_total = 0.0
        day_residuals = cross_residuals.loc[day]
        for product in products:
            product_frame = group_frame[
                (group_frame["day"] == day) & (group_frame["product"] == product)
            ].sort_values("timestamp")
            if product_frame.empty:
                continue
            signal_series = day_residuals[product].reindex(product_frame["timestamp"]).to_numpy(float)
            mids = product_frame["mid_price"].to_numpy(float)
            bid_prices = [product_frame[f"bid_price_{level}"].to_numpy(float) for level in [1, 2, 3]]
            bid_volumes = [product_frame[f"bid_volume_{level}"].to_numpy(float) for level in [1, 2, 3]]
            ask_prices = [product_frame[f"ask_price_{level}"].to_numpy(float) for level in [1, 2, 3]]
            ask_volumes = [product_frame[f"ask_volume_{level}"].to_numpy(float) for level in [1, 2, 3]]
            cash = 0.0
            position = 0
            for idx, signal in enumerate(signal_series):
                target = 0
                if not np.isnan(signal):
                    if signal > threshold:
                        target = -POSITION_LIMIT
                    elif signal < -threshold:
                        target = POSITION_LIMIT
                position, cash = cross_arrays(
                    idx,
                    bid_prices,
                    bid_volumes,
                    ask_prices,
                    ask_volumes,
                    position,
                    cash,
                    target,
                )
            day_total += cash + position * mids[-1]
        output[int(day)] = float(day_total)
    return output


def analyze_groups(frame: pd.DataFrame, days: List[int]) -> Dict:
    results = {}
    for group_name, products in PRODUCT_GROUPS.items():
        group_frame = frame[frame["product"].isin(products)].copy()
        candidates = []
        for window in [20, 50, 100, 200]:
            for threshold in [10.0, 20.0, 50.0, 100.0]:
                candidates.append(
                    {
                        "strategy": "group_residual_reversion",
                        "params": {"window": window, "threshold": threshold},
                        "day_pnl": simulate_group_residual(group_frame, products, window, threshold),
                    }
                )
        results[group_name] = loo_summary(candidates, days)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Round 5 per-product alpha isolation.")
    parser.add_argument("--max-timestamp", type=int, default=99_900)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    warnings.filterwarnings("ignore")
    frame = load_prices(args.max_timestamp)
    trades = load_trades()
    days = sorted(int(day) for day in frame["day"].unique())

    product_results = {}
    for product in Trader.PRODUCTS:
        product_frame = frame[frame["product"] == product].copy()
        candidates = product_candidates(product_frame, trades)
        maker = loo_summary(candidates["maker"], days)
        signals = loo_summary(candidates["signals"], days)
        ml = simulate_ml_product(product_frame, days)
        product_results[product] = {
            "maker": maker,
            "signals": signals,
            "ml_ridge_taker": ml,
            "best_family": max(
                [
                    ("maker", maker.get("loo_total", float("-inf"))),
                    ("signals", signals.get("loo_total", float("-inf"))),
                    ("ml_ridge_taker", ml.get("loo_total", float("-inf")) if ml else float("-inf")),
                ],
                key=lambda item: item[1],
            )[0],
        }

    group_results = analyze_groups(frame, days)
    robust_products = []
    for product, result in product_results.items():
        for family in ["maker", "signals", "ml_ridge_taker"]:
            summary = result.get(family)
            if not summary:
                continue
            if summary.get("loo_total", 0.0) > 0 and summary.get("loo_min", -1.0) >= 0:
                robust_products.append(
                    {
                        "product": product,
                        "family": family,
                        "loo_total": summary["loo_total"],
                        "loo_min": summary["loo_min"],
                        "full_best": summary.get("full_best"),
                    }
                )
    robust_products.sort(key=lambda item: item["loo_total"], reverse=True)

    report = {
        "config": {
            "max_timestamp": args.max_timestamp,
            "days": days,
            "validation": "leave-one-day-out; candidate parameters are selected on two training days and scored on the held-out day",
            "position_limit": POSITION_LIMIT,
        },
        "product_results": product_results,
        "group_results": group_results,
        "robust_products": robust_products,
        "notes": [
            "Maker results use the same public-repo-style passive-fill assumption as round5_diagnostics.",
            "Directional, residual, and ML taker tests pay the displayed spread by crossing the book.",
            "A signal is considered robust only if the leave-one-day total is positive and every held-out fold is non-negative.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))

    print(f"Wrote product alpha report to {args.output}")
    print("Top robust isolated products:")
    for item in robust_products[:20]:
        print(
            f"  {item['product']}: {item['family']} "
            f"loo={item['loo_total']:.1f} min={item['loo_min']:.1f}"
        )
    print("Top group residual tests:")
    for group_name, summary in sorted(
        group_results.items(),
        key=lambda item: item[1].get("loo_total", float("-inf")),
        reverse=True,
    )[:10]:
        print(
            f"  {group_name}: loo={summary.get('loo_total', 0.0):.1f} "
            f"min={summary.get('loo_min', 0.0):.1f} "
            f"best={summary.get('full_best', {}).get('params')}"
        )


if __name__ == "__main__":
    main()
