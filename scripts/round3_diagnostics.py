import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datamodel import Listing, Observation, Order, OrderDepth, TradingState
from trader import Trader


DATA_DIR = ROOT / "data" / "round3"
OUTPUT_PATH = ROOT / "logs" / "round3_diagnostics.json"
TTE_BY_DAY = {0: 8.0, 1: 7.0, 2: 6.0}
PRODUCT_ORDER = [
    "HYDROGEL_PACK",
    "VELVETFRUIT_EXTRACT",
    "VEV_4000",
    "VEV_4500",
    "VEV_5000",
    "VEV_5100",
    "VEV_5200",
    "VEV_5300",
    "VEV_5400",
    "VEV_5500",
    "VEV_6000",
    "VEV_6500",
]


def load_price_rows() -> Dict[int, Dict[int, List[Dict]]]:
    by_day_ts: Dict[int, Dict[int, List[Dict]]] = {}
    for path in sorted(DATA_DIR.glob("prices_round_3_day_*.csv")):
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


def apply_fills(
    orders: Iterable[Order],
    depths: Dict[str, OrderDepth],
    cash: Dict[str, float],
    position: Dict[str, int],
) -> Tuple[Dict[str, float], Dict[str, int], Dict[str, int]]:
    stats = {"filled_orders": 0, "filled_quantity": 0}

    for order in orders:
        depth = depths[order.symbol]
        if order.quantity > 0:
            quantity_left = order.quantity
            for price in sorted(list(depth.sell_orders)):
                if price > order.price or quantity_left <= 0:
                    break
                fill = min(quantity_left, -depth.sell_orders[price])
                if fill > 0:
                    cash[order.symbol] -= price * fill
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
                    cash[order.symbol] += price * fill
                    position[order.symbol] -= fill
                    quantity_left -= fill
                    depth.buy_orders[price] -= fill
                    stats["filled_orders"] += 1
                    stats["filled_quantity"] += fill

    return cash, position, stats


def net_option_delta(trader: Trader, position: Dict[str, int], spot: float, tte: float) -> float:
    option_positions = {symbol: position.get(symbol, 0) for symbol in trader.OPTION_STRIKES}
    return trader.portfolio_delta(option_positions, spot, tte)


def run_backtest(by_day_ts: Dict[int, Dict[int, List[Dict]]]) -> Dict:
    trader = Trader()
    day_results = []
    product_totals = {product: 0.0 for product in PRODUCT_ORDER}
    total_filled_orders = 0
    total_filled_quantity = 0

    for day in sorted(by_day_ts):
        trader_data = ""
        cash = {product: 0.0 for product in PRODUCT_ORDER}
        position = {product: 0 for product in PRODUCT_ORDER}
        max_abs_position = {product: 0 for product in PRODUCT_ORDER}
        last_mid: Dict[str, float] = {}
        listings = {product: Listing(product, product, "XIRECS") for product in PRODUCT_ORDER}
        last_option_delta = 0.0

        for timestamp in sorted(by_day_ts[day]):
            depths, mids = build_depths(by_day_ts[day][timestamp])
            last_mid.update({product: mid for product, mid in mids.items() if mid > 0})

            state = TradingState(
                trader_data,
                timestamp,
                listings,
                depths,
                {product: [] for product in PRODUCT_ORDER},
                {product: [] for product in PRODUCT_ORDER},
                dict(position),
                Observation({"VELVETFRUIT_TTE": TTE_BY_DAY[day]}, {}),
            )

            result, _, trader_data = trader.run(state)
            orders = [order for product_orders in result.values() for order in product_orders]
            cash, position, fill_stats = apply_fills(orders, depths, cash, position)
            total_filled_orders += fill_stats["filled_orders"]
            total_filled_quantity += fill_stats["filled_quantity"]

            for product in PRODUCT_ORDER:
                max_abs_position[product] = max(max_abs_position[product], abs(position[product]))

            if "VELVETFRUIT_EXTRACT" in last_mid:
                last_option_delta = net_option_delta(
                    trader,
                    position,
                    last_mid["VELVETFRUIT_EXTRACT"],
                    TTE_BY_DAY[day],
                )

        pnl_by_product = {}
        for product in PRODUCT_ORDER:
            pnl_by_product[product] = cash[product] + position[product] * last_mid[product]
            product_totals[product] += pnl_by_product[product]

        day_results.append(
            {
                "day": day,
                "tte_days": TTE_BY_DAY[day],
                "pnl_by_product": pnl_by_product,
                "position": dict(position),
                "max_abs_position": max_abs_position,
                "ending_option_delta": last_option_delta,
                "total_pnl": sum(pnl_by_product.values()),
            }
        )

    return {
        "config": {
            "fill_model": "crossing-only replay against top-three book levels",
            "tte_injected_by_day": TTE_BY_DAY,
            "products": PRODUCT_ORDER,
        },
        "day_results": day_results,
        "per_product_totals": product_totals,
        "combined_pnl": sum(day["total_pnl"] for day in day_results),
        "filled_orders": total_filled_orders,
        "filled_quantity": total_filled_quantity,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Round 3 deterministic replay diagnostics.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    by_day_ts = load_price_rows()
    diagnostics = run_backtest(by_day_ts)
    args.output.write_text(json.dumps(diagnostics, indent=2))

    if not args.quiet:
        print(f"Wrote diagnostics to {args.output}")
        print(f"Combined PnL: {diagnostics['combined_pnl']:.1f}")
        for day in diagnostics["day_results"]:
            print(
                f"Day {day['day']}: total={day['total_pnl']:.1f}, "
                f"ending_option_delta={day['ending_option_delta']:.1f}"
            )


if __name__ == "__main__":
    main()
