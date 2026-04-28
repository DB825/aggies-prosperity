import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datamodel import Listing, Observation, Order, OrderDepth, Trade, TradingState
from trader import Trader


DATA_DIR = ROOT / "data" / "round5"
OUTPUT_PATH = ROOT / "logs" / "round5_diagnostics.json"


def load_price_rows() -> Dict[int, Dict[int, List[Dict]]]:
    by_day_ts: Dict[int, Dict[int, List[Dict]]] = {}
    for path in sorted(DATA_DIR.glob("prices_round_5_day_*.csv")):
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
                        price = row[f"{side}_price_{level}"]
                        volume = row[f"{side}_volume_{level}"]
                        parsed[f"{side}_price_{level}"] = float(price) if price else None
                        parsed[f"{side}_volume_{level}"] = int(float(volume)) if volume else None
                by_day_ts.setdefault(parsed["day"], {}).setdefault(parsed["timestamp"], []).append(parsed)
    return by_day_ts


def load_trades() -> Dict[int, Dict[int, Dict[str, List[Trade]]]]:
    by_day_ts: Dict[int, Dict[int, Dict[str, List[Trade]]]] = {}
    for path in sorted(DATA_DIR.glob("trades_round_5_day_*.csv")):
        day = int(path.stem.split("_day_")[-1])
        with path.open(newline="") as file:
            for row in csv.DictReader(file, delimiter=";"):
                trade = Trade(
                    row["symbol"],
                    int(float(row["price"])),
                    int(float(row["quantity"])),
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


def apply_fills(
    orders: Iterable[Order],
    depths: Dict[str, OrderDepth],
    market_trades: Dict[str, List[Trade]],
    mids: Dict[str, float],
    cash: Dict[str, float],
    position: Dict[str, int],
) -> Dict[str, int]:
    remaining: List[Order] = []
    stats = {"cross_fills": 0, "passive_fills": 0, "filled_quantity": 0}

    for order in orders:
        depth = depths[order.symbol]
        quantity_left = order.quantity
        if quantity_left > 0:
            for price in sorted(list(depth.sell_orders)):
                if price > order.price or quantity_left <= 0:
                    break
                fill = min(quantity_left, -depth.sell_orders[price])
                if fill > 0:
                    cash[order.symbol] -= price * fill
                    position[order.symbol] += fill
                    quantity_left -= fill
                    depth.sell_orders[price] += fill
                    stats["cross_fills"] += 1
                    stats["filled_quantity"] += fill
        elif quantity_left < 0:
            sell_left = -quantity_left
            for price in sorted(list(depth.buy_orders), reverse=True):
                if price < order.price or sell_left <= 0:
                    break
                fill = min(sell_left, depth.buy_orders[price])
                if fill > 0:
                    cash[order.symbol] += price * fill
                    position[order.symbol] -= fill
                    sell_left -= fill
                    depth.buy_orders[price] -= fill
                    stats["cross_fills"] += 1
                    stats["filled_quantity"] += fill
            quantity_left = -sell_left
        if quantity_left != 0:
            remaining.append(Order(order.symbol, order.price, quantity_left))

    by_symbol: Dict[str, List[Order]] = {}
    for order in remaining:
        by_symbol.setdefault(order.symbol, []).append(order)

    for symbol, trades in market_trades.items():
        if symbol not in by_symbol or symbol not in mids:
            continue
        symbol_orders = by_symbol[symbol]
        mid = mids[symbol]
        for trade in trades:
            quantity_left = trade.quantity
            if trade.price <= mid:
                bids = sorted(
                    [order for order in symbol_orders if order.quantity > 0 and order.price >= trade.price],
                    key=lambda order: order.price,
                    reverse=True,
                )
                for order in bids:
                    if quantity_left <= 0:
                        break
                    fill = min(quantity_left, order.quantity)
                    if fill > 0:
                        cash[symbol] -= order.price * fill
                        position[symbol] += fill
                        order.quantity -= fill
                        quantity_left -= fill
                        stats["passive_fills"] += 1
                        stats["filled_quantity"] += fill
            elif trade.price >= mid:
                asks = sorted(
                    [order for order in symbol_orders if order.quantity < 0 and order.price <= trade.price],
                    key=lambda order: order.price,
                )
                for order in asks:
                    if quantity_left <= 0:
                        break
                    fill = min(quantity_left, -order.quantity)
                    if fill > 0:
                        cash[symbol] += order.price * fill
                        position[symbol] -= fill
                        order.quantity += fill
                        quantity_left -= fill
                        stats["passive_fills"] += 1
                        stats["filled_quantity"] += fill
    return stats


def run_backtest(max_timestamp: int | None = 99_900) -> Dict:
    price_rows = load_price_rows()
    trades = load_trades()
    trader = Trader()
    products = Trader.PRODUCTS
    product_totals = {product: 0.0 for product in products}
    day_results = []
    fill_totals = {"cross_fills": 0, "passive_fills": 0, "filled_quantity": 0}

    for day in sorted(price_rows):
        trader_data = ""
        cash = {product: 0.0 for product in products}
        position = {product: 0 for product in products}
        max_abs_position = {product: 0 for product in products}
        last_mid: Dict[str, float] = {}
        listings = {product: Listing(product, product, "XIRECS") for product in products}

        timestamps = sorted(price_rows[day])
        if max_timestamp is not None:
            timestamps = [timestamp for timestamp in timestamps if timestamp <= max_timestamp]

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
            orders = [order for product_orders in result.values() for order in product_orders]
            stats = apply_fills(orders, depths, timestamp_trades, mids, cash, position)
            for key, value in stats.items():
                fill_totals[key] += value
            for product in products:
                max_abs_position[product] = max(max_abs_position[product], abs(position[product]))

        pnl_by_product = {}
        for product in products:
            pnl = cash[product] + position[product] * last_mid.get(product, 0.0)
            pnl_by_product[product] = pnl
            product_totals[product] += pnl

        day_results.append(
            {
                "day": day,
                "pnl_by_product": pnl_by_product,
                "position": dict(position),
                "max_abs_position": max_abs_position,
                "total_pnl": sum(pnl_by_product.values()),
            }
        )

    return {
        "config": {
            "fill_model": "book-crossing plus public-repo-style passive fills from bot trades",
            "products": products,
            "position_limit": Trader.POSITION_LIMIT,
            "max_timestamp": max_timestamp,
            "maker_product_count": len(Trader.MAKE_PARAMS),
            "jump_reversion_products": Trader.JUMP_REVERSION,
            "groups": Trader.GROUPS,
        },
        "day_results": day_results,
        "per_product_totals": product_totals,
        "combined_pnl": sum(day["total_pnl"] for day in day_results),
        "fills": fill_totals,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Round 5 deterministic replay diagnostics.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument(
        "--max-timestamp",
        type=int,
        default=99_900,
        help="Last timestamp to replay. Defaults to the official 1,000-tick log window.",
    )
    parser.add_argument("--full-day", action="store_true", help="Replay every timestamp in the historical files.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    diagnostics = run_backtest(None if args.full_day else args.max_timestamp)
    args.output.write_text(json.dumps(diagnostics, indent=2))

    if not args.quiet:
        print(f"Wrote diagnostics to {args.output}")
        print(f"Combined PnL: {diagnostics['combined_pnl']:.1f}")
        for day in diagnostics["day_results"]:
            print(f"Day {day['day']}: total={day['total_pnl']:.1f}")
        print("Top products:")
        for product, pnl in sorted(
            diagnostics["per_product_totals"].items(),
            key=lambda item: item[1],
            reverse=True,
        )[:12]:
            print(f"  {product}: {pnl:.1f}")


if __name__ == "__main__":
    main()
