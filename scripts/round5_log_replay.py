import argparse
import csv
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datamodel import Listing, Observation, Trade, TradingState
from scripts.round5_diagnostics import apply_fills, build_depths
from trader import Trader


DEFAULT_ZIP = Path.home() / "Downloads" / "547014.zip"
OUTPUT_PATH = ROOT / "logs" / "round5_log_replay.json"


def load_submission_log(zip_path: Path) -> Tuple[Dict[int, List[Dict]], Dict[int, Dict[str, List[Trade]]], float]:
    with zipfile.ZipFile(zip_path) as archive:
        log_name = next(name for name in archive.namelist() if name.endswith(".log"))
        payload = json.loads(archive.read(log_name))
        reported_profit = 0.0
        json_names = [name for name in archive.namelist() if name.endswith(".json")]
        if json_names:
            summary = json.loads(archive.read(json_names[0]))
            reported_profit = float(summary.get("profit", 0.0))

    rows_by_timestamp: Dict[int, List[Dict]] = {}
    for row in csv.DictReader(io.StringIO(payload["activitiesLog"]), delimiter=";"):
        parsed = {
            "day": int(row["day"]),
            "timestamp": int(row["timestamp"]),
            "product": row["product"],
            "mid_price": float(row["mid_price"]),
        }
        for level in (1, 2, 3):
            for side in ("bid", "ask"):
                price = row[f"{side}_price_{level}"]
                volume = row[f"{side}_volume_{level}"]
                parsed[f"{side}_price_{level}"] = float(price) if price else None
                parsed[f"{side}_volume_{level}"] = int(float(volume)) if volume else None
        rows_by_timestamp.setdefault(parsed["timestamp"], []).append(parsed)

    trades_by_timestamp: Dict[int, Dict[str, List[Trade]]] = {}
    for row in payload.get("tradeHistory", []):
        if row.get("buyer") == "SUBMISSION" or row.get("seller") == "SUBMISSION":
            continue
        trade = Trade(
            row["symbol"],
            int(float(row["price"])),
            int(float(row["quantity"])),
            row.get("buyer") or None,
            row.get("seller") or None,
            int(row["timestamp"]),
        )
        trades_by_timestamp.setdefault(trade.timestamp, {}).setdefault(trade.symbol, []).append(trade)
    return rows_by_timestamp, trades_by_timestamp, reported_profit


def replay(zip_path: Path) -> Dict:
    rows_by_timestamp, trades_by_timestamp, reported_profit = load_submission_log(zip_path)
    trader = Trader()
    trader_data = ""
    products = Trader.PRODUCTS
    cash = {product: 0.0 for product in products}
    position = {product: 0 for product in products}
    last_mid: Dict[str, float] = {}
    fill_totals = {"cross_fills": 0, "passive_fills": 0, "filled_quantity": 0}
    listings = {product: Listing(product, product, "XIRECS") for product in products}

    for timestamp in sorted(rows_by_timestamp):
        depths, mids = build_depths(rows_by_timestamp[timestamp])
        last_mid.update(mids)
        timestamp_trades = trades_by_timestamp.get(timestamp, {})
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
        orders = [order for product_orders in result.values() for order in product_orders]
        stats = apply_fills(orders, depths, timestamp_trades, mids, cash, position)
        for key, value in stats.items():
            fill_totals[key] += value

    pnl_by_product = {
        product: cash[product] + position[product] * last_mid.get(product, 0.0)
        for product in products
    }
    return {
        "zip_path": str(zip_path),
        "reported_profit_for_original_submission": reported_profit,
        "replay_estimated_pnl": sum(pnl_by_product.values()),
        "pnl_by_product": pnl_by_product,
        "fills": fill_totals,
        "note": (
            "Replay excludes original SUBMISSION fills and uses anonymous market trades "
            "for passive-fill simulation; it is a sanity screen, not an exact exchange clone."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay current trader.py on a Round 5 submission log zip.")
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    report = replay(args.zip)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))

    print(f"Wrote log replay to {args.output}")
    print(f"Original reported profit: {report['reported_profit_for_original_submission']:.1f}")
    print(f"Current trader replay estimate: {report['replay_estimated_pnl']:.1f}")
    for product, pnl in sorted(report["pnl_by_product"].items(), key=lambda item: item[1], reverse=True)[:12]:
        if pnl:
            print(f"  {product}: {pnl:.1f}")


if __name__ == "__main__":
    main()
