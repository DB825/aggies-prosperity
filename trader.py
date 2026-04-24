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
        "VEV_4000": 60,
        "VEV_4500": 60,
        "VEV_5000": 150,
        "VEV_5100": 150,
        "VEV_5200": 150,
        "VEV_5300": 150,
        "VEV_5400": 150,
        "VEV_5500": 150,
        "VEV_6000": 10,
        "VEV_6500": 10,
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

    CORE_OPTION_SYMBOLS = [
        "VEV_5000",
        "VEV_5100",
        "VEV_5200",
        "VEV_5300",
        "VEV_5400",
        "VEV_5500",
    ]
    OPTION_LIVE_IV_ALPHA = 0.20
    OPTION_LIVE_SMILE_WEIGHT = 0.15
    OPTION_RESIDUAL_ALPHA = 0.04
    OPTION_RESIDUAL_SD_FLOOR = 0.75
    OPTION_RESIDUAL_ENTRY_Z = 1.25
    OPTION_RESIDUAL_QUOTE_Z = 0.60
    OPTION_V5000_ENTRY_Z = 0.90
    OPTION_PAIR_GAP_Z = 2.25
    OPTION_PAIR_TARGET_DELTA = 6.0
    OPTION_PAIR_MAX_LEG_CLIP = 12
    OPTION_STRONG_SINGLE_CLIP = 10
    OPTION_PASSIVE_CLIP = 6
    OPTION_SAME_SIDE_CORE_LIMIT = 360

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

    OPTION_DELTA_BUDGET = 300.0
    OPTION_DELTA_SOFT_LIMIT = 220.0
    OPTION_DELTA_HARD_LIMIT = 280.0
    OPTION_GROSS_POSITION_LIMIT = 700
    OPTION_REENTRY_DELTA = 150.0
    INVENTORY_DRAWDOWN_LIMIT = 18_000.0
    GLOBAL_DRAWDOWN_LIMIT = 26_000.0
    RISK_REENTRY_DRAWDOWN = 12_000.0
    OPTION_RISK_EXIT_EDGE = 0.75
    UNDERLYING_RISK_EXIT_EDGE = 4.0
    HEDGE_RATIO = 0.25
    RISK_HEDGE_RATIO = 0.50
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
        tte = self.time_to_expiry(state)

        hydro_fair = None
        if hydro_depth is not None:
            hydro_fair = self.estimate_hydrogel_fair(hydro_depth, memory)

        velvet_fair = None
        spot_for_options = None
        if velvet_depth is not None:
            velvet_fair = self.estimate_velvet_fair(velvet_depth, memory)
            book_spot = self.book_fair_value(velvet_depth)
            spot_for_options = book_spot if book_spot is not None else velvet_fair

        option_context: Dict[str, Dict] = {}
        if spot_for_options is not None:
            option_context = self.build_option_context(state, memory, spot_for_options, tte)

        mark_prices = self.build_mark_prices(state, hydro_fair, velvet_fair, spot_for_options, tte, option_context)
        risk_state = self.update_risk_state(state, memory, mark_prices, spot_for_options, tte, option_context)
        option_delta = risk_state["option_delta"]

        if hydro_depth is not None and hydro_fair is not None:
            result["HYDROGEL_PACK"] = self.trade_hydrogel(
                hydro_depth,
                hydro_fair,
                state.position.get("HYDROGEL_PACK", 0),
                state.timestamp,
                risk_state["reduce_all"],
            )

        if velvet_depth is not None and velvet_fair is not None:
            hedge_ratio = self.RISK_HEDGE_RATIO if risk_state["reduce_options"] else self.HEDGE_RATIO
            hedge_limit = 120 if risk_state["reduce_options"] else 80
            hedge_target = self.clamp_int(int(round(-hedge_ratio * option_delta)), -hedge_limit, hedge_limit)
            result["VELVETFRUIT_EXTRACT"] = self.trade_velvet(
                velvet_depth,
                velvet_fair,
                state.position.get("VELVETFRUIT_EXTRACT", 0),
                state.timestamp,
                hedge_target,
                risk_state["reduce_all"],
            )

        if spot_for_options is not None:
            option_orders = self.trade_options(
                state,
                spot_for_options,
                tte,
                risk_state,
                option_context,
            )
            result.update(option_orders)

        memory["last_timestamp"] = state.timestamp
        trader_data = json.dumps(memory, separators=(",", ":"))
        return result, 0, trader_data

    def load_memory(self, trader_data: str) -> Dict:
        baseline = {"ema": {}, "risk": {}, "options": {"live_vols": {}, "residuals": {}}, "last_timestamp": 0}
        if not trader_data:
            return baseline
        try:
            memory = json.loads(trader_data)
            if not isinstance(memory, dict):
                return baseline
            memory.setdefault("ema", {})
            memory.setdefault("risk", {})
            memory.setdefault("options", {})
            memory["options"].setdefault("live_vols", {})
            memory["options"].setdefault("residuals", {})
            memory.setdefault("last_timestamp", 0)
            return memory
        except Exception:
            return baseline

    def build_mark_prices(
        self,
        state: TradingState,
        hydro_fair: Optional[float],
        velvet_fair: Optional[float],
        spot_for_options: Optional[float],
        tte: float,
        option_context: Dict[str, Dict],
    ) -> Dict[str, float]:
        marks: Dict[str, float] = {}
        if hydro_fair is not None:
            marks["HYDROGEL_PACK"] = hydro_fair
        if velvet_fair is not None:
            marks["VELVETFRUIT_EXTRACT"] = velvet_fair
        if spot_for_options is not None:
            for symbol, strike in self.OPTION_STRIKES.items():
                if symbol in state.order_depths:
                    if symbol in option_context:
                        marks[symbol] = option_context[symbol]["fair"]
                    else:
                        marks[symbol] = self.option_fair_value(spot_for_options, strike, tte)
        return marks

    def update_risk_state(
        self,
        state: TradingState,
        memory: Dict,
        mark_prices: Dict[str, float],
        spot_for_options: Optional[float],
        tte: float,
        option_context: Dict[str, Dict],
    ) -> Dict[str, float]:
        risk = memory.setdefault("risk", {})
        inventory_mtm = float(risk.get("inventory_mtm", 0.0))
        peak_inventory_mtm = float(risk.get("peak_inventory_mtm", inventory_mtm))
        last_marks = risk.get("last_marks", {})
        last_positions = risk.get("last_positions", {})

        for product, previous_position in last_positions.items():
            if product not in mark_prices or product not in last_marks:
                continue
            inventory_mtm += float(previous_position) * (mark_prices[product] - float(last_marks[product]))

        peak_inventory_mtm = max(peak_inventory_mtm, inventory_mtm)
        drawdown = peak_inventory_mtm - inventory_mtm
        option_delta = 0.0
        if spot_for_options is not None:
            option_delta = self.portfolio_delta_from_context(state.position, option_context, spot_for_options, tte)
        option_gross = self.option_gross_position(state.position)
        core_long = self.core_side_position(state.position, 1)
        core_short = self.core_side_position(state.position, -1)

        reduce_options = bool(risk.get("reduce_options", False))
        reduce_all = bool(risk.get("reduce_all", False))
        if (
            abs(option_delta) >= self.OPTION_DELTA_HARD_LIMIT
            or option_gross >= self.OPTION_GROSS_POSITION_LIMIT
            or core_long >= self.OPTION_SAME_SIDE_CORE_LIMIT
            or core_short >= self.OPTION_SAME_SIDE_CORE_LIMIT
            or drawdown >= self.INVENTORY_DRAWDOWN_LIMIT
        ):
            reduce_options = True
        elif (
            reduce_options
            and abs(option_delta) <= self.OPTION_REENTRY_DELTA
            and drawdown <= self.RISK_REENTRY_DRAWDOWN
        ):
            reduce_options = False

        if drawdown >= self.GLOBAL_DRAWDOWN_LIMIT:
            reduce_all = True
        elif reduce_all and drawdown <= self.RISK_REENTRY_DRAWDOWN:
            reduce_all = False

        risk["inventory_mtm"] = inventory_mtm
        risk["peak_inventory_mtm"] = peak_inventory_mtm
        risk["drawdown"] = drawdown
        risk["option_delta"] = option_delta
        risk["option_gross"] = option_gross
        risk["core_long"] = core_long
        risk["core_short"] = core_short
        risk["reduce_options"] = reduce_options
        risk["reduce_all"] = reduce_all
        risk["last_marks"] = mark_prices
        risk["last_positions"] = {product: int(state.position.get(product, 0)) for product in mark_prices}

        return {
            "inventory_mtm": inventory_mtm,
            "drawdown": drawdown,
            "option_delta": option_delta,
            "option_gross": option_gross,
            "core_long": core_long,
            "core_short": core_short,
            "reduce_options": reduce_options,
            "reduce_all": reduce_all,
        }

    def build_option_context(
        self,
        state: TradingState,
        memory: Dict,
        spot: float,
        tte: float,
    ) -> Dict[str, Dict]:
        option_memory = memory.setdefault("options", {})
        live_vol_memory = option_memory.setdefault("live_vols", {})
        residual_memory = option_memory.setdefault("residuals", {})
        context: Dict[str, Dict] = {}

        for symbol, strike in self.OPTION_STRIKES.items():
            order_depth = state.order_depths.get(symbol)
            if order_depth is None:
                continue

            mid = self.midpoint(order_depth)
            prior_vol = self.option_vol(strike)
            previous_live = float(live_vol_memory.get(symbol, prior_vol))
            live_iv = None
            if symbol in self.CORE_OPTION_SYMBOLS and mid is not None:
                live_iv = self.implied_volatility(mid, spot, strike, tte, prior_vol)
            smoothed_live = previous_live
            if live_iv is not None:
                smoothed_live = (1 - self.OPTION_LIVE_IV_ALPHA) * previous_live + self.OPTION_LIVE_IV_ALPHA * live_iv
            live_vol_memory[symbol] = smoothed_live

            blended_vol = prior_vol
            if symbol in self.CORE_OPTION_SYMBOLS:
                blended_vol = (
                    (1 - self.OPTION_LIVE_SMILE_WEIGHT) * prior_vol
                    + self.OPTION_LIVE_SMILE_WEIGHT * smoothed_live
                )

            fair = self.option_fair_value(spot, strike, tte, blended_vol)
            delta = self.bs_delta(spot, strike, tte, blended_vol)
            residual = (mid - fair) if mid is not None else 0.0

            residual_state = residual_memory.get(symbol, {})
            mean = float(residual_state.get("mean", 0.0))
            var = float(residual_state.get("var", self.OPTION_RESIDUAL_SD_FLOOR ** 2))
            sd = math.sqrt(max(var, self.OPTION_RESIDUAL_SD_FLOOR ** 2))
            zscore = (residual - mean) / sd if sd > 0 else 0.0

            centered = residual - mean
            new_mean = (1 - self.OPTION_RESIDUAL_ALPHA) * mean + self.OPTION_RESIDUAL_ALPHA * residual
            new_var = (1 - self.OPTION_RESIDUAL_ALPHA) * var + self.OPTION_RESIDUAL_ALPHA * centered * centered
            residual_memory[symbol] = {"mean": new_mean, "var": new_var}

            context[symbol] = {
                "symbol": symbol,
                "strike": strike,
                "mid": mid,
                "prior_vol": prior_vol,
                "live_vol": smoothed_live,
                "vol": blended_vol,
                "fair": fair,
                "delta": delta,
                "residual": residual,
                "residual_mean": mean,
                "residual_sd": sd,
                "zscore": zscore,
            }

        return context

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
        risk_reduce_only: bool,
    ) -> List[Order]:
        orders: List[Order] = []
        position_after = position
        reduce_only = risk_reduce_only or timestamp >= self.UNDERLYING_REDUCTION_START
        exit_edge = self.UNDERLYING_RISK_EXIT_EDGE if risk_reduce_only else self.HYDROGEL_TAKE_EDGE

        if not reduce_only or position_after < 0:
            position_after = self.take_asks_with_budget(
                "HYDROGEL_PACK",
                order_depth,
                fair + exit_edge if reduce_only else fair - self.HYDROGEL_TAKE_EDGE,
                self.HYDROGEL_MAX_TAKE,
                position_after,
                orders,
            )

        if not reduce_only or position_after > 0:
            position_after = self.hit_bids_with_budget(
                "HYDROGEL_PACK",
                order_depth,
                fair - exit_edge if reduce_only else fair + self.HYDROGEL_TAKE_EDGE,
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
        risk_reduce_only: bool,
    ) -> List[Order]:
        orders: List[Order] = []
        position_after = position
        reduce_only = risk_reduce_only or timestamp >= self.UNDERLYING_REDUCTION_START
        exit_edge = self.UNDERLYING_RISK_EXIT_EDGE if risk_reduce_only else self.VELVET_TAKE_EDGE

        if not reduce_only or position_after < target_position:
            position_after = self.take_asks_with_budget(
                "VELVETFRUIT_EXTRACT",
                order_depth,
                fair + exit_edge if reduce_only else fair - self.VELVET_TAKE_EDGE,
                self.VELVET_MAX_TAKE,
                position_after,
                orders,
            )

        if not reduce_only or position_after > target_position:
            position_after = self.hit_bids_with_budget(
                "VELVETFRUIT_EXTRACT",
                order_depth,
                fair - exit_edge if reduce_only else fair + self.VELVET_TAKE_EDGE,
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

    def trade_options(
        self,
        state: TradingState,
        spot: float,
        tte: float,
        risk_state: Dict[str, float],
        option_context: Dict[str, Dict],
    ) -> Dict[str, List[Order]]:
        shadow_positions = {
            symbol: int(state.position.get(symbol, 0))
            for symbol in self.OPTION_STRIKES
        }
        shadow_delta = self.portfolio_delta_from_context(shadow_positions, option_context, spot, tte)
        reduce_only = risk_state["reduce_options"] or state.timestamp >= self.OPTION_REDUCTION_START
        orders_by_product: Dict[str, List[Order]] = {symbol: [] for symbol in self.OPTION_STRIKES}

        if reduce_only:
            for symbol in self.OPTION_PRIORITY:
                order_depth = state.order_depths.get(symbol)
                if order_depth is None:
                    continue
                strike = self.OPTION_STRIKES[symbol]
                fair = option_context.get(symbol, {}).get("fair", self.option_fair_value(spot, strike, tte))
                delta = option_context.get(symbol, {}).get("delta", self.bs_delta(spot, strike, tte, self.option_vol(strike)))
                position = shadow_positions[symbol]
                if position < 0:
                    position, delta_change = self.take_option_asks(
                        symbol,
                        order_depth,
                        fair + self.OPTION_RISK_EXIT_EDGE,
                        self.OPTION_MAX_TAKE[symbol],
                        position,
                        min(self.position_limit(symbol) - position, abs(position)),
                        delta,
                        orders_by_product[symbol],
                    )
                    shadow_delta += delta_change
                elif position > 0:
                    position, delta_change = self.hit_option_bids(
                        symbol,
                        order_depth,
                        fair - self.OPTION_RISK_EXIT_EDGE,
                        self.OPTION_MAX_TAKE[symbol],
                        position,
                        min(self.position_limit(symbol) + position, abs(position)),
                        delta,
                        orders_by_product[symbol],
                    )
                    shadow_delta += delta_change
                shadow_positions[symbol] = position
            return orders_by_product

        paired_symbols = set()
        core_pairs = list(zip(self.CORE_OPTION_SYMBOLS, self.CORE_OPTION_SYMBOLS[1:]))
        for left_symbol, right_symbol in core_pairs:
            if left_symbol not in option_context or right_symbol not in option_context:
                continue
            left_ctx = option_context[left_symbol]
            right_ctx = option_context[right_symbol]
            left_entry = self.option_entry_threshold(left_symbol)
            right_entry = self.option_entry_threshold(right_symbol)

            if (
                left_ctx["zscore"] <= -left_entry
                and right_ctx["zscore"] >= right_entry
                and right_ctx["zscore"] - left_ctx["zscore"] >= self.OPTION_PAIR_GAP_Z
            ):
                traded = self.execute_option_pair_trade(
                    long_symbol=left_symbol,
                    short_symbol=right_symbol,
                    state=state,
                    option_context=option_context,
                    shadow_positions=shadow_positions,
                    shadow_delta=shadow_delta,
                    orders_by_product=orders_by_product,
                )
                if traded:
                    paired_symbols.update((left_symbol, right_symbol))
                    shadow_delta = self.portfolio_delta_from_context(shadow_positions, option_context, spot, tte)
            elif (
                right_ctx["zscore"] <= -right_entry
                and left_ctx["zscore"] >= left_entry
                and left_ctx["zscore"] - right_ctx["zscore"] >= self.OPTION_PAIR_GAP_Z
            ):
                traded = self.execute_option_pair_trade(
                    long_symbol=right_symbol,
                    short_symbol=left_symbol,
                    state=state,
                    option_context=option_context,
                    shadow_positions=shadow_positions,
                    shadow_delta=shadow_delta,
                    orders_by_product=orders_by_product,
                )
                if traded:
                    paired_symbols.update((left_symbol, right_symbol))
                    shadow_delta = self.portfolio_delta_from_context(shadow_positions, option_context, spot, tte)

        single_priority = ["VEV_5000"] + [symbol for symbol in self.CORE_OPTION_SYMBOLS if symbol != "VEV_5000"]
        for symbol in single_priority:
            if symbol not in option_context:
                continue
            if symbol != "VEV_5000" and symbol in paired_symbols:
                continue

            context = option_context[symbol]
            entry_z = self.option_entry_threshold(symbol)
            if symbol != "VEV_5000":
                entry_z += 0.35

            position = shadow_positions[symbol]
            order_depth = state.order_depths.get(symbol)
            if order_depth is None:
                continue

            if context["zscore"] <= -entry_z:
                buy_capacity = self.option_buy_capacity(symbol, shadow_positions, option_context, shadow_delta)
                clip = min(self.OPTION_STRONG_SINGLE_CLIP if symbol == "VEV_5000" else self.OPTION_MAX_TAKE[symbol], buy_capacity)
                if clip > 0:
                    position, _ = self.take_option_asks(
                        symbol,
                        order_depth,
                        context["fair"] - self.OPTION_TAKE_EDGES[symbol],
                        clip,
                        position,
                        clip,
                        context["delta"],
                        orders_by_product[symbol],
                    )
                    shadow_positions[symbol] = position
                    shadow_delta = self.portfolio_delta_from_context(shadow_positions, option_context, spot, tte)
            elif context["zscore"] >= entry_z:
                sell_capacity = self.option_sell_capacity(symbol, shadow_positions, option_context, shadow_delta)
                clip = min(self.OPTION_STRONG_SINGLE_CLIP if symbol == "VEV_5000" else self.OPTION_MAX_TAKE[symbol], sell_capacity)
                if clip > 0:
                    position, _ = self.hit_option_bids(
                        symbol,
                        order_depth,
                        context["fair"] + self.OPTION_TAKE_EDGES[symbol],
                        clip,
                        position,
                        clip,
                        context["delta"],
                        orders_by_product[symbol],
                    )
                    shadow_positions[symbol] = position
                    shadow_delta = self.portfolio_delta_from_context(shadow_positions, option_context, spot, tte)

        for symbol in self.CORE_OPTION_SYMBOLS:
            if symbol not in option_context or symbol not in state.order_depths:
                continue
            self.place_residual_option_quote(
                symbol,
                state.order_depths[symbol],
                option_context[symbol],
                shadow_positions,
                shadow_delta,
                orders_by_product[symbol],
            )

        return orders_by_product

    def option_fair_value(self, spot: float, strike: int, tte: float, sigma: Optional[float] = None) -> float:
        return self.bs_call(spot, strike, tte, self.option_vol(strike) if sigma is None else sigma)

    def option_vol(self, strike: int) -> float:
        return self.OPTION_VOLS[strike]

    def portfolio_delta_from_context(
        self,
        positions: Dict[str, int],
        option_context: Dict[str, Dict],
        spot: float,
        tte: float,
    ) -> float:
        total = 0.0
        for symbol in self.OPTION_PRIORITY:
            strike = self.OPTION_STRIKES[symbol]
            delta = option_context.get(symbol, {}).get("delta")
            if delta is None:
                delta = self.bs_delta(spot, strike, tte, self.option_vol(strike))
            total += positions.get(symbol, 0) * delta
        return total

    def portfolio_delta(self, positions: Dict[str, int], spot: float, tte: float) -> float:
        total = 0.0
        for symbol, strike in self.OPTION_STRIKES.items():
            total += positions.get(symbol, 0) * self.bs_delta(spot, strike, tte, self.option_vol(strike))
        return total

    def option_entry_threshold(self, symbol: str) -> float:
        if symbol == "VEV_5000":
            return self.OPTION_V5000_ENTRY_Z
        return self.OPTION_RESIDUAL_ENTRY_Z

    def core_side_position(self, positions: Dict[str, int], side: int) -> int:
        total = 0
        for symbol in self.CORE_OPTION_SYMBOLS:
            position = int(positions.get(symbol, 0))
            if side > 0 and position > 0:
                total += position
            elif side < 0 and position < 0:
                total += -position
        return total

    def option_buy_capacity(
        self,
        symbol: str,
        positions: Dict[str, int],
        option_context: Dict[str, Dict],
        shadow_delta: float,
    ) -> int:
        position = int(positions.get(symbol, 0))
        buy_capacity = self.position_limit(symbol) - position
        if symbol in self.CORE_OPTION_SYMBOLS:
            buy_capacity = min(
                buy_capacity,
                max(0, self.OPTION_SAME_SIDE_CORE_LIMIT - self.core_side_position(positions, 1)),
            )
        delta = option_context.get(symbol, {}).get("delta", 0.0)
        if delta > 0:
            buy_capacity = min(
                buy_capacity,
                int(max(0.0, (self.OPTION_DELTA_BUDGET - shadow_delta) / max(delta, 1e-6))),
            )
        return max(0, buy_capacity)

    def option_sell_capacity(
        self,
        symbol: str,
        positions: Dict[str, int],
        option_context: Dict[str, Dict],
        shadow_delta: float,
    ) -> int:
        position = int(positions.get(symbol, 0))
        sell_capacity = self.position_limit(symbol) + position
        if symbol in self.CORE_OPTION_SYMBOLS:
            sell_capacity = min(
                sell_capacity,
                max(0, self.OPTION_SAME_SIDE_CORE_LIMIT - self.core_side_position(positions, -1)),
            )
        delta = option_context.get(symbol, {}).get("delta", 0.0)
        if delta > 0:
            sell_capacity = min(
                sell_capacity,
                int(max(0.0, (self.OPTION_DELTA_BUDGET + shadow_delta) / max(delta, 1e-6))),
            )
        return max(0, sell_capacity)

    def execute_option_pair_trade(
        self,
        long_symbol: str,
        short_symbol: str,
        state: TradingState,
        option_context: Dict[str, Dict],
        shadow_positions: Dict[str, int],
        shadow_delta: float,
        orders_by_product: Dict[str, List[Order]],
    ) -> bool:
        long_depth = state.order_depths.get(long_symbol)
        short_depth = state.order_depths.get(short_symbol)
        if long_depth is None or short_depth is None:
            return False

        long_ctx = option_context[long_symbol]
        short_ctx = option_context[short_symbol]
        long_capacity = self.option_buy_capacity(long_symbol, shadow_positions, option_context, shadow_delta)
        short_capacity = self.option_sell_capacity(short_symbol, shadow_positions, option_context, shadow_delta)
        if long_capacity <= 0 or short_capacity <= 0:
            return False

        long_delta = max(long_ctx["delta"], 0.05)
        short_delta = max(short_ctx["delta"], 0.05)
        long_quantity = min(
            self.OPTION_PAIR_MAX_LEG_CLIP,
            self.OPTION_MAX_TAKE[long_symbol],
            long_capacity,
            max(1, int(round(self.OPTION_PAIR_TARGET_DELTA / long_delta))),
        )
        short_quantity = min(
            self.OPTION_PAIR_MAX_LEG_CLIP,
            self.OPTION_MAX_TAKE[short_symbol],
            short_capacity,
            max(1, int(round(self.OPTION_PAIR_TARGET_DELTA / short_delta))),
        )
        if long_quantity <= 0 or short_quantity <= 0:
            return False

        original_long = shadow_positions[long_symbol]
        original_short = shadow_positions[short_symbol]

        new_long, _ = self.take_option_asks(
            long_symbol,
            long_depth,
            long_ctx["fair"] - 0.5 * self.OPTION_TAKE_EDGES[long_symbol],
            long_quantity,
            original_long,
            long_quantity,
            long_ctx["delta"],
            orders_by_product[long_symbol],
        )
        new_short, _ = self.hit_option_bids(
            short_symbol,
            short_depth,
            short_ctx["fair"] + 0.5 * self.OPTION_TAKE_EDGES[short_symbol],
            short_quantity,
            original_short,
            short_quantity,
            short_ctx["delta"],
            orders_by_product[short_symbol],
        )

        shadow_positions[long_symbol] = new_long
        shadow_positions[short_symbol] = new_short
        return new_long != original_long or new_short != original_short

    def place_residual_option_quote(
        self,
        symbol: str,
        order_depth: OrderDepth,
        context: Dict,
        shadow_positions: Dict[str, int],
        shadow_delta: float,
        orders: List[Order],
    ) -> None:
        if abs(context["zscore"]) < self.OPTION_RESIDUAL_QUOTE_Z:
            return
        if abs(shadow_delta) > self.OPTION_DELTA_SOFT_LIMIT:
            return

        position = int(shadow_positions.get(symbol, 0))
        best_bid, best_ask = self.best_bid_ask(order_depth)
        make_edge = self.OPTION_MAKE_EDGES[symbol]

        if context["zscore"] < 0:
            buy_capacity = min(
                self.OPTION_PASSIVE_CLIP,
                self.option_buy_capacity(symbol, shadow_positions, {symbol: context}, shadow_delta),
            )
            if buy_capacity <= 0:
                return
            bid_price = math.floor(context["fair"] - make_edge)
            if best_bid is not None:
                bid_price = min(bid_price, best_bid + 1)
            if best_ask is None or bid_price < best_ask:
                orders.append(Order(symbol, int(bid_price), buy_capacity))
        else:
            sell_capacity = min(
                self.OPTION_PASSIVE_CLIP,
                self.option_sell_capacity(symbol, shadow_positions, {symbol: context}, shadow_delta),
            )
            if sell_capacity <= 0:
                return
            ask_price = math.ceil(context["fair"] + make_edge)
            if best_ask is not None:
                ask_price = max(ask_price, best_ask - 1)
            if best_bid is None or ask_price > best_bid:
                orders.append(Order(symbol, int(ask_price), -sell_capacity))

    def option_gross_position(self, positions: Dict[str, int]) -> int:
        total = 0
        for symbol in self.OPTION_STRIKES:
            total += abs(int(positions.get(symbol, 0)))
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

    def midpoint(self, order_depth: OrderDepth) -> Optional[float]:
        best_bid, best_ask = self.best_bid_ask(order_depth)
        if best_bid is None or best_ask is None:
            return None
        return (best_bid + best_ask) / 2.0

    def implied_volatility(
        self,
        option_price: float,
        spot: float,
        strike: int,
        tte: float,
        fallback_sigma: float,
    ) -> Optional[float]:
        intrinsic = max(0.0, spot - strike)
        extrinsic = option_price - intrinsic
        if extrinsic <= 0.75:
            return None

        lower = 1e-6
        upper = max(0.08, fallback_sigma * 4.0)
        for _ in range(60):
            middle = 0.5 * (lower + upper)
            fair = self.bs_call(spot, strike, tte, middle)
            if fair > option_price:
                upper = middle
            else:
                lower = middle
        sigma = 0.5 * (lower + upper)
        if sigma <= 0 or sigma > 0.10:
            return None
        return sigma

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
