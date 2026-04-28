from datamodel import OrderDepth, UserId, TradingState, Order
from typing import Dict, List, Any
import json
import math


class Product:
    HYDROGEL = "HYDROGEL_PACK"
    VEV = "VELVETFRUIT_EXTRACT"


VOUCHER_STRIKES = {
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

POSITION_LIMITS = {
    Product.HYDROGEL: 200,
    Product.VEV: 200,
    **{symbol: 300 for symbol in VOUCHER_STRIKES},
}


class Trader:
    """
    Round 4 model:
    - HYDROGEL_PACK: stable-value market making + Mark 14 / Mark 38 counterparty filter.
    - VELVETFRUIT_EXTRACT: adaptive fair-value market making + counterparty trend/fade overlay.
    - VEV vouchers: Black-Scholes call mispricing model, delta-aware sizing, and counterparty boost.

    Design inspiration from public Prosperity repositories:
    - simple reusable product loops
    - fair-value market making around best bid/ask
    - strict position-limit clipping
    - compact traderData state for EMAs / last timestamp
    """

    def __init__(self):
        self.position_limits = POSITION_LIMITS
        self.base_fair = {
            Product.HYDROGEL: 10000.0,
            Product.VEV: 5248.0,
        }

        # Historical round-4 calibration from the data capsule.
        self.vev_vol = 0.23
        self.hydrogel_edge_threshold = 2.0
        self.vev_edge_threshold = 2.0
        self.voucher_edge_threshold = 2.0

        # Counterparty map inferred from historical trades.
        # Positive means follow when buyer / fade when seller.
        # Negative means fade when buyer / follow when seller.
        self.mark_alpha = {
            Product.HYDROGEL: {
                "Mark 14": 1.0,   # tends to buy lower and sell higher
                "Mark 38": -1.0,  # tends to buy higher and sell lower
            },
            Product.VEV: {
                "Mark 14": 0.8,
                "Mark 01": 0.6,
                "Mark 55": -0.8,
                "Mark 22": -0.4,
                "Mark 49": -0.3,
            },
        }

    # -------------------------
    # Utility functions
    # -------------------------

    def run(self, state: TradingState):
        try:
            cache = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            cache = {}

        result: Dict[str, List[Order]] = {}

        # Update fair values using live order books.
        mids = {}
        for product, depth in state.order_depths.items():
            mid = self.mid_price(depth)
            if mid is not None:
                mids[product] = mid

        hydro_fair = self.update_ema(cache, "hydrogel_ema", mids.get(Product.HYDROGEL, self.base_fair[Product.HYDROGEL]), 0.03)
        vev_fair = self.update_ema(cache, "vev_ema", mids.get(Product.VEV, self.base_fair[Product.VEV]), 0.05)

        # Counterparty impulse from market trades visible at current timestamp.
        cp_signal = self.counterparty_signal(state)

        if Product.HYDROGEL in state.order_depths:
            result[Product.HYDROGEL] = self.trade_stable_product(
                Product.HYDROGEL,
                state,
                fair_value=0.65 * 10000.0 + 0.35 * hydro_fair + 2.5 * cp_signal.get(Product.HYDROGEL, 0.0),
                threshold=self.hydrogel_edge_threshold,
                quote_width=3,
            )

        if Product.VEV in state.order_depths:
            result[Product.VEV] = self.trade_stable_product(
                Product.VEV,
                state,
                fair_value=vev_fair + 3.0 * cp_signal.get(Product.VEV, 0.0),
                threshold=self.vev_edge_threshold,
                quote_width=3,
            )

        # Vouchers depend on VEV fair. Trade them via Black-Scholes edge.
        for symbol, strike in VOUCHER_STRIKES.items():
            if symbol in state.order_depths:
                result[symbol] = self.trade_voucher(symbol, strike, state, vev_fair, cp_signal)

        # Save state.
        cache["last_timestamp"] = state.timestamp
        traderData = json.dumps(cache, separators=(",", ":"))
        conversions = 0
        return result, conversions, traderData

    def mid_price(self, depth: OrderDepth):
        if not depth.buy_orders or not depth.sell_orders:
            return None
        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        return (best_bid + best_ask) / 2

    def best_bid_ask(self, depth: OrderDepth):
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        return best_bid, best_ask

    def update_ema(self, cache: Dict[str, Any], key: str, value: float, alpha: float):
        old = cache.get(key)
        if old is None:
            cache[key] = value
        else:
            cache[key] = alpha * value + (1 - alpha) * old
        return cache[key]

    def available_to_buy(self, product: str, state: TradingState):
        return self.position_limits[product] - state.position.get(product, 0)

    def available_to_sell(self, product: str, state: TradingState):
        return self.position_limits[product] + state.position.get(product, 0)

    def clip_buy(self, product: str, state: TradingState, qty: int):
        return max(0, min(qty, self.available_to_buy(product, state)))

    def clip_sell(self, product: str, state: TradingState, qty: int):
        return max(0, min(qty, self.available_to_sell(product, state)))

    # -------------------------
    # Counterparty alpha
    # -------------------------

    def counterparty_signal(self, state: TradingState):
        signal = {}

        # market_trades are public trades. A smart buyer is bullish, a smart seller is bearish.
        for product, trades in state.market_trades.items():
            product_signal = 0.0
            alpha_map = self.mark_alpha.get(product, {})

            for tr in trades:
                qty = getattr(tr, "quantity", 0) or 0
                buyer = getattr(tr, "buyer", None)
                seller = getattr(tr, "seller", None)

                if buyer in alpha_map:
                    product_signal += alpha_map[buyer] * math.sqrt(abs(qty))
                if seller in alpha_map:
                    product_signal -= alpha_map[seller] * math.sqrt(abs(qty))

            if product_signal != 0:
                signal[product] = max(-3.0, min(3.0, product_signal))

        return signal

    # -------------------------
    # Stable product strategy
    # -------------------------

    def trade_stable_product(self, product: str, state: TradingState, fair_value: float, threshold: float, quote_width: int):
        orders: List[Order] = []
        depth = state.order_depths[product]
        pos = state.position.get(product, 0)

        # Aggress against mispriced asks.
        for ask, volume in sorted(depth.sell_orders.items()):
            ask_volume = -volume
            edge = fair_value - ask
            if edge >= threshold:
                qty = self.clip_buy(product, state, min(ask_volume, self.target_take_size(edge, pos, product, side=1)))
                if qty > 0:
                    orders.append(Order(product, ask, qty))
                    pos += qty
                    state.position[product] = pos

        # Aggress against mispriced bids.
        for bid, volume in sorted(depth.buy_orders.items(), reverse=True):
            edge = bid - fair_value
            if edge >= threshold:
                qty = self.clip_sell(product, state, min(volume, self.target_take_size(edge, pos, product, side=-1)))
                if qty > 0:
                    orders.append(Order(product, bid, -qty))
                    pos -= qty
                    state.position[product] = pos

        # Passive quote if inventory is not too extreme.
        buy_cap = self.available_to_buy(product, state)
        sell_cap = self.available_to_sell(product, state)

        bid_px = math.floor(fair_value - quote_width)
        ask_px = math.ceil(fair_value + quote_width)

        if buy_cap > 0 and pos < 0.75 * self.position_limits[product]:
            orders.append(Order(product, bid_px, min(20, buy_cap)))
        if sell_cap > 0 and pos > -0.75 * self.position_limits[product]:
            orders.append(Order(product, ask_px, -min(20, sell_cap)))

        return orders

    def target_take_size(self, edge: float, pos: int, product: str, side: int):
        limit = self.position_limits[product]
        # More size for larger edge, less if already crowded in that direction.
        base = 8 + int(4 * max(0, edge))
        if side > 0:
            inventory_penalty = max(0.25, 1 - max(0, pos) / limit)
        else:
            inventory_penalty = max(0.25, 1 - max(0, -pos) / limit)
        return max(1, int(base * inventory_penalty))

    # -------------------------
    # Voucher strategy
    # -------------------------

    def trade_voucher(self, symbol: str, strike: int, state: TradingState, vev_fair: float, cp_signal: Dict[str, float]):
        orders: List[Order] = []
        depth = state.order_depths[symbol]
        pos = state.position.get(symbol, 0)

        # Round 4 vouchers start with TTE=7 days. State timestamp resets each day in Prosperity,
        # so use a conservative floor rather than assuming exact hidden day in live.
        # This is intentionally robust rather than overfit.
        T = 7 / 365
        sigma = self.local_vol_for_strike(strike)

        theo = self.bs_call(vev_fair, strike, T, sigma)
        delta = self.bs_delta(vev_fair, strike, T, sigma)

        # Counterparty overlay: Mark 01 buying vouchers historically looked informed; Mark 22 selling
        # high-strike vouchers looked structurally one-sided. Treat that as a small bullish voucher impulse.
        voucher_signal = 0.0
        for tr in state.market_trades.get(symbol, []):
            buyer = getattr(tr, "buyer", None)
            seller = getattr(tr, "seller", None)
            qty = getattr(tr, "quantity", 0) or 0
            if buyer == "Mark 01":
                voucher_signal += 0.5 * math.sqrt(abs(qty))
            if seller == "Mark 01":
                voucher_signal -= 0.5 * math.sqrt(abs(qty))
            if buyer == "Mark 22":
                voucher_signal -= 0.3 * math.sqrt(abs(qty))
            if seller == "Mark 22":
                voucher_signal += 0.3 * math.sqrt(abs(qty))

        theo += max(-2.0, min(2.0, voucher_signal))

        # Less aggressive on very deep ITM/OTM vouchers because penny/fair-value errors can dominate.
        threshold = self.voucher_edge_threshold
        if strike <= 4500 or strike >= 6000:
            threshold += 1.0

        for ask, volume in sorted(depth.sell_orders.items()):
            ask_volume = -volume
            edge = theo - ask
            if edge >= threshold:
                qty = self.clip_buy(symbol, state, min(ask_volume, self.voucher_size(edge, delta, pos, symbol, side=1)))
                if qty > 0:
                    orders.append(Order(symbol, ask, qty))
                    pos += qty
                    state.position[symbol] = pos

        for bid, volume in sorted(depth.buy_orders.items(), reverse=True):
            edge = bid - theo
            if edge >= threshold:
                qty = self.clip_sell(symbol, state, min(volume, self.voucher_size(edge, delta, pos, symbol, side=-1)))
                if qty > 0:
                    orders.append(Order(symbol, bid, -qty))
                    pos -= qty
                    state.position[symbol] = pos

        # Passive quote around theoretical value, but only if spread gives room.
        buy_cap = self.available_to_buy(symbol, state)
        sell_cap = self.available_to_sell(symbol, state)
        bid_px = max(0, math.floor(theo - threshold))
        ask_px = max(1, math.ceil(theo + threshold))

        if buy_cap > 0 and pos < 0.65 * self.position_limits[symbol]:
            orders.append(Order(symbol, bid_px, min(20, buy_cap)))
        if sell_cap > 0 and pos > -0.65 * self.position_limits[symbol]:
            orders.append(Order(symbol, ask_px, -min(20, sell_cap)))

        return orders

    def voucher_size(self, edge: float, delta: float, pos: int, symbol: str, side: int):
        limit = self.position_limits[symbol]
        base = 6 + int(3 * max(0, edge))
        # OTM low-delta options can be sized larger per delta unit, but cap hard.
        delta_adjust = 1.2 if delta < 0.25 else 1.0
        if side > 0:
            inv_penalty = max(0.25, 1 - max(0, pos) / limit)
        else:
            inv_penalty = max(0.25, 1 - max(0, -pos) / limit)
        return max(1, min(40, int(base * delta_adjust * inv_penalty)))

    def local_vol_for_strike(self, strike: int):
        # Historical capsule shows ATM/mid strikes around 22%-24% IV.
        if strike in (5000, 5100, 5200, 5300, 5400, 5500):
            return 0.23
        if strike < 5000:
            return 0.20
        return 0.26

    def normal_cdf(self, x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def bs_call(self, S, K, T, sigma):
        if T <= 0:
            return max(S - K, 0.0)
        if sigma <= 0:
            return max(S - K, 0.0)
        d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * self.normal_cdf(d1) - K * self.normal_cdf(d2)

    def bs_delta(self, S, K, T, sigma):
        if T <= 0:
            return 1.0 if S > K else 0.0
        d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
        return self.normal_cdf(d1)
