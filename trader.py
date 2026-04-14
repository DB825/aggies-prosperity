import json
from typing import Dict, List

from datamodel import Order, OrderDepth, TradingState


class Trader:
    POSITION_LIMITS = {
        "EMERALDS": 80,
        "TOMATOES": 80,
    }

    PARAMS = {
        "EMERALDS": {
            "alpha": 0.08,
            "take_edge": 1.0,
            "make_edge": 1,
            "max_take": 30,
            "max_make": 12,
        },
        "TOMATOES": {
            "alpha": 0.25,
            "take_edge": 1.5,
            "make_edge": 2,
            "max_take": 20,
            "max_make": 10,
        },
    }

    def bid(self):
        return 15

    def run(self, state: TradingState):
        memory = self.load_memory(state.traderData)
        result: Dict[str, List[Order]] = {}

        for product, order_depth in state.order_depths.items():
            if product not in self.POSITION_LIMITS:
                result[product] = []
                continue

            params = self.PARAMS[product]
            fair = self.update_fair_value(product, order_depth, state, memory, params["alpha"])
            if fair <= 0:
                result[product] = []
                continue

            position = state.position.get(product, 0)
            orders: List[Order] = []

            buy_capacity = self.POSITION_LIMITS[product] - position
            sell_capacity = self.POSITION_LIMITS[product] + position

            buy_capacity = self.take_cheap_asks(
                product,
                order_depth,
                fair,
                params["take_edge"],
                params["max_take"],
                buy_capacity,
                orders,
            )
            sell_capacity = self.hit_expensive_bids(
                product,
                order_depth,
                fair,
                params["take_edge"],
                params["max_take"],
                sell_capacity,
                orders,
            )

            self.place_passive_quotes(
                product,
                order_depth,
                fair,
                position,
                params["make_edge"],
                params["max_make"],
                buy_capacity,
                sell_capacity,
                orders,
            )

            result[product] = orders

        traderData = json.dumps(memory, separators=(",", ":"))
        conversions = 0
        return result, conversions, traderData

    def load_memory(self, trader_data: str) -> Dict:
        if not trader_data:
            return {"fair": {}, "last_timestamp": 0}
        try:
            memory = json.loads(trader_data)
            if not isinstance(memory, dict):
                return {"fair": {}, "last_timestamp": 0}
            memory.setdefault("fair", {})
            memory.setdefault("last_timestamp", 0)
            return memory
        except Exception:
            return {"fair": {}, "last_timestamp": 0}

    def update_fair_value(
        self,
        product: str,
        order_depth: OrderDepth,
        state: TradingState,
        memory: Dict,
        alpha: float,
    ) -> float:
        book_estimate = self.book_fair_value(order_depth)
        trade_estimate = self.recent_trade_fair_value(product, state)

        if book_estimate is None and trade_estimate is None:
            return float(memory["fair"].get(product, 0))
        if book_estimate is None:
            estimate = trade_estimate
        elif trade_estimate is None:
            estimate = book_estimate
        else:
            estimate = 0.8 * book_estimate + 0.2 * trade_estimate

        previous = memory["fair"].get(product)
        if previous is None or previous == 0:
            fair = float(estimate)
        else:
            fair = (1 - alpha) * float(previous) + alpha * float(estimate)

        memory["fair"][product] = fair
        memory["last_timestamp"] = state.timestamp
        return fair

    def book_fair_value(self, order_depth: OrderDepth):
        best_bid = max(order_depth.buy_orders) if order_depth.buy_orders else None
        best_ask = min(order_depth.sell_orders) if order_depth.sell_orders else None

        if best_bid is None and best_ask is None:
            return None
        if best_bid is None:
            return float(best_ask)
        if best_ask is None:
            return float(best_bid)

        bid_volume = abs(order_depth.buy_orders[best_bid])
        ask_volume = abs(order_depth.sell_orders[best_ask])
        if bid_volume + ask_volume == 0:
            return (best_bid + best_ask) / 2

        return (best_bid * ask_volume + best_ask * bid_volume) / (bid_volume + ask_volume)

    def recent_trade_fair_value(self, product: str, state: TradingState):
        trades = state.market_trades.get(product, []) + state.own_trades.get(product, [])
        if not trades:
            return None

        total_volume = 0
        total_notional = 0
        for trade in trades[-8:]:
            volume = abs(trade.quantity)
            total_volume += volume
            total_notional += trade.price * volume

        if total_volume == 0:
            return None
        return total_notional / total_volume

    def take_cheap_asks(
        self,
        product: str,
        order_depth: OrderDepth,
        fair: float,
        edge: float,
        max_clip: int,
        buy_capacity: int,
        orders: List[Order],
    ) -> int:
        if buy_capacity <= 0:
            return buy_capacity

        for price, volume in sorted(order_depth.sell_orders.items()):
            if price > fair - edge or buy_capacity <= 0:
                break
            quantity = min(-volume, buy_capacity, max_clip)
            if quantity > 0:
                orders.append(Order(product, price, quantity))
                buy_capacity -= quantity
        return buy_capacity

    def hit_expensive_bids(
        self,
        product: str,
        order_depth: OrderDepth,
        fair: float,
        edge: float,
        max_clip: int,
        sell_capacity: int,
        orders: List[Order],
    ) -> int:
        if sell_capacity <= 0:
            return sell_capacity

        for price, volume in sorted(order_depth.buy_orders.items(), reverse=True):
            if price < fair + edge or sell_capacity <= 0:
                break
            quantity = min(volume, sell_capacity, max_clip)
            if quantity > 0:
                orders.append(Order(product, price, -quantity))
                sell_capacity -= quantity
        return sell_capacity

    def place_passive_quotes(
        self,
        product: str,
        order_depth: OrderDepth,
        fair: float,
        position: int,
        edge: int,
        max_clip: int,
        buy_capacity: int,
        sell_capacity: int,
        orders: List[Order],
    ) -> None:
        best_bid = max(order_depth.buy_orders) if order_depth.buy_orders else None
        best_ask = min(order_depth.sell_orders) if order_depth.sell_orders else None

        inventory_skew = position / self.POSITION_LIMITS[product]
        fair_with_skew = fair - inventory_skew

        bid_price = int(fair_with_skew - edge)
        ask_price = int(fair_with_skew + edge)

        if best_bid is not None:
            bid_price = min(bid_price, best_bid + 1)
        if best_ask is not None:
            ask_price = max(ask_price, best_ask - 1)

        if bid_price >= ask_price:
            bid_price = int(fair_with_skew - edge)
            ask_price = int(fair_with_skew + edge)

        if buy_capacity > 0 and (best_ask is None or bid_price < best_ask):
            quantity = min(max_clip, buy_capacity)
            orders.append(Order(product, bid_price, quantity))

        if sell_capacity > 0 and (best_bid is None or ask_price > best_bid):
            quantity = min(max_clip, sell_capacity)
            orders.append(Order(product, ask_price, -quantity))
