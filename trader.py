import json
import math
from typing import Dict, List, Optional, Tuple

from datamodel import Order, OrderDepth, TradingState


class Trader:
    POSITION_LIMITS = {
        "ASH_COATED_OSMIUM": 80,
        "INTARIAN_PEPPER_ROOT": 80,
    }

    ASH_ANCHOR = 10000.0
    PEPPER_SLOPE = 0.001
    PEPPER_TARGET = 80
    PEPPER_EXIT_TIMESTAMP = 10_000_000
    PEPPER_FORCE_EXIT_TIMESTAMP = 10_000_000
    PEPPER_FORCE_EXIT_EDGE = 8.0
    PEPPER_TREND_STOP_LOSS = 35.0
    PEPPER_STOP_LOSS_EXIT_EDGE = 60.0
    RISK_COOLDOWN = 50_000
    ASH_STOP_LOSS_DEVIATION = 35.0
    ASH_STOP_LOSS_EXIT_EDGE = 10.0

    PARAMS = {
        "ASH_COATED_OSMIUM": {
            "fair_alpha": 0.12,
            "imbalance_weight": 0.4,
            "take_edge": 0.0,
            "make_edge": 3.0,
            "max_take": 32,
            "max_make": 22,
            "inventory_skew": 2.0,
            "cross_inventory_skew": 1.25,
            "inventory_imbalance_threshold": 40,
        },
        "INTARIAN_PEPPER_ROOT": {
            "intercept_alpha": 0.18,
            "trend_buy_edge": 8.0,
            "rich_sell_edge": 14.0,
            "exit_sell_edge": 5.0,
            "make_edge": 2.0,
            "max_take": 10,
            "max_make": 30,
            "inventory_skew": 0.08,
        },
    }

    def bid(self):
        return 825

    def run(self, state: TradingState):
        memory = self.load_memory(state.traderData)
        result: Dict[str, List[Order]] = {}

        for product, order_depth in state.order_depths.items():
            if product not in self.POSITION_LIMITS:
                result[product] = []
                continue

            fair = self.estimate_fair_value(product, order_depth, state, memory)
            if fair <= 0:
                result[product] = []
                continue

            position = state.position.get(product, 0)
            risk_active = self.is_risk_active(product, state.timestamp, memory)
            if product == "INTARIAN_PEPPER_ROOT":
                orders = self.trade_pepper_root(product, order_depth, state.timestamp, fair, position, risk_active)
            else:
                orders = self.trade_osmium(product, order_depth, state.timestamp, fair, position, risk_active)

            result[product] = orders

        memory["last_timestamp"] = state.timestamp
        traderData = json.dumps(memory, separators=(",", ":"))
        conversions = 0
        return result, conversions, traderData

    def load_memory(self, trader_data: str) -> Dict:
        baseline = {"fair": {}, "intercept": {}, "open_intercept": {}, "risk": {}, "last_timestamp": 0}
        if not trader_data:
            return baseline
        try:
            memory = json.loads(trader_data)
            if not isinstance(memory, dict):
                return baseline
            memory.setdefault("fair", {})
            memory.setdefault("intercept", {})
            memory.setdefault("open_intercept", {})
            memory.setdefault("risk", {})
            memory.setdefault("last_timestamp", 0)
            return memory
        except Exception:
            return baseline

    def estimate_fair_value(
        self,
        product: str,
        order_depth: OrderDepth,
        state: TradingState,
        memory: Dict,
    ) -> float:
        if product == "INTARIAN_PEPPER_ROOT":
            return self.estimate_pepper_fair(order_depth, state, memory)
        return self.estimate_osmium_fair(product, order_depth, state, memory)

    def estimate_osmium_fair(
        self,
        product: str,
        order_depth: OrderDepth,
        state: TradingState,
        memory: Dict,
    ) -> float:
        params = self.PARAMS[product]
        book_estimate = self.book_fair_value(order_depth)
        trade_estimate = self.recent_trade_fair_value(product, state)
        previous = float(memory["fair"].get(product, self.ASH_ANCHOR))

        if book_estimate is None and trade_estimate is None:
            base_fair = previous
        elif book_estimate is None:
            base_fair = 0.85 * previous + 0.15 * float(trade_estimate)
        elif trade_estimate is None:
            base_fair = float(book_estimate)
        else:
            base_fair = 0.85 * float(book_estimate) + 0.15 * float(trade_estimate)

        fair_alpha = params["fair_alpha"]
        smoothed = (1 - fair_alpha) * previous + fair_alpha * base_fair
        memory["fair"][product] = smoothed
        if abs(base_fair - self.ASH_ANCHOR) > self.ASH_STOP_LOSS_DEVIATION:
            self.activate_risk_mode(product, state.timestamp, memory, "anchor_break")

        imbalance = self.book_imbalance(order_depth)
        position = state.position.get(product, 0)
        threshold = params["inventory_imbalance_threshold"]
        if abs(position) > threshold and position * imbalance > 0:
            imbalance = 0.0
        return smoothed + params["imbalance_weight"] * imbalance

    def estimate_pepper_fair(
        self,
        order_depth: OrderDepth,
        state: TradingState,
        memory: Dict,
    ) -> float:
        product = "INTARIAN_PEPPER_ROOT"
        params = self.PARAMS[product]
        book_estimate = self.book_fair_value(order_depth)
        previous_fair = memory["fair"].get(product)
        previous_intercept = memory["intercept"].get(product)

        if book_estimate is not None:
            observed_intercept = float(book_estimate) - self.PEPPER_SLOPE * state.timestamp
            memory["open_intercept"].setdefault(product, observed_intercept)
            if observed_intercept < float(memory["open_intercept"][product]) - self.PEPPER_TREND_STOP_LOSS:
                self.activate_risk_mode(product, state.timestamp, memory, "trend_break")
            if previous_intercept is None:
                intercept = observed_intercept
            else:
                alpha = params["intercept_alpha"]
                intercept = (1 - alpha) * float(previous_intercept) + alpha * observed_intercept
        elif previous_intercept is not None:
            intercept = float(previous_intercept)
        elif previous_fair is not None:
            intercept = float(previous_fair) - self.PEPPER_SLOPE * state.timestamp
        else:
            return 0.0

        fair = intercept + self.PEPPER_SLOPE * state.timestamp
        memory["intercept"][product] = intercept
        memory["fair"][product] = fair
        return fair

    def activate_risk_mode(self, product: str, timestamp: int, memory: Dict, reason: str) -> None:
        risk = memory.setdefault("risk", {}).setdefault(product, {})
        risk["active_until"] = max(int(risk.get("active_until", 0)), timestamp + self.RISK_COOLDOWN)
        risk["reason"] = reason

    def is_risk_active(self, product: str, timestamp: int, memory: Dict) -> bool:
        risk = memory.get("risk", {}).get(product, {})
        return timestamp <= int(risk.get("active_until", -1))

    def trade_osmium(
        self,
        product: str,
        order_depth: OrderDepth,
        timestamp: int,
        fair: float,
        position: int,
        risk_active: bool,
    ) -> List[Order]:
        params = self.PARAMS[product]
        orders: List[Order] = []
        position_after = position
        exit_window = timestamp >= self.PEPPER_EXIT_TIMESTAMP

        if risk_active:
            self.flatten_inventory(
                product,
                order_depth,
                fair,
                position_after,
                params["max_take"],
                self.ASH_STOP_LOSS_EXIT_EDGE,
                orders,
            )
            return orders

        cross_skew = params["cross_inventory_skew"] * position_after / self.POSITION_LIMITS[product]
        position_after = self.take_cheap_asks(
            product,
            order_depth,
            fair - params["take_edge"] - cross_skew,
            params["max_take"],
            position_after,
            orders,
        )
        cross_skew = params["cross_inventory_skew"] * position_after / self.POSITION_LIMITS[product]
        position_after = self.hit_expensive_bids(
            product,
            order_depth,
            fair + params["take_edge"] - cross_skew,
            params["max_take"],
            position_after,
            orders,
            max_inventory_to_sell=None,
        )

        if exit_window:
            if position_after > 0:
                position_after = self.hit_expensive_bids(
                    product,
                    order_depth,
                    fair - 8.0,
                    params["max_take"],
                    position_after,
                    orders,
                    max_inventory_to_sell=position_after,
                )
            elif position_after < 0:
                position_after = self.buy_inventory_to_cover(
                    product,
                    order_depth,
                    fair + 8.0,
                    params["max_take"],
                    position_after,
                    orders,
                )
            return orders

        self.place_mean_reversion_quotes(
            product,
            order_depth,
            fair,
            position_after,
            target_position=0,
            edge=params["make_edge"],
            max_clip=params["max_make"],
            inventory_skew=params["inventory_skew"],
            orders=orders,
        )
        return orders

    def trade_pepper_root(
        self,
        product: str,
        order_depth: OrderDepth,
        timestamp: int,
        fair: float,
        position: int,
        risk_active: bool,
    ) -> List[Order]:
        params = self.PARAMS[product]
        orders: List[Order] = []
        position_after = position
        exit_window = timestamp >= self.PEPPER_EXIT_TIMESTAMP

        if risk_active:
            self.flatten_inventory(
                product,
                order_depth,
                fair,
                position_after,
                max(params["max_take"], 20),
                self.PEPPER_STOP_LOSS_EXIT_EDGE,
                orders,
            )
            return orders

        if not exit_window:
            position_after = self.take_cheap_asks(
                product,
                order_depth,
                fair + params["trend_buy_edge"],
                params["max_take"],
                position_after,
                orders,
            )

            if position_after > self.PEPPER_TARGET:
                sellable = position_after - self.PEPPER_TARGET
                position_after = self.hit_expensive_bids(
                    product,
                    order_depth,
                    fair + params["rich_sell_edge"],
                    params["max_take"],
                    position_after,
                    orders,
                    max_inventory_to_sell=sellable,
                )
        else:
            sellable = max(0, position_after)
            exit_edge = params["exit_sell_edge"]
            if timestamp >= self.PEPPER_FORCE_EXIT_TIMESTAMP:
                exit_edge = self.PEPPER_FORCE_EXIT_EDGE
            position_after = self.hit_expensive_bids(
                product,
                order_depth,
                fair - exit_edge,
                params["max_take"],
                position_after,
                orders,
                max_inventory_to_sell=sellable,
            )

        if not exit_window:
            self.place_pepper_quotes(product, order_depth, fair, position_after, orders)

        return orders

    def book_fair_value(self, order_depth: OrderDepth) -> Optional[float]:
        best_bid, best_ask = self.best_bid_ask(order_depth)
        if best_bid is None or best_ask is None:
            return None

        bid_volume = abs(order_depth.buy_orders[best_bid])
        ask_volume = abs(order_depth.sell_orders[best_ask])
        if bid_volume + ask_volume == 0:
            return (best_bid + best_ask) / 2

        return (best_bid * ask_volume + best_ask * bid_volume) / (bid_volume + ask_volume)

    def recent_trade_fair_value(self, product: str, state: TradingState) -> Optional[float]:
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

    def book_imbalance(self, order_depth: OrderDepth) -> float:
        bid_volume = sum(abs(volume) for _, volume in sorted(order_depth.buy_orders.items(), reverse=True)[:2])
        ask_volume = sum(abs(volume) for _, volume in sorted(order_depth.sell_orders.items())[:2])
        total = bid_volume + ask_volume
        if total == 0:
            return 0.0
        return (bid_volume - ask_volume) / total

    def take_cheap_asks(
        self,
        product: str,
        order_depth: OrderDepth,
        limit_price: float,
        max_clip: int,
        position: int,
        orders: List[Order],
    ) -> int:
        buy_capacity = self.POSITION_LIMITS[product] - position
        if buy_capacity <= 0:
            return position

        for price, volume in sorted(order_depth.sell_orders.items()):
            if price > limit_price or buy_capacity <= 0:
                break
            quantity = min(abs(volume), buy_capacity, max_clip)
            if quantity > 0:
                orders.append(Order(product, price, quantity))
                buy_capacity -= quantity
                position += quantity
        return position

    def flatten_inventory(
        self,
        product: str,
        order_depth: OrderDepth,
        fair: float,
        position: int,
        max_clip: int,
        exit_edge: float,
        orders: List[Order],
    ) -> int:
        if position > 0:
            return self.hit_expensive_bids(
                product,
                order_depth,
                fair - exit_edge,
                max_clip,
                position,
                orders,
                max_inventory_to_sell=position,
            )
        if position < 0:
            return self.buy_inventory_to_cover(
                product,
                order_depth,
                fair + exit_edge,
                max_clip,
                position,
                orders,
            )
        return position

    def hit_expensive_bids(
        self,
        product: str,
        order_depth: OrderDepth,
        limit_price: float,
        max_clip: int,
        position: int,
        orders: List[Order],
        max_inventory_to_sell: Optional[int],
    ) -> int:
        sell_capacity = self.POSITION_LIMITS[product] + position
        if max_inventory_to_sell is not None:
            sell_capacity = min(sell_capacity, max_inventory_to_sell)
        if sell_capacity <= 0:
            return position

        for price, volume in sorted(order_depth.buy_orders.items(), reverse=True):
            if price < limit_price or sell_capacity <= 0:
                break
            quantity = min(abs(volume), sell_capacity, max_clip)
            if quantity > 0:
                orders.append(Order(product, price, -quantity))
                sell_capacity -= quantity
                position -= quantity
        return position

    def buy_inventory_to_cover(
        self,
        product: str,
        order_depth: OrderDepth,
        limit_price: float,
        max_clip: int,
        position: int,
        orders: List[Order],
    ) -> int:
        buy_capacity = min(-position, self.POSITION_LIMITS[product] - position)
        if buy_capacity <= 0:
            return position

        for price, volume in sorted(order_depth.sell_orders.items()):
            if price > limit_price or buy_capacity <= 0:
                break
            quantity = min(abs(volume), buy_capacity, max_clip)
            if quantity > 0:
                orders.append(Order(product, price, quantity))
                buy_capacity -= quantity
                position += quantity
        return position

    def place_mean_reversion_quotes(
        self,
        product: str,
        order_depth: OrderDepth,
        fair: float,
        position: int,
        target_position: int,
        edge: float,
        max_clip: int,
        inventory_skew: float,
        orders: List[Order],
    ) -> None:
        best_bid, best_ask = self.best_bid_ask(order_depth)
        limit = self.POSITION_LIMITS[product]
        buy_capacity = limit - position
        sell_capacity = limit + position
        skew = inventory_skew * (position - target_position) / limit
        fair_with_skew = fair - skew

        bid_price = math.floor(fair_with_skew - edge)
        ask_price = math.ceil(fair_with_skew + edge)

        if best_bid is not None:
            bid_price = min(bid_price, best_bid + 1)
        if best_ask is not None:
            ask_price = max(ask_price, best_ask - 1)

        if bid_price >= ask_price:
            bid_price = math.floor(fair_with_skew - edge)
            ask_price = math.ceil(fair_with_skew + edge)

        if buy_capacity > 0 and (best_ask is None or bid_price < best_ask):
            orders.append(Order(product, int(bid_price), min(max_clip, buy_capacity)))

        if sell_capacity > 0 and (best_bid is None or ask_price > best_bid):
            orders.append(Order(product, int(ask_price), -min(max_clip, sell_capacity)))

    def place_pepper_quotes(
        self,
        product: str,
        order_depth: OrderDepth,
        fair: float,
        position: int,
        orders: List[Order],
    ) -> None:
        params = self.PARAMS[product]
        best_bid, best_ask = self.best_bid_ask(order_depth)
        buy_capacity = self.POSITION_LIMITS[product] - position
        if buy_capacity <= 0:
            return

        missing_inventory = self.PEPPER_TARGET - position
        fair_with_skew = fair + params["inventory_skew"] * max(0, missing_inventory)
        bid_price = math.floor(fair_with_skew - params["make_edge"])
        if best_bid is not None:
            bid_price = min(bid_price, best_bid + 1)

        if best_ask is None or bid_price < best_ask:
            orders.append(Order(product, int(bid_price), min(params["max_make"], buy_capacity)))

    def best_bid_ask(self, order_depth: OrderDepth) -> Tuple[Optional[int], Optional[int]]:
        best_bid = max(order_depth.buy_orders) if order_depth.buy_orders else None
        best_ask = min(order_depth.sell_orders) if order_depth.sell_orders else None
        return best_bid, best_ask
