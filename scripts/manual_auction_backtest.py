import argparse
import csv
import json
import random
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "logs" / "manual_auction_backtest.json"


@dataclass(frozen=True)
class Market:
    symbol: str
    fair_value: float
    round_trip_fee: float
    buy_orders: Dict[int, int]
    sell_orders: Dict[int, int]
    max_bid_qty: int
    min_price: int
    max_price: int


@dataclass(frozen=True)
class BidResult:
    symbol: str
    bid_price: int
    bid_qty: int
    clearing_price: int
    traded_volume: int
    allocation: int
    profit: float


MARKETS = {
    "DRYLAND_FLAX": Market(
        symbol="DRYLAND_FLAX",
        fair_value=30.0,
        round_trip_fee=0.0,
        buy_orders={30: 30_000, 29: 5_000, 28: 12_000, 27: 28_000},
        sell_orders={28: 40_000, 31: 20_000, 32: 20_000, 33: 30_000},
        max_bid_qty=30_000,
        min_price=24,
        max_price=34,
    ),
    "EMBER_MUSHROOM": Market(
        symbol="EMBER_MUSHROOM",
        fair_value=20.0,
        round_trip_fee=0.10,
        buy_orders={20: 43_000, 19: 17_000, 18: 6_000, 17: 5_000, 16: 10_000, 15: 5_000, 14: 10_000, 13: 7_000},
        sell_orders={12: 20_000, 13: 25_000, 14: 35_000, 15: 6_000, 16: 5_000, 17: 0, 18: 10_000, 19: 12_000},
        max_bid_qty=43_000,
        min_price=12,
        max_price=23,
    ),
}

RECOMMENDED_ORDERS = {
    "DRYLAND_FLAX": {"bid_price": 30, "bid_qty": 9_999},
    "EMBER_MUSHROOM": {"bid_price": 17, "bid_qty": 19_999},
}


def clearing_result(bids: Dict[int, int], asks: Dict[int, int], price_range: Iterable[int]) -> Tuple[int, int]:
    best_price = None
    best_volume = -1
    for price in price_range:
        demand = sum(qty for bid_price, qty in bids.items() if bid_price >= price)
        supply = sum(qty for ask_price, qty in asks.items() if ask_price <= price)
        volume = min(demand, supply)
        if volume > best_volume or (volume == best_volume and (best_price is None or price > best_price)):
            best_price = price
            best_volume = volume
    if best_price is None:
        raise ValueError("price range cannot be empty")
    return best_price, best_volume


def simulate_bid(market: Market, bid_price: int, bid_qty: int) -> BidResult:
    bids_with_us = dict(market.buy_orders)
    bids_with_us[bid_price] = bids_with_us.get(bid_price, 0) + bid_qty
    low = min(market.min_price, min(market.buy_orders), min(market.sell_orders), bid_price) - 1
    high = max(market.max_price, max(market.buy_orders), max(market.sell_orders), bid_price) + 1
    clearing_price, traded_volume = clearing_result(bids_with_us, market.sell_orders, range(low, high + 1))

    if bid_price < clearing_price or bid_qty <= 0:
        allocation = 0
    else:
        higher_priority = sum(qty for price, qty in market.buy_orders.items() if price > bid_price)
        same_price_ahead = market.buy_orders.get(bid_price, 0)
        allocation = min(bid_qty, max(0, traded_volume - higher_priority - same_price_ahead))

    profit = (market.fair_value - clearing_price - market.round_trip_fee) * allocation
    return BidResult(
        symbol=market.symbol,
        bid_price=bid_price,
        bid_qty=bid_qty,
        clearing_price=clearing_price,
        traded_volume=traded_volume,
        allocation=allocation,
        profit=round(profit, 10),
    )


def exact_scan(market: Market) -> List[BidResult]:
    return [
        simulate_bid(market, bid_price, bid_qty)
        for bid_price in range(market.min_price, market.max_price + 1)
        for bid_qty in range(0, market.max_bid_qty + 1)
    ]


def top_results(results: List[BidResult], limit: int = 10) -> List[BidResult]:
    return sorted(results, key=lambda result: (result.profit, -result.bid_price, result.bid_qty), reverse=True)[:limit]


def percentile(values: List[float], pct: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * pct
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - index) + ordered[high] * (index - low)


def perturb_orders(orders: Dict[int, int], pct: float, rng: random.Random, step: int = 1_000) -> Dict[int, int]:
    perturbed = {}
    for price, volume in orders.items():
        if volume == 0:
            perturbed[price] = 0
            continue
        changed = volume * (1 + rng.uniform(-pct, pct))
        perturbed[price] = max(0, int(round(changed / step)) * step)
    return perturbed


def stress_order(market: Market, bid_price: int, bid_qty: int, simulations: int, pct: float, seed: int) -> Dict:
    rng = random.Random(seed)
    profits = []
    clearing_prices = []
    allocations = []
    for _ in range(simulations):
        stressed = Market(
            symbol=market.symbol,
            fair_value=market.fair_value,
            round_trip_fee=market.round_trip_fee,
            buy_orders=perturb_orders(market.buy_orders, pct, rng),
            sell_orders=perturb_orders(market.sell_orders, pct, rng),
            max_bid_qty=market.max_bid_qty,
            min_price=market.min_price,
            max_price=market.max_price,
        )
        result = simulate_bid(stressed, bid_price, bid_qty)
        profits.append(result.profit)
        clearing_prices.append(result.clearing_price)
        allocations.append(result.allocation)

    return {
        "simulations": simulations,
        "volume_shock_pct": pct,
        "mean_profit": statistics.mean(profits),
        "p05_profit": percentile(profits, 0.05),
        "median_profit": percentile(profits, 0.50),
        "worst_profit": min(profits),
        "loss_probability": sum(profit < 0 for profit in profits) / len(profits),
        "zero_allocation_probability": sum(allocation == 0 for allocation in allocations) / len(allocations),
        "clearing_price_counts": {str(price): clearing_prices.count(price) for price in sorted(set(clearing_prices))},
    }


def threshold_checks() -> Dict[str, List[Dict]]:
    checks = {}
    for symbol, spec in RECOMMENDED_ORDERS.items():
        market = MARKETS[symbol]
        quantities = range(max(0, spec["bid_qty"] - 2), min(market.max_bid_qty, spec["bid_qty"] + 2) + 1)
        checks[symbol] = [asdict(simulate_bid(market, spec["bid_price"], qty)) for qty in quantities]
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the Round 1 manual auction strategy.")
    parser.add_argument("--stress-sims", type=int, default=1_000)
    parser.add_argument("--stress-pct", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=825)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    market_results = {}
    total_recommended_profit = 0.0
    total_exact_best_profit = 0.0

    for symbol, market in MARKETS.items():
        exact_results = exact_scan(market)
        recommended = simulate_bid(market, **RECOMMENDED_ORDERS[symbol])
        exact_best_profit = max(result.profit for result in exact_results)
        exact_best = [result for result in exact_results if result.profit == exact_best_profit]
        total_recommended_profit += recommended.profit
        total_exact_best_profit += exact_best_profit
        market_results[symbol] = {
            "book": asdict(market),
            "recommended": asdict(recommended),
            "exact_best_profit": exact_best_profit,
            "recommended_is_exact_optimum": recommended.profit == exact_best_profit,
            "number_of_exact_optima": len(exact_best),
            "top_10": [asdict(result) for result in top_results(exact_results, 10)],
            "stress": stress_order(
                market,
                RECOMMENDED_ORDERS[symbol]["bid_price"],
                RECOMMENDED_ORDERS[symbol]["bid_qty"],
                args.stress_sims,
                args.stress_pct,
                args.seed + len(symbol),
            ),
        }

    output = {
        "mechanism": {
            "clearing_rule": "Choose the price that maximizes traded volume; ties break to the higher clearing price.",
            "allocation_rule": "Price priority, then time priority; our order is submitted last at its bid price.",
            "settlement": "Uniform clearing price, then immediate buyback at product fair value less fees.",
        },
        "recommended_orders": RECOMMENDED_ORDERS,
        "market_results": market_results,
        "threshold_checks": threshold_checks(),
        "total_recommended_profit": round(total_recommended_profit, 10),
        "total_exact_best_profit": round(total_exact_best_profit, 10),
        "recommended_portfolio_is_exact_optimum": total_recommended_profit == total_exact_best_profit,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(f"total recommended profit: {total_recommended_profit:.2f}")
    print(f"total exact best profit: {total_exact_best_profit:.2f}")
    for symbol, result in market_results.items():
        rec = result["recommended"]
        print(
            f"{symbol}: bid {rec['bid_price']} x {rec['bid_qty']} clears {rec['clearing_price']} "
            f"allocates {rec['allocation']} profit {rec['profit']:.2f}; "
            f"exact optimum={result['recommended_is_exact_optimum']}"
        )
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
