import argparse
import json
import math
import sys
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datamodel import Listing, Observation, TradingState
from scripts.round5_diagnostics import apply_fills, build_depths, load_price_rows, load_trades
from trader import Trader


DATA_DIR = ROOT / "data" / "round5"
OUTPUT_PATH = ROOT / "logs" / "round5_ml_research.json"
TAKER_THRESHOLDS = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 14.0, 20.0, 30.0]
PASSIVE_GATES = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 14.0, 20.0, 9999.0]


def load_feature_frame(max_timestamp: int, horizon: int) -> Tuple[pd.DataFrame, List[str]]:
    frame = pd.concat(
        [pd.read_csv(path, sep=";") for path in sorted(DATA_DIR.glob("prices_round_5_day_*.csv"))],
        ignore_index=True,
    )
    frame = frame[frame["timestamp"] <= max_timestamp].copy()
    frame = frame.sort_values(["product", "day", "timestamp"]).reset_index(drop=True)

    numeric_columns = [
        "bid_price_1",
        "ask_price_1",
        "bid_volume_1",
        "ask_volume_1",
        "bid_price_2",
        "ask_price_2",
        "bid_volume_2",
        "ask_volume_2",
        "bid_price_3",
        "ask_price_3",
        "bid_volume_3",
        "ask_volume_3",
        "mid_price",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

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
    frame["time_fraction"] = frame["timestamp"] / max_timestamp

    for lag in [1, 2, 3, 5, 10, 20, 50, 100]:
        frame[f"return_{lag}"] = frame.groupby(["day", "product"])["mid_price"].diff(lag)
    for window in [5, 10, 20, 50, 100]:
        rolling_mean = frame.groupby(["day", "product"])["mid_price"].transform(
            lambda series: series.rolling(window, min_periods=2).mean()
        )
        frame[f"z_{window}"] = frame["mid_price"] - rolling_mean

    frame["target"] = frame.groupby(["day", "product"])["mid_price"].shift(-horizon) - frame["mid_price"]
    product_ids = {product: idx for idx, product in enumerate(sorted(frame["product"].unique()))}
    frame["product_id"] = frame["product"].map(product_ids).astype("category")

    feature_columns = (
        ["spread", "micro_delta", "imbalance_1", "imbalance_3", "time_fraction"]
        + [f"return_{lag}" for lag in [1, 2, 3, 5, 10, 20, 50, 100]]
        + [f"z_{window}" for window in [5, 10, 20, 50, 100]]
    )
    frame = frame.dropna(
        subset=feature_columns + ["target", "bid_price_1", "ask_price_1", "mid_price", "product_id"]
    ).copy()
    return frame, feature_columns


def crossed_taker_pnl(frame: pd.DataFrame, predictions: np.ndarray, threshold: float) -> float:
    cash: Dict[Tuple[int, str], float] = defaultdict(float)
    position: Dict[Tuple[int, str], int] = defaultdict(int)
    last_mid: Dict[Tuple[int, str], float] = {}

    local = frame[["day", "timestamp", "product", "bid_price_1", "ask_price_1", "mid_price"]].copy()
    local["prediction"] = predictions
    local = local.sort_values(["day", "timestamp", "product"])

    for row in local.itertuples(index=False):
        key = (int(row.day), row.product)
        current_position = position[key]
        target = 0
        if row.prediction > threshold:
            target = Trader.POSITION_LIMIT
        elif row.prediction < -threshold:
            target = -Trader.POSITION_LIMIT

        delta = target - current_position
        if delta > 0:
            cash[key] -= float(row.ask_price_1) * delta
            current_position += delta
        elif delta < 0:
            quantity = -delta
            cash[key] += float(row.bid_price_1) * quantity
            current_position -= quantity

        position[key] = current_position
        last_mid[key] = float(row.mid_price)

    return sum(cash[key] + position[key] * last_mid[key] for key in position)


def prediction_map(frame: pd.DataFrame, predictions: Iterable[float]) -> Dict[Tuple[int, int, str], float]:
    return {
        (int(day), int(timestamp), product): float(prediction)
        for day, timestamp, product, prediction in zip(
            frame["day"], frame["timestamp"], frame["product"], predictions
        )
    }


def simulate_filtered_trader(
    day: int,
    max_timestamp: int,
    price_rows: Dict[int, Dict[int, List[Dict]]],
    trades: Dict[int, Dict[int, Dict[str, List]]],
    predictions: Optional[Dict[Tuple[int, int, str], float]] = None,
    gate: float = 9999.0,
) -> float:
    trader = Trader()
    trader_data = ""
    products = Trader.PRODUCTS
    cash = {product: 0.0 for product in products}
    position = {product: 0 for product in products}
    last_mid: Dict[str, float] = {}
    listings = {product: Listing(product, product, "XIRECS") for product in products}

    timestamps = [timestamp for timestamp in sorted(price_rows[day]) if timestamp <= max_timestamp]
    for timestamp in timestamps:
        depths, mids = build_depths(price_rows[day][timestamp])
        last_mid.update(mids)
        timestamp_trades = trades.get(day, {}).get(timestamp, {})
        state = TradingState(
            trader_data,
            timestamp,
            listings,
            depths,
            {product: [] for product in products},
            timestamp_trades,
            dict(position),
            Observation({}, {}),
        )

        result, _, trader_data = trader.run(state)
        filtered_orders = []
        for product, orders in result.items():
            best_bid = max(depths[product].buy_orders) if depths[product].buy_orders else None
            best_ask = min(depths[product].sell_orders) if depths[product].sell_orders else None
            forecast = 0.0 if predictions is None else predictions.get((day, timestamp, product), 0.0)
            for order in orders:
                passive_buy = order.quantity > 0 and best_ask is not None and order.price < best_ask
                passive_sell = order.quantity < 0 and best_bid is not None and order.price > best_bid
                if passive_buy and forecast < -gate:
                    continue
                if passive_sell and forecast > gate:
                    continue
                filtered_orders.append(order)

        apply_fills(filtered_orders, depths, timestamp_trades, mids, cash, position)

    return sum(cash[product] + position[product] * last_mid.get(product, 0.0) for product in products)


def make_models(feature_columns: List[str]) -> Dict[str, Tuple[Callable[[], object], List[str], Optional[str]]]:
    models: Dict[str, Tuple[Callable[[], object], List[str], Optional[str]]] = {
        "ridge": (
            lambda: make_pipeline(
                ColumnTransformer(
                    [
                        ("numeric", StandardScaler(), feature_columns),
                        ("product", OneHotEncoder(handle_unknown="ignore"), ["product"]),
                    ]
                ),
                Ridge(alpha=100.0),
            ),
            feature_columns + ["product"],
            None,
        ),
        "hist_gradient_boosting": (
            lambda: HistGradientBoostingRegressor(
                max_iter=120,
                max_leaf_nodes=15,
                learning_rate=0.04,
                l2_regularization=1.0,
                random_state=7,
            ),
            feature_columns,
            "sample",
        ),
        "mlp": (
            lambda: make_pipeline(
                StandardScaler(),
                MLPRegressor(
                    hidden_layer_sizes=(48, 24),
                    activation="relu",
                    alpha=0.01,
                    learning_rate_init=0.001,
                    max_iter=80,
                    early_stopping=True,
                    n_iter_no_change=8,
                    random_state=7,
                ),
            ),
            feature_columns,
            "sample",
        ),
    }

    try:
        from lightgbm import LGBMRegressor

        models["lightgbm"] = (
            lambda: LGBMRegressor(
                n_estimators=260,
                learning_rate=0.025,
                num_leaves=15,
                min_child_samples=80,
                subsample=0.8,
                colsample_bytree=0.9,
                reg_lambda=5.0,
                random_state=7,
                verbosity=-1,
            ),
            ["product_id"] + feature_columns,
            "categorical",
        )
    except Exception:
        pass
    return models


def evaluate_tabular_models(
    frame: pd.DataFrame,
    feature_columns: List[str],
    max_timestamp: int,
    train_sample: int,
) -> Tuple[Dict[str, Dict], Dict[int, float]]:
    price_rows = load_price_rows()
    trades = load_trades()
    days = sorted(int(day) for day in frame["day"].unique())
    baseline_by_day = {
        day: simulate_filtered_trader(day, max_timestamp, price_rows, trades)
        for day in days
    }
    models = make_models(feature_columns)
    results: Dict[str, Dict] = {}

    for name, (factory, model_features, fit_mode) in models.items():
        folds = []
        for test_day in days:
            train = frame[frame["day"] != test_day]
            test = frame[frame["day"] == test_day]

            if fit_mode == "sample" and len(train) > train_sample:
                fit_frame = train.sample(train_sample, random_state=42 + test_day)
            else:
                fit_frame = train

            model = factory()
            fit_kwargs = {}
            if fit_mode == "categorical":
                fit_kwargs["categorical_feature"] = ["product_id"]
            model.fit(fit_frame[model_features], fit_frame["target"], **fit_kwargs)

            train_predictions = model.predict(train[model_features])
            test_predictions = model.predict(test[model_features])

            train_taker = [
                (crossed_taker_pnl(train, train_predictions, threshold), threshold)
                for threshold in TAKER_THRESHOLDS
            ]
            best_train_taker_pnl, taker_threshold = max(train_taker, key=lambda item: item[0])
            test_taker_pnl = crossed_taker_pnl(test, test_predictions, taker_threshold)

            train_map = prediction_map(train, train_predictions)
            train_passive = [
                (
                    sum(
                        simulate_filtered_trader(day, max_timestamp, price_rows, trades, train_map, gate)
                        for day in days
                        if day != test_day
                    ),
                    gate,
                )
                for gate in PASSIVE_GATES
            ]
            best_train_passive_pnl, passive_gate = max(train_passive, key=lambda item: item[0])
            test_map = prediction_map(test, test_predictions)
            test_passive_pnl = simulate_filtered_trader(
                test_day,
                max_timestamp,
                price_rows,
                trades,
                test_map,
                passive_gate,
            )

            corr = float(np.corrcoef(test_predictions, test["target"])[0, 1])
            if math.isnan(corr):
                corr = 0.0
            folds.append(
                {
                    "test_day": int(test_day),
                    "target_corr": corr,
                    "target_mse": float(mean_squared_error(test["target"], test_predictions)),
                    "taker_threshold": taker_threshold,
                    "train_taker_pnl": float(best_train_taker_pnl),
                    "test_taker_pnl": float(test_taker_pnl),
                    "passive_gate": passive_gate,
                    "train_passive_pnl": float(best_train_passive_pnl),
                    "test_passive_pnl": float(test_passive_pnl),
                    "baseline_pnl": float(baseline_by_day[test_day]),
                    "passive_delta": float(test_passive_pnl - baseline_by_day[test_day]),
                }
            )

        results[name] = {
            "folds": folds,
            "taker_sum": float(sum(fold["test_taker_pnl"] for fold in folds)),
            "passive_sum": float(sum(fold["test_passive_pnl"] for fold in folds)),
            "baseline_sum": float(sum(fold["baseline_pnl"] for fold in folds)),
            "passive_delta": float(sum(fold["passive_delta"] for fold in folds)),
            "mean_target_corr": float(np.mean([fold["target_corr"] for fold in folds])),
        }
    return results, baseline_by_day


def evaluate_transformer(
    frame: pd.DataFrame,
    feature_columns: List[str],
    max_timestamp: int,
    train_sample: int,
) -> Dict:
    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset
    except Exception as exc:
        return {"skipped": True, "reason": f"torch unavailable: {exc}"}

    torch.manual_seed(7)
    torch.set_num_threads(2)
    sequence_length = 16
    transformer_features = [
        "spread",
        "micro_delta",
        "imbalance_1",
        "imbalance_3",
        "return_1",
        "return_3",
        "return_10",
        "z_20",
    ]
    products = sorted(frame["product"].unique())
    product_ids = {product: idx for idx, product in enumerate(products)}

    sequences = []
    targets = []
    metadata = []
    for (day, product), group in frame.sort_values(["day", "product", "timestamp"]).groupby(
        ["day", "product"],
        sort=False,
    ):
        values = group[transformer_features].to_numpy(np.float32)
        target_values = group["target"].to_numpy(np.float32)
        timestamps = group["timestamp"].to_numpy(np.int64)
        product_id = product_ids[product]
        for idx in range(sequence_length - 1, len(group)):
            sequences.append(values[idx - sequence_length + 1 : idx + 1])
            targets.append(target_values[idx])
            metadata.append((int(day), int(timestamps[idx]), product, product_id))

    if not sequences:
        return {"skipped": True, "reason": "no sequence rows"}

    x = np.asarray(sequences, dtype=np.float32)
    y = np.asarray(targets, dtype=np.float32)
    meta = np.array(metadata, dtype=object)

    class TinyTransformer(nn.Module):
        def __init__(self, product_count: int, feature_count: int, seq_len: int) -> None:
            super().__init__()
            d_model = 32
            self.input_projection = nn.Linear(feature_count, d_model)
            self.position = nn.Parameter(torch.zeros(1, seq_len, d_model))
            self.product_embedding = nn.Embedding(product_count, d_model)
            layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=4,
                dim_feedforward=64,
                dropout=0.05,
                batch_first=True,
                activation="gelu",
            )
            self.encoder = nn.TransformerEncoder(layer, num_layers=1)
            self.head = nn.Sequential(nn.LayerNorm(d_model), nn.Linear(d_model, 1))

        def forward(self, batch: torch.Tensor, product_batch: torch.Tensor) -> torch.Tensor:
            encoded = (
                self.input_projection(batch)
                + self.position
                + self.product_embedding(product_batch).unsqueeze(1)
            )
            encoded = self.encoder(encoded)
            return self.head(encoded[:, -1]).squeeze(-1)

    price_rows = load_price_rows()
    trades = load_trades()
    days = sorted(int(day) for day in frame["day"].unique())
    baseline_by_day = {
        day: simulate_filtered_trader(day, max_timestamp, price_rows, trades)
        for day in days
    }
    folds = []

    for test_day in days:
        day_column = meta[:, 0].astype(int)
        train_index = np.where(day_column != test_day)[0]
        test_index = np.where(day_column == test_day)[0]
        feature_mean = x[train_index].reshape(-1, x.shape[-1]).mean(axis=0)
        feature_std = x[train_index].reshape(-1, x.shape[-1]).std(axis=0) + 1e-6
        target_mean = float(y[train_index].mean())
        target_std = float(y[train_index].std() + 1e-6)
        scaled_x = (x - feature_mean) / feature_std

        rng = np.random.default_rng(100 + test_day)
        fit_index = train_index.copy()
        if len(fit_index) > train_sample:
            fit_index = rng.choice(fit_index, train_sample, replace=False)

        dataset = TensorDataset(
            torch.tensor(scaled_x[fit_index]),
            torch.tensor(meta[fit_index, 3].astype(int), dtype=torch.long),
            torch.tensor((y[fit_index] - target_mean) / target_std, dtype=torch.float32),
        )
        loader = DataLoader(dataset, batch_size=1024, shuffle=True)
        model = TinyTransformer(len(products), x.shape[-1], sequence_length)
        optimizer = torch.optim.AdamW(model.parameters(), lr=0.002, weight_decay=0.01)
        loss_function = nn.MSELoss()

        model.train()
        epoch_losses = []
        for _ in range(5):
            losses = []
            for batch_x, batch_product, batch_y in loader:
                optimizer.zero_grad()
                loss = loss_function(model(batch_x, batch_product), batch_y)
                loss.backward()
                optimizer.step()
                losses.append(float(loss))
            epoch_losses.append(float(np.mean(losses)))

        def predict(index: np.ndarray) -> np.ndarray:
            output = []
            model.eval()
            with torch.no_grad():
                for start in range(0, len(index), 4096):
                    batch_index = index[start : start + 4096]
                    batch_prediction = model(
                        torch.tensor(scaled_x[batch_index]),
                        torch.tensor(meta[batch_index, 3].astype(int), dtype=torch.long),
                    )
                    output.append(batch_prediction.numpy() * target_std + target_mean)
            return np.concatenate(output)

        train_predictions = predict(train_index)
        test_predictions = predict(test_index)
        train_meta = meta[train_index]
        train_map = {
            (int(day), int(timestamp), product): float(prediction)
            for (day, timestamp, product, _), prediction in zip(train_meta, train_predictions)
        }
        train_passive = [
            (
                sum(
                    simulate_filtered_trader(day, max_timestamp, price_rows, trades, train_map, gate)
                    for day in days
                    if day != test_day
                ),
                gate,
            )
            for gate in PASSIVE_GATES
        ]
        best_train_passive_pnl, passive_gate = max(train_passive, key=lambda item: item[0])

        test_meta = meta[test_index]
        test_map = {
            (int(day), int(timestamp), product): float(prediction)
            for (day, timestamp, product, _), prediction in zip(test_meta, test_predictions)
        }
        test_passive_pnl = simulate_filtered_trader(
            test_day,
            max_timestamp,
            price_rows,
            trades,
            test_map,
            passive_gate,
        )
        corr = float(np.corrcoef(test_predictions, y[test_index])[0, 1])
        if math.isnan(corr):
            corr = 0.0
        folds.append(
            {
                "test_day": int(test_day),
                "target_corr": corr,
                "epoch_losses": epoch_losses,
                "passive_gate": passive_gate,
                "train_passive_pnl": float(best_train_passive_pnl),
                "test_passive_pnl": float(test_passive_pnl),
                "baseline_pnl": float(baseline_by_day[test_day]),
                "passive_delta": float(test_passive_pnl - baseline_by_day[test_day]),
            }
        )

    return {
        "folds": folds,
        "passive_sum": float(sum(fold["test_passive_pnl"] for fold in folds)),
        "baseline_sum": float(sum(fold["baseline_pnl"] for fold in folds)),
        "passive_delta": float(sum(fold["passive_delta"] for fold in folds)),
        "mean_target_corr": float(np.mean([fold["target_corr"] for fold in folds])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Round 5 ML/NN/transformer research harness.")
    parser.add_argument("--max-timestamp", type=int, default=99_900)
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--train-sample", type=int, default=60_000)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--skip-transformer", action="store_true")
    args = parser.parse_args()

    warnings.filterwarnings("ignore")
    frame, feature_columns = load_feature_frame(args.max_timestamp, args.horizon)
    tabular_results, baseline_by_day = evaluate_tabular_models(
        frame,
        feature_columns,
        args.max_timestamp,
        args.train_sample,
    )
    transformer_result = {"skipped": True, "reason": "disabled by --skip-transformer"}
    if not args.skip_transformer:
        transformer_result = evaluate_transformer(
            frame,
            feature_columns,
            args.max_timestamp,
            args.train_sample,
        )

    report = {
        "config": {
            "max_timestamp": args.max_timestamp,
            "horizon": args.horizon,
            "train_sample": args.train_sample,
            "validation": "leave-one-day-out; thresholds and quote gates chosen on training days only",
            "baseline": "current trader.py without ML filters",
        },
        "baseline_by_day": baseline_by_day,
        "baseline_sum": float(sum(baseline_by_day.values())),
        "tabular_models": tabular_results,
        "transformer": transformer_result,
        "recommendation": (
            "Do not add ML to trader.py unless a model improves leave-one-day-out passive_sum "
            "versus baseline_sum. These models are useful as a research check, but the submitted "
            "exchange bot should remain the simpler robust strategy when validation is negative."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))

    print(f"Wrote ML research to {args.output}")
    print(f"Baseline: {report['baseline_sum']:.1f}")
    for name, result in tabular_results.items():
        print(
            f"{name}: passive={result['passive_sum']:.1f} "
            f"delta={result['passive_delta']:.1f} taker={result['taker_sum']:.1f} "
            f"corr={result['mean_target_corr']:.4f}"
        )
    if not transformer_result.get("skipped"):
        print(
            f"transformer: passive={transformer_result['passive_sum']:.1f} "
            f"delta={transformer_result['passive_delta']:.1f} "
            f"corr={transformer_result['mean_target_corr']:.4f}"
        )
    else:
        print(f"transformer skipped: {transformer_result.get('reason')}")


if __name__ == "__main__":
    main()
