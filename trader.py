import json
import math
from typing import Dict, List, Optional, Tuple

from datamodel import Order, OrderDepth, TradingState


class Trader:
    POSITION_LIMIT = 10

    PRODUCTS = [
        "GALAXY_SOUNDS_DARK_MATTER",
        "GALAXY_SOUNDS_BLACK_HOLES",
        "GALAXY_SOUNDS_PLANETARY_RINGS",
        "GALAXY_SOUNDS_SOLAR_WINDS",
        "GALAXY_SOUNDS_SOLAR_FLAMES",
        "SLEEP_POD_SUEDE",
        "SLEEP_POD_LAMB_WOOL",
        "SLEEP_POD_POLYESTER",
        "SLEEP_POD_NYLON",
        "SLEEP_POD_COTTON",
        "MICROCHIP_CIRCLE",
        "MICROCHIP_OVAL",
        "MICROCHIP_SQUARE",
        "MICROCHIP_RECTANGLE",
        "MICROCHIP_TRIANGLE",
        "PEBBLES_XS",
        "PEBBLES_S",
        "PEBBLES_M",
        "PEBBLES_L",
        "PEBBLES_XL",
        "ROBOT_VACUUMING",
        "ROBOT_MOPPING",
        "ROBOT_DISHES",
        "ROBOT_LAUNDRY",
        "ROBOT_IRONING",
        "UV_VISOR_YELLOW",
        "UV_VISOR_AMBER",
        "UV_VISOR_ORANGE",
        "UV_VISOR_RED",
        "UV_VISOR_MAGENTA",
        "TRANSLATOR_SPACE_GRAY",
        "TRANSLATOR_ASTRO_BLACK",
        "TRANSLATOR_ECLIPSE_CHARCOAL",
        "TRANSLATOR_GRAPHITE_MIST",
        "TRANSLATOR_VOID_BLUE",
        "PANEL_1X2",
        "PANEL_2X2",
        "PANEL_1X4",
        "PANEL_2X4",
        "PANEL_4X4",
        "OXYGEN_SHAKE_MORNING_BREATH",
        "OXYGEN_SHAKE_EVENING_BREATH",
        "OXYGEN_SHAKE_MINT",
        "OXYGEN_SHAKE_CHOCOLATE",
        "OXYGEN_SHAKE_GARLIC",
        "SNACKPACK_CHOCOLATE",
        "SNACKPACK_VANILLA",
        "SNACKPACK_PISTACHIO",
        "SNACKPACK_STRAWBERRY",
        "SNACKPACK_RASPBERRY",
    ]

    # Passive-maker parameters selected from per-product isolation, then
    # sanity-checked against leave-one-day replay and the official 1,000-tick
    # log. Products without enough isolated edge are intentionally idle.
    MAKE_PARAMS: Dict[str, Tuple[float, float]] = {
        "PANEL_1X4": (0.5, 4.0),
        "PANEL_2X2": (5.0, 0.0),
        "PANEL_2X4": (4.0, 0.0),
        "OXYGEN_SHAKE_CHOCOLATE": (1.0, 0.5),
        "OXYGEN_SHAKE_EVENING_BREATH": (1.0, 1.0),
        "OXYGEN_SHAKE_GARLIC": (2.0, 2.0),
        "OXYGEN_SHAKE_MINT": (7.0, 0.0),
        "SNACKPACK_CHOCOLATE": (0.5, 2.0),
        "SNACKPACK_PISTACHIO": (0.5, 0.5),
        "SNACKPACK_VANILLA": (4.0, 2.0),
        "SNACKPACK_STRAWBERRY": (4.0, 0.0),
        "SLEEP_POD_SUEDE": (2.0, 0.0),
        "SLEEP_POD_COTTON": (4.0, 0.5),
        "SLEEP_POD_POLYESTER": (5.0, 1.0),
        "SLEEP_POD_NYLON": (4.0, 0.0),
        "UV_VISOR_YELLOW": (1.5, 3.0),
        "UV_VISOR_ORANGE": (3.0, 0.0),
        "TRANSLATOR_ECLIPSE_CHARCOAL": (2.0, 0.0),
        "TRANSLATOR_VOID_BLUE": (2.0, 0.0),
        "TRANSLATOR_ASTRO_BLACK": (4.0, 0.0),
        "PEBBLES_XS": (5.0, 1.0),
        "ROBOT_MOPPING": (2.0, 1.0),
        "MICROCHIP_SQUARE": (1.0, 1.0),
        "MICROCHIP_OVAL": (1.0, 0.5),
    }

    JUMP_REVERSION = {
        "OXYGEN_SHAKE_CHOCOLATE": 30.0,
        "OXYGEN_SHAKE_EVENING_BREATH": 30.0,
    }

    # Learned from isolated one-product leave-one-day tests. These are deliberately
    # simple one-tick regimes so the live bot has no heavy model dependency.
    SIGNAL_PARAMS: Dict[str, Tuple[float, str, bool]] = {
        "ROBOT_IRONING": (25.0, "reversion", True),
        "MICROCHIP_OVAL": (30.0, "reversion", True),
        "OXYGEN_SHAKE_GARLIC": (25.0, "momentum", True),
        "PANEL_1X2": (25.0, "reversion", True),
        "SLEEP_POD_NYLON": (25.0, "reversion", True),
        "SNACKPACK_RASPBERRY": (20.0, "reversion", True),
    }

    GROUPS: Dict[str, Dict] = {}

    def run(self, state: TradingState):
        memory = self.load_memory(state.traderData)
        result: Dict[str, List[Order]] = {product: [] for product in state.order_depths}
        mids = {
            product: self.book_mid(depth)
            for product, depth in state.order_depths.items()
        }

        desired_targets = self.compute_group_targets(memory, mids)
        desired_targets.update(self.compute_jump_targets(memory, mids))
        desired_targets.update(self.compute_signal_targets(memory, mids))

        for product, depth in state.order_depths.items():
            orders: List[Order] = []
            live_position = state.position.get(product, 0)
            target = desired_targets.get(product)
            crossed_to_target = False
            if target is not None:
                order_count = len(orders)
                live_position = self.trade_to_target(product, depth, live_position, target, orders)
                crossed_to_target = len(orders) > order_count

            if not crossed_to_target and product in self.MAKE_PARAMS and mids.get(product) is not None:
                edge, skew = self.MAKE_PARAMS[product]
                self.add_passive_quotes(product, depth, live_position, mids[product], edge, skew, orders)

            result[product] = orders

        for product, mid in mids.items():
            if mid is not None:
                memory["last_mid"][product] = mid

        trader_data = json.dumps(memory, separators=(",", ":"))
        return result, 0, trader_data

    def load_memory(self, trader_data: str) -> Dict:
        baseline = {"last_mid": {}, "jump_target": {}, "group_target": {}, "signal_target": {}}
        if not trader_data:
            return baseline
        try:
            memory = json.loads(trader_data)
        except Exception:
            return baseline
        if not isinstance(memory, dict):
            return baseline
        memory.setdefault("last_mid", {})
        memory.setdefault("jump_target", {})
        memory.setdefault("group_target", {})
        memory.setdefault("signal_target", {})
        return memory

    def compute_group_targets(self, memory: Dict, mids: Dict[str, Optional[float]]) -> Dict[str, int]:
        targets: Dict[str, int] = {}
        for name, config in self.GROUPS.items():
            products = config["products"]
            if any(mids.get(product) is None for product in products):
                continue
            spread = sum(mids[product] for product in products) - config["anchor"]
            current = int(memory["group_target"].get(name, 0))
            if spread > config["entry"]:
                current = -int(config["target"])
            elif spread < -config["entry"]:
                current = int(config["target"])
            memory["group_target"][name] = current
            if current != 0:
                for product in products:
                    targets[product] = current
        return targets

    def compute_jump_targets(self, memory: Dict, mids: Dict[str, Optional[float]]) -> Dict[str, int]:
        targets: Dict[str, int] = {}
        for product, threshold in self.JUMP_REVERSION.items():
            mid = mids.get(product)
            last = memory["last_mid"].get(product)
            current = int(memory["jump_target"].get(product, 0))
            if mid is not None and last is not None:
                move = mid - float(last)
                if move > threshold:
                    current = -self.POSITION_LIMIT
                elif move < -threshold:
                    current = self.POSITION_LIMIT
            memory["jump_target"][product] = current
            if current != 0:
                targets[product] = current
        return targets

    def compute_signal_targets(self, memory: Dict, mids: Dict[str, Optional[float]]) -> Dict[str, int]:
        targets: Dict[str, int] = {}
        for product, (threshold, mode, sticky) in self.SIGNAL_PARAMS.items():
            mid = mids.get(product)
            last = memory["last_mid"].get(product)
            current = int(memory["signal_target"].get(product, 0))
            if mid is not None and last is not None:
                move = mid - float(last)
                if abs(move) > threshold:
                    direction = 1 if move > 0 else -1
                    if mode == "reversion":
                        direction *= -1
                    current = direction * self.POSITION_LIMIT
                elif not sticky:
                    current = 0
            memory["signal_target"][product] = current
            if current != 0:
                targets[product] = current
        return targets

    def trade_to_target(
        self,
        product: str,
        depth: OrderDepth,
        position: int,
        target: int,
        orders: List[Order],
    ) -> int:
        target = self.clamp_int(target, -self.POSITION_LIMIT, self.POSITION_LIMIT)
        delta = target - position
        if delta > 0:
            for price, volume in sorted(depth.sell_orders.items()):
                if delta <= 0:
                    break
                quantity = min(delta, -volume)
                if quantity > 0:
                    orders.append(Order(product, int(price), int(quantity)))
                    position += quantity
                    delta -= quantity
        elif delta < 0:
            sell_left = -delta
            for price, volume in sorted(depth.buy_orders.items(), reverse=True):
                if sell_left <= 0:
                    break
                quantity = min(sell_left, volume)
                if quantity > 0:
                    orders.append(Order(product, int(price), -int(quantity)))
                    position -= quantity
                    sell_left -= quantity
        return position

    def add_passive_quotes(
        self,
        product: str,
        depth: OrderDepth,
        position: int,
        fair: float,
        edge: float,
        skew: float,
        orders: List[Order],
    ) -> None:
        best_bid, best_ask = self.best_bid_ask(depth)
        if best_bid is None or best_ask is None:
            return

        adjusted_fair = fair - skew * position
        bid_price = min(best_bid + 1, math.floor(adjusted_fair - edge))
        ask_price = max(best_ask - 1, math.ceil(adjusted_fair + edge))

        if bid_price >= ask_price:
            return

        buy_capacity = self.POSITION_LIMIT - position
        sell_capacity = self.POSITION_LIMIT + position
        if buy_capacity > 0 and bid_price < best_ask:
            orders.append(Order(product, int(bid_price), int(buy_capacity)))
        if sell_capacity > 0 and ask_price > best_bid:
            orders.append(Order(product, int(ask_price), -int(sell_capacity)))

    def book_mid(self, depth: OrderDepth) -> Optional[float]:
        best_bid, best_ask = self.best_bid_ask(depth)
        if best_bid is None or best_ask is None:
            return None
        return (best_bid + best_ask) / 2.0

    def best_bid_ask(self, depth: OrderDepth) -> Tuple[Optional[int], Optional[int]]:
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        return best_bid, best_ask

    def clamp_int(self, value: int, lower: int, upper: int) -> int:
        return max(lower, min(upper, int(value)))
