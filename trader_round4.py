from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json
import math


HYDROGEL = "HYDROGEL_PACK"
VEV = "VELVETFRUIT_EXTRACT"

VOUCHERS = {
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

LIMIT = {
    HYDROGEL: 200,
    VEV: 200,
    **{p: 300 for p in VOUCHERS},
}

# Auto-generated from the Round 4 data files by walk-forward/spread-aware filtering.
# This is NOT manually selected Mark 14/67/etc. It is model output.
# Each entry gives the desired directional signal when the mark appears as buyer/seller:
#   +weight -> go longer
#   -weight -> go shorter
MARK_WEIGHTS = {'VELVETFRUIT_EXTRACT': {'Mark 67': {'buyer': {'w': 0.3746580258042526, 'h': 10, 'avg': 1.4390728476821193, 'qty': 1510}}, 'Mark 49': {'seller': {'w': 0.39527102898507305, 'h': 10, 'avg': 1.5392156862745099, 'qty': 1071}, 'buyer': {'w': -0.5136725368174593, 'h': 200, 'avg': 6.130841121495327, 'qty': 107}}, 'Mark 22': {'seller': {'w': 0.15, 'h': 50, 'avg': 0.8550932568149211, 'qty': 697}}}, 'VEV_5200': {'Mark 22': {'seller': {'w': 0.15, 'h': 200, 'avg': 0.42207792207792205, 'qty': 154}}}}


class Trader:
    """
    Round 4 v14: BSM/binomial base + optimized Mark overlay.

    This version can use marks, but avoids manual insider-picking:
    - Mark names are scanned from the data file offline.
    - Signals are kept only if they pass spread-aware edge, sample-size, and day-consistency filters.
    - In live trading, mark flow is only an overlay; option pricing remains the main source of truth.
    """

    def __init__(self):
        self.TICKS_PER_DAY = 100000.0
        self.DAYS_PER_YEAR = 365.0
        self.DAYS_TO_EXPIRY_AT_OPEN = 5.0
        self.MIN_DAYS_LEFT = 0.25
        self.BINOMIAL_STEPS_PER_DAY = 4
        self.MAX_BINOMIAL_STEPS = 80

        self.SIGMA_PRIOR = 0.14
        self.VAR_ALPHA = 0.02
        self.MIN_SIGMA = 0.06
        self.MAX_SIGMA = 0.60

        self.EDGE_FLOOR = 2.0
        self.VEV_TREND_FILTER = 1.0
        self.MARK_FAIR_SHIFT = 2.0
        self.MARK_TARGET_SHIFT = 0.30

        self.HYDROGEL_MAX = 35
        self.VEV_BASE_MAX = 120

    def run(self, state: TradingState):
        try:
            cache = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            cache = {}
        self.ensure_cache(cache)

        result: Dict[str, List[Order]] = {}
        mids = {}

        for product, depth in state.order_depths.items():
            mid = self.mid(depth)
            if mid is not None:
                mids[product] = mid

        if VEV in mids:
            self.update_vev_state(cache, state.timestamp, mids[VEV])

        sigma = self.current_sigma(cache)
        vev_trend = self.vev_trend(cache, mids.get(VEV))

        for product, depth in state.order_depths.items():
            position = state.position.get(product, 0)

            if product in VOUCHERS and VEV in mids:
                result[product] = self.trade_voucher(
                    product=product,
                    strike=VOUCHERS[product],
                    depth=depth,
                    position=position,
                    S=mids[VEV],
                    timestamp=state.timestamp,
                    sigma=sigma,
                    vev_trend=vev_trend,
                    mark_signal=self.mark_signal(product, state),
                )

            elif product == VEV:
                result[product] = self.trade_vev(
                    depth=depth,
                    position=position,
                    vev_trend=vev_trend,
                    mark_signal=self.mark_signal(product, state),
                )

            elif product == HYDROGEL:
                result[product] = self.trade_hydrogel(
                    depth=depth,
                    position=position,
                    mark_signal=self.mark_signal(product, state),
                )

            else:
                result[product] = []

        return result, 0, json.dumps(cache, separators=(",", ":"))

    # ------------------------------------------------------------------
    # Mark overlay
    # ------------------------------------------------------------------

    def mark_signal(self, product: str, state: TradingState):
        weights = MARK_WEIGHTS.get(product)
        if not weights:
            return 0.0

        score = 0.0
        total_w = 0.0

        for tr in state.market_trades.get(product, []):
            qty = abs(getattr(tr, "quantity", 0) or 0)
            if qty <= 0:
                continue
            q = math.sqrt(qty)

            buyer = getattr(tr, "buyer", None)
            seller = getattr(tr, "seller", None)

            if buyer in weights and "buyer" in weights[buyer]:
                score += q * weights[buyer]["buyer"]["w"]
                total_w += q

            if seller in weights and "seller" in weights[seller]:
                score += q * weights[seller]["seller"]["w"]
                total_w += q

        if total_w <= 0:
            return 0.0

        return self.clip(score / total_w, -3.0, 3.0)

    # ------------------------------------------------------------------
    # State and time grid
    # ------------------------------------------------------------------

    def ensure_cache(self, cache):
        cache.setdefault("vev_var", self.SIGMA_PRIOR * self.SIGMA_PRIOR)
        cache.setdefault("vev_fast", None)
        cache.setdefault("vev_slow", None)
        cache.setdefault("vev_prev_mid", None)
        cache.setdefault("vev_prev_ts", None)

    def update_vev_state(self, cache, timestamp: int, mid: float):
        prev_mid = cache.get("vev_prev_mid")
        prev_ts = cache.get("vev_prev_ts")
        var = cache.get("vev_var", self.SIGMA_PRIOR * self.SIGMA_PRIOR)

        if prev_mid is not None and prev_ts is not None and timestamp > prev_ts:
            dt_year = max((timestamp - prev_ts) / self.TICKS_PER_DAY / self.DAYS_PER_YEAR, 1e-9)
            lr = math.log(max(mid, 1e-9) / max(prev_mid, 1e-9))
            obs_var = (lr * lr) / dt_year
            var = (1.0 - self.VAR_ALPHA) * var + self.VAR_ALPHA * obs_var
            cache["vev_var"] = self.clip(var, self.MIN_SIGMA * self.MIN_SIGMA, self.MAX_SIGMA * self.MAX_SIGMA)

        if cache.get("vev_fast") is None:
            cache["vev_fast"] = mid
            cache["vev_slow"] = mid
        else:
            cache["vev_fast"] = 0.10 * mid + 0.90 * cache["vev_fast"]
            cache["vev_slow"] = 0.02 * mid + 0.98 * cache["vev_slow"]

        cache["vev_prev_mid"] = mid
        cache["vev_prev_ts"] = timestamp

    def current_sigma(self, cache):
        return self.clip(math.sqrt(cache.get("vev_var", self.SIGMA_PRIOR * self.SIGMA_PRIOR)), self.MIN_SIGMA, self.MAX_SIGMA)

    def vev_trend(self, cache, mid):
        if mid is None or cache.get("vev_fast") is None or cache.get("vev_slow") is None:
            return 0.0
        sigma = self.current_sigma(cache)
        daily_sigma_points = max(1.0, sigma * mid * math.sqrt(1.0 / self.DAYS_PER_YEAR))
        return self.clip((cache["vev_fast"] - cache["vev_slow"]) / daily_sigma_points, -6.0, 6.0)

    def time_to_expiry(self, timestamp: int):
        days_left = max(self.MIN_DAYS_LEFT, self.DAYS_TO_EXPIRY_AT_OPEN - timestamp / self.TICKS_PER_DAY)
        return days_left / self.DAYS_PER_YEAR, days_left

    def binomial_steps(self, days_left: float):
        return max(1, min(self.MAX_BINOMIAL_STEPS, int(math.ceil(days_left * self.BINOMIAL_STEPS_PER_DAY))))

    # ------------------------------------------------------------------
    # Trading logic
    # ------------------------------------------------------------------

    def trade_voucher(self, product: str, strike: int, depth: OrderDepth, position: int,
                      S: float, timestamp: int, sigma: float, vev_trend: float, mark_signal: float):
        orders: List[Order] = []
        if not depth.buy_orders or not depth.sell_orders:
            return orders

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        spread = best_ask - best_bid

        T, days_left = self.time_to_expiry(timestamp)
        steps = self.binomial_steps(days_left)

        bsm = self.bs_call(S, strike, T, sigma)
        bino = self.binomial_call(S, strike, T, sigma, steps)

        # Mark shifts fair value modestly; it cannot override the model alone.
        fair = 0.50 * bsm + 0.50 * bino + self.MARK_FAIR_SHIFT * mark_signal
        model_gap = abs(bsm - bino)

        edge = max(self.EDGE_FLOOR, 0.25 * spread, 0.02 * max(abs(fair), 1.0), model_gap)

        sell_edge = best_bid - fair
        buy_edge = fair - best_ask

        width = max(20.0, 2.0 * sigma * S * math.sqrt(max(T, 1e-9)))
        activity = 1.0 / (1.0 + abs(S - strike) / width)
        max_abs = int(LIMIT[product] * self.clip(activity, 0.15, 1.0))

        # Mark can tilt size a little, but direction still requires price edge.
        target = position

        if sell_edge > edge and vev_trend < self.VEV_TREND_FILTER:
            target = -max_abs
        elif buy_edge > edge and vev_trend > -self.VEV_TREND_FILTER:
            target = max_abs
        else:
            tilt = int(max_abs * self.MARK_TARGET_SHIFT * math.tanh(mark_signal))
            if abs(tilt) > abs(position) + 8:
                target = tilt

        if abs(target - position) < max(4, int(0.03 * LIMIT[product])):
            return orders

        return self.cross_to_target(product, depth, position, target, LIMIT[product])

    def trade_vev(self, depth: OrderDepth, position: int, vev_trend: float, mark_signal: float):
        signal = vev_trend + 0.75 * mark_signal

        if signal > 1.5:
            target = self.VEV_BASE_MAX
        elif signal < -1.5:
            target = -self.VEV_BASE_MAX
        else:
            target = position

        if abs(target - position) < 8:
            return []

        return self.cross_to_target(VEV, depth, position, target, LIMIT[VEV])

    def trade_hydrogel(self, depth: OrderDepth, position: int, mark_signal: float):
        # Hydrogel only uses robust optimized mark signals if any exist. No Mark14 mean reversion.
        if mark_signal > 1.0:
            target = self.HYDROGEL_MAX
        elif mark_signal < -1.0:
            target = -self.HYDROGEL_MAX
        else:
            target = position

        if abs(target - position) < 6:
            return []

        return self.cross_to_target(HYDROGEL, depth, position, target, self.HYDROGEL_MAX)

    # ------------------------------------------------------------------
    # Execution and math
    # ------------------------------------------------------------------

    def cross_to_target(self, product: str, depth: OrderDepth, position: int, target: int, cap: int):
        orders: List[Order] = []
        limit = LIMIT[product]
        target = max(-limit, min(limit, target))
        delta = target - position

        if delta > 0:
            qty_left = min(delta, cap, limit - position)
            for ask, vol in sorted(depth.sell_orders.items()):
                if qty_left <= 0:
                    break
                available = -vol
                if available <= 0:
                    continue
                qty = min(qty_left, available)
                if qty > 0:
                    orders.append(Order(product, ask, qty))
                    position += qty
                    qty_left -= qty

        elif delta < 0:
            qty_left = min(-delta, cap, limit + position)
            for bid, vol in sorted(depth.buy_orders.items(), reverse=True):
                if qty_left <= 0:
                    break
                available = vol
                if available <= 0:
                    continue
                qty = min(qty_left, available)
                if qty > 0:
                    orders.append(Order(product, bid, -qty))
                    position -= qty
                    qty_left -= qty

        return orders

    def norm_cdf(self, x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def bs_call(self, S, K, T, sigma):
        if T <= 0 or sigma <= 0:
            return max(S - K, 0.0)
        S = max(S, 1e-9)
        d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * self.norm_cdf(d1) - K * self.norm_cdf(d2)

    def binomial_call(self, S, K, T, sigma, n_steps):
        intrinsic = max(S - K, 0.0)
        if T <= 0 or sigma <= 0 or n_steps <= 0:
            return intrinsic

        dt = T / n_steps
        u = math.exp(sigma * math.sqrt(dt))
        d = 1.0 / u
        if abs(u - d) < 1e-12:
            return intrinsic

        p = (1.0 - d) / (u - d)
        p = self.clip(p, 0.0, 1.0)

        fair = 0.0
        for j in range(n_steps + 1):
            prob = math.comb(n_steps, j) * (p ** j) * ((1.0 - p) ** (n_steps - j))
            st = S * (u ** j) * (d ** (n_steps - j))
            fair += prob * max(st - K, 0.0)

        return fair

    def mid(self, depth: OrderDepth):
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None
        return (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0

    def clip(self, x, lo, hi):
        return max(lo, min(hi, x))
