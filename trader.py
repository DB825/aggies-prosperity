import json
import math
from typing import Dict, List, Optional, Tuple

from datamodel import Order, OrderDepth, TradingState


class Trader:
    POSITION_LIMITS = {
        "HYDROGEL_PACK": 200,
        "VELVETFRUIT_EXTRACT": 200,
        "VEV_4000": 300,
        "VEV_4500": 300,
        "VEV_5000": 300,
        "VEV_5100": 300,
        "VEV_5200": 300,
        "VEV_5300": 300,
        "VEV_5400": 300,
        "VEV_5500": 300,
        "VEV_6000": 300,
        "VEV_6500": 300,
    }

    INTERNAL_LIMITS = {
        "VEV_4000": 80,
        "VEV_4500": 80,
        "VEV_5000": 200,
        "VEV_5100": 200,
        "VEV_5200": 200,
        "VEV_5300": 200,
        "VEV_5400": 200,
        "VEV_5500": 200,
        "VEV_6000": 20,
        "VEV_6500": 20,
    }

    OPTION_STRIKES = {
        "VEV_4000": 4000,
        "VEV_4500": 4500,
        "VEV_5000": 5000,
        "VEV_5100": 5100,
        "VEV_5200": 5200,
        "VEV_5300": 5300,
        "VEV_5400": 5400,
        "VEV_5500": 5500,
        "VEV_6000": 6000,
        "VEV_6500": 6500,
    }

    OPTION_PRIORITY = [
        "VEV_5500",
        "VEV_5400",
        "VEV_5300",
        "VEV_5200",
        "VEV_5100",
        "VEV_5000",
        "VEV_4500",
        "VEV_4000",
        "VEV_6000",
        "VEV_6500",
    ]

    # Calibrated from the Round 3 historical chain (TTE 8/7/6 days) and then
    # rolled forward to live TTE=5 days with time-to-expiry adjusted in pricing.
    OPTION_VOLS = {
        4000: 1e-6,
        4500: 1e-6,
        5000: 0.01220,
        5100: 0.01210,
        5200: 0.01225,
        5300: 0.01238,
        5400: 0.01155,
        5500: 0.01255,
        6000: 1e-6,
        6500: 1e-6,
    }

    OPTION_TAKE_EDGES = {
        "VEV_4000": 2.0,
        "VEV_4500": 2.0,
        "VEV_5000": 0.25,
        "VEV_5100": 0.25,
        "VEV_5200": 0.25,
        "VEV_5300": 0.25,
        "VEV_5400": 0.25,
        "VEV_5500": 0.25,
        "VEV_6000": 1.0,
        "VEV_6500": 1.0,
    }

    OPTION_MAKE_EDGES = {
        "VEV_4000": 3.0,
        "VEV_4500": 3.0,
        "VEV_5000": 1.0,
        "VEV_5100": 1.0,
        "VEV_5200": 1.0,
        "VEV_5300": 1.0,
        "VEV_5400": 1.0,
        "VEV_5500": 1.0,
        "VEV_6000": 1.0,
        "VEV_6500": 1.0,
    }

    OPTION_MAX_TAKE = {
        "VEV_4000": 10,
        "VEV_4500": 10,
        "VEV_5000": 20,
        "VEV_5100": 20,
        "VEV_5200": 25,
        "VEV_5300": 25,
        "VEV_5400": 25,
        "VEV_5500": 25,
        "VEV_6000": 8,
        "VEV_6500": 8,
    }

    OPTION_MAX_MAKE = {
        "VEV_4000": 6,
        "VEV_4500": 6,
        "VEV_5000": 10,
        "VEV_5100": 10,
        "VEV_5200": 12,
        "VEV_5300": 12,
        "VEV_5400": 12,
        "VEV_5500": 12,
        "VEV_6000": 6,
        "VEV_6500": 6,
    }

    HYDROGEL_ANCHOR = 10000.0
    HYDROGEL_ALPHA = 0.02
    HYDROGEL_ANCHOR_PULL = 0.12
    HYDROGEL_IMBALANCE_WEIGHT = 5.0
    HYDROGEL_TAKE_EDGE = 7.5
    HYDROGEL_MAKE_EDGE = 8.0
    HYDROGEL_MAX_TAKE = 30
    HYDROGEL_MAX_MAKE = 24
    HYDROGEL_INVENTORY_SKEW = 18.0

    VELVET_INITIAL_FAIR = 5250.0
    VELVET_ALPHA = 0.02
    VELVET_IMBALANCE_WEIGHT = 1.5
    VELVET_TAKE_EDGE = 8.5
    VELVET_MAKE_EDGE = 4.0
    VELVET_MAX_TAKE = 25
    VELVET_MAX_MAKE = 28
    VELVET_INVENTORY_SKEW = 10.0

    OPTION_DELTA_BUDGET = 550.0
    OPTION_REDUCTION_START = 995_000
    UNDERLYING_REDUCTION_START = 996_000
    LIVE_TTE_DAYS = 5.0

    def bid(self):
        return 0

    def run(self, state: TradingState):
        memory = self.load_memory(state.traderData)
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}

        hydro_depth = state.order_depths.get("HYDROGEL_PACK")
        velvet_depth = state.order_depths.get("VELVETFRUIT_EXTRACT")

        hydro_fair = None
        if hydro_depth is not None:
            hydro_fair = self.estimate_hydrogel_fair(hydro_depth, memory)
            result["HYDROGEL_PACK"] = self.trade_hydrogel(
                hydro_depth,
                hydro_fair,
                state.position.get("HYDROGEL_PACK", 0),
                state.timestamp,
            )

        velvet_fair = None
        spot_for_options = None
        if velvet_depth is not None:
            velvet_fair = self.estimate_velvet_fair(velvet_depth, memory)
            book_spot = self.book_fair_value(velvet_depth)
            spot_for_options = book_spot if book_spot is not None else velvet_fair

        if spot_for_options is not None:
            option_delta = self.portfolio_delta(state.position, spot_for_options, self.time_to_expiry(state))
        else:
            option_delta = 0.0

        if velvet_depth is not None and velvet_fair is not None:
            hedge_target = self.clamp_int(int(round(-0.25 * option_delta)), -80, 80)
            result["VELVETFRUIT_EXTRACT"] = self.trade_velvet(
                velvet_depth,
                velvet_fair,
                state.position.get("VELVETFRUIT_EXTRACT", 0),
                state.timestamp,
                hedge_target,
            )

        if spot_for_options is not None:
            option_orders = self.trade_options(state, spot_for_options)
            result.update(option_orders)

        memory["last_timestamp"] = state.timestamp
        trader_data = json.dumps(memory, separators=(",", ":"))
        return result, 0, trader_data

    def load_memory(self, trader_data: str) -> Dict:
        baseline = {"ema": {}, "last_timestamp": 0}
        if not trader_data:
            return baseline
        try:
            memory = json.loads(trader_data)
            if not isinstance(memory, dict):
                return baseline
            memory.setdefault("ema", {})
            memory.setdefault("last_timestamp", 0)
            return memory
        except Exception:
            return baseline

    def time_to_expiry(self, state: TradingState) -> float:
        observations = getattr(state, "observations", None)
        plain = getattr(observations, "plainValueObservations", {}) if observations else {}
        for key in ("VELVETFRUIT_TTE", "ROUND3_TTE", "TTE"):
            if key in plain:
                try:
                    return max(0.05, float(plain[key]))
                except Exception:
                    pass
        return self.LIVE_TTE_DAYS

    def estimate_hydrogel_fair(self, order_depth: OrderDepth, memory: Dict) -> float:
        book_fair = self.book_fair_value(order_depth)
        previous = float(memory["ema"].get("HYDROGEL_PACK", self.HYDROGEL_ANCHOR))
        observed = previous if book_fair is None else float(book_fair)
        ema = (1 - self.HYDROGEL_ALPHA) * previous + self.HYDROGEL_ALPHA * observed
        ema = (1 - self.HYDROGEL_ANCHOR_PULL) * ema + self.HYDROGEL_ANCHOR_PULL * self.HYDROGEL_ANCHOR
        memory["ema"]["HYDROGEL_PACK"] = ema
        return ema + self.HYDROGEL_IMBALANCE_WEIGHT * self.book_imbalance(order_depth)

    def estimate_velvet_fair(self, order_depth: OrderDepth, memory: Dict) -> float:
        book_fair = self.book_fair_value(order_depth)
        previous = float(memory["ema"].get("VELVETFRUIT_EXTRACT", self.VELVET_INITIAL_FAIR))
        observed = previous if book_fair is None else float(book_fair)
        ema = (1 - self.VELVET_ALPHA) * previous + self.VELVET_ALPHA * observed
        memory["ema"]["VELVETFRUIT_EXTRACT"] = ema
        return ema + self.VELVET_IMBALANCE_WEIGHT * self.book_imbalance(order_depth)

    def trade_hydrogel(
        self,
        order_depth: OrderDepth,
        fair: float,
        position: int,
        timestamp: int,
    ) -> List[Order]:
        orders: List[Order] = []
        position_after = position
        reduce_only = timestamp >= self.UNDERLYING_REDUCTION_START

        if not reduce_only or position_after < 0:
            position_after = self.take_asks_with_budget(
                "HYDROGEL_PACK",
                order_depth,
                fair - self.HYDROGEL_TAKE_EDGE,
                self.HYDROGEL_MAX_TAKE,
                position_after,
                orders,
            )

        if not reduce_only or position_after > 0:
            position_after = self.hit_bids_with_budget(
                "HYDROGEL_PACK",
                order_depth,
                fair + self.HYDROGEL_TAKE_EDGE,
                self.HYDROGEL_MAX_TAKE,
                position_after,
                orders,
            )

        if not reduce_only:
            self.place_mean_reversion_quotes(
                "HYDROGEL_PACK",
                order_depth,
                fair,
                position_after,
                0,
                self.HYDROGEL_MAKE_EDGE,
                self.HYDROGEL_MAX_MAKE,
                self.HYDROGEL_INVENTORY_SKEW,
                orders,
            )
        return orders

    def trade_velvet(
        self,
        order_depth: OrderDepth,
        fair: float,
        position: int,
        timestamp: int,
        target_position: int,
    ) -> List[Order]:
        orders: List[Order] = []
        position_after = position
        reduce_only = timestamp >= self.UNDERLYING_REDUCTION_START

        if not reduce_only or position_after < target_position:
            position_after = self.take_asks_with_budget(
                "VELVETFRUIT_EXTRACT",
                order_depth,
                fair - self.VELVET_TAKE_EDGE,
                self.VELVET_MAX_TAKE,
                position_after,
                orders,
            )

        if not reduce_only or position_after > target_position:
            position_after = self.hit_bids_with_budget(
                "VELVETFRUIT_EXTRACT",
                order_depth,
                fair + self.VELVET_TAKE_EDGE,
                self.VELVET_MAX_TAKE,
                position_after,
                orders,
            )

        if not reduce_only:
            self.place_mean_reversion_quotes(
                "VELVETFRUIT_EXTRACT",
                order_depth,
                fair,
                position_after,
                target_position,
                self.VELVET_MAKE_EDGE,
                self.VELVET_MAX_MAKE,
                self.VELVET_INVENTORY_SKEW,
                orders,
            )
        return orders

    def trade_options(self, state: TradingState, spot: float) -> Dict[str, List[Order]]:
        tte = self.time_to_expiry(state)
        shadow_positions = {
            symbol: int(state.position.get(symbol, 0))
            for symbol in self.OPTION_STRIKES
        }
        shadow_delta = self.portfolio_delta(shadow_positions, spot, tte)
        reduce_only = state.timestamp >= self.OPTION_REDUCTION_START
        orders_by_product: Dict[str, List[Order]] = {symbol: [] for symbol in self.OPTION_STRIKES}

        for symbol in self.OPTION_PRIORITY:
            order_depth = state.order_depths.get(symbol)
            if order_depth is None:
                continue

            strike = self.OPTION_STRIKES[symbol]
            delta = self.bs_delta(spot, strike, tte, self.option_vol(strike))
            fair = self.option_fair_value(spot, strike, tte)
            position = shadow_positions[symbol]
            internal_limit = self.position_limit(symbol)
            orders = orders_by_product[symbol]

            if not reduce_only or position < 0:
                buy_capacity = internal_limit - position
                if delta > 0:
                    buy_capacity = min(
                        buy_capacity,
                        int(max(0.0, (self.OPTION_DELTA_BUDGET - shadow_delta) / max(delta, 1e-6))),
                    )
                if buy_capacity > 0:
                    position, delta_change = self.take_option_asks(
                        symbol,
                        order_depth,
                        fair - self.OPTION_TAKE_EDGES[symbol],
                        self.OPTION_MAX_TAKE[symbol],
                        position,
                        buy_capacity,
                        delta,
                        orders,
                    )
                    shadow_delta += delta_change

            if not reduce_only or position > 0:
                sell_capacity = internal_limit + position
                if delta > 0:
                    sell_capacity = min(
                        sell_capacity,
                        int(max(0.0, (self.OPTION_DELTA_BUDGET + shadow_delta) / max(delta, 1e-6))),
                    )
                if sell_capacity > 0:
                    position, delta_change = self.hit_option_bids(
                        symbol,
                        order_depth,
                        fair + self.OPTION_TAKE_EDGES[symbol],
                        self.OPTION_MAX_TAKE[symbol],
                        position,
                        sell_capacity,
                        delta,
                        orders,
                    )
                    shadow_delta += delta_change

            shadow_positions[symbol] = position

            if not reduce_only:
                self.place_mean_reversion_quotes(
                    symbol,
                    order_depth,
                    fair,
                    position,
                    0,
                    self.OPTION_MAKE_EDGES[symbol],
                    self.OPTION_MAX_MAKE[symbol],
                    6.0,
                    orders,
                )

        return orders_by_product

    def option_fair_value(self, spot: float, strike: int, tte: float) -> float:
        return self.bs_call(spot, strike, tte, self.option_vol(strike))

    def option_vol(self, strike: int) -> float:
        return self.OPTION_VOLS[strike]

    def portfolio_delta(self, positions: Dict[str, int], spot: float, tte: float) -> float:
        total = 0.0
        for symbol, strike in self.OPTION_STRIKES.items():
            total += positions.get(symbol, 0) * self.bs_delta(spot, strike, tte, self.option_vol(strike))
        return total

    def take_option_asks(
        self,
        symbol: str,
        order_depth: OrderDepth,
        limit_price: float,
        max_clip: int,
        position: int,
        buy_capacity: int,
        delta: float,
        orders: List[Order],
    ) -> Tuple[int, float]:
        delta_change = 0.0
        for price, volume in sorted(order_depth.sell_orders.items()):
            if price > limit_price or buy_capacity <= 0:
                break
            quantity = min(abs(volume), buy_capacity, max_clip)
            if quantity > 0:
                orders.append(Order(symbol, price, quantity))
                position += quantity
                buy_capacity -= quantity
                delta_change += delta * quantity
        return position, delta_change

    def hit_option_bids(
        self,
        symbol: str,
        order_depth: OrderDepth,
        limit_price: float,
        max_clip: int,
        position: int,
        sell_capacity: int,
        delta: float,
        orders: List[Order],
    ) -> Tuple[int, float]:
        delta_change = 0.0
        for price, volume in sorted(order_depth.buy_orders.items(), reverse=True):
            if price < limit_price or sell_capacity <= 0:
                break
            quantity = min(abs(volume), sell_capacity, max_clip)
            if quantity > 0:
                orders.append(Order(symbol, price, -quantity))
                position -= quantity
                sell_capacity -= quantity
                delta_change -= delta * quantity
        return position, delta_change

    def take_asks_with_budget(
        self,
        product: str,
        order_depth: OrderDepth,
        limit_price: float,
        max_clip: int,
        position: int,
        orders: List[Order],
    ) -> int:
        buy_capacity = self.position_limit(product) - position
        if buy_capacity <= 0:
            return position

        for price, volume in sorted(order_depth.sell_orders.items()):
            if price > limit_price or buy_capacity <= 0:
                break
            quantity = min(abs(volume), buy_capacity, max_clip)
            if quantity > 0:
                orders.append(Order(product, price, quantity))
                position += quantity
                buy_capacity -= quantity
        return position

    def hit_bids_with_budget(
        self,
        product: str,
        order_depth: OrderDepth,
        limit_price: float,
        max_clip: int,
        position: int,
        orders: List[Order],
    ) -> int:
        sell_capacity = self.position_limit(product) + position
        if sell_capacity <= 0:
            return position

        for price, volume in sorted(order_depth.buy_orders.items(), reverse=True):
            if price < limit_price or sell_capacity <= 0:
                break
            quantity = min(abs(volume), sell_capacity, max_clip)
            if quantity > 0:
                orders.append(Order(product, price, -quantity))
                position -= quantity
                sell_capacity -= quantity
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
        limit = self.position_limit(product)
        buy_capacity = limit - position
        sell_capacity = limit + position
        skew = inventory_skew * (position - target_position) / max(1, limit)
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

    def position_limit(self, product: str) -> int:
        return min(self.POSITION_LIMITS[product], self.INTERNAL_LIMITS.get(product, self.POSITION_LIMITS[product]))

    def book_fair_value(self, order_depth: OrderDepth) -> Optional[float]:
        best_bid, best_ask = self.best_bid_ask(order_depth)
        if best_bid is None or best_ask is None:
            return None

        bid_volume = abs(order_depth.buy_orders[best_bid])
        ask_volume = abs(order_depth.sell_orders[best_ask])
        if bid_volume + ask_volume == 0:
            return (best_bid + best_ask) / 2.0

        return (best_bid * ask_volume + best_ask * bid_volume) / (bid_volume + ask_volume)

    def book_imbalance(self, order_depth: OrderDepth) -> float:
        best_bid, best_ask = self.best_bid_ask(order_depth)
        if best_bid is None or best_ask is None:
            return 0.0

        bid_volume = abs(order_depth.buy_orders[best_bid])
        ask_volume = abs(order_depth.sell_orders[best_ask])
        total = bid_volume + ask_volume
        if total == 0:
            return 0.0
        return (bid_volume - ask_volume) / total

    def best_bid_ask(self, order_depth: OrderDepth) -> Tuple[Optional[int], Optional[int]]:
        best_bid = max(order_depth.buy_orders) if order_depth.buy_orders else None
        best_ask = min(order_depth.sell_orders) if order_depth.sell_orders else None
        return best_bid, best_ask

    def bs_call(self, spot: float, strike: int, tte: float, sigma: float) -> float:
        if sigma <= 1e-9 or tte <= 0:
            return max(0.0, spot - strike)
        vol_term = sigma * math.sqrt(tte)
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tte) / vol_term
        d2 = d1 - vol_term
        return spot * self.norm_cdf(d1) - strike * self.norm_cdf(d2)

    def bs_delta(self, spot: float, strike: int, tte: float, sigma: float) -> float:
        if sigma <= 1e-9 or tte <= 0:
            return 1.0 if spot > strike else 0.0
        vol_term = sigma * math.sqrt(tte)
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tte) / vol_term
        return self.norm_cdf(d1)

    def norm_cdf(self, value: float) -> float:
        return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))

    def clamp_int(self, value: int, low: int, high: int) -> int:
        return max(low, min(high, value))
