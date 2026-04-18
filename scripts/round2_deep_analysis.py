"""Deep-dive quantitative analysis of Round 2 tick data.

Goals:
  * Characterise osmium mean-reversion speed and optimal edge thresholds.
  * Quantify lag profile of order-book imbalance against next-tick returns.
  * Compare fair-value estimators (mid, vwap micro, 5-level micro, EMA).
  * Stress-test pepper trend stability across days.
  * Produce a machine-readable summary for the diagnostic harness.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data" / "round2"
OUTPUT_PATH = ROOT / "logs" / "round2_deep_analysis.json"
PRODUCTS = ("ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT")


def load_rows() -> Dict[int, Dict[int, List[Dict]]]:
    by_day_ts: Dict[int, Dict[int, List[Dict]]] = {}
    for path in sorted(DATA_DIR.glob("prices_round_2_day_*.csv")):
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
                        parsed[f"{side}_volume_{level}"] = float(volume) if volume else None
                by_day_ts.setdefault(parsed["day"], {}).setdefault(parsed["timestamp"], []).append(parsed)
    return by_day_ts


def mid(row: Dict) -> Optional[float]:
    bid = row["bid_price_1"]
    ask = row["ask_price_1"]
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def vwap_micro(row: Dict) -> Optional[float]:
    bid = row["bid_price_1"]
    ask = row["ask_price_1"]
    bv = row["bid_volume_1"]
    av = row["ask_volume_1"]
    if bid is None or ask is None or not bv or not av:
        return mid(row)
    return (bid * av + ask * bv) / (av + bv)


def depth_micro(row: Dict, levels: int = 3) -> Optional[float]:
    """Depth-weighted fair: sum(price * volume) / sum(volume) across 'levels' on each side."""
    bid_pairs = []
    ask_pairs = []
    for level in range(1, levels + 1):
        bp = row[f"bid_price_{level}"]
        bv = row[f"bid_volume_{level}"]
        ap = row[f"ask_price_{level}"]
        av = row[f"ask_volume_{level}"]
        if bp is not None and bv:
            bid_pairs.append((bp, bv))
        if ap is not None and av:
            ask_pairs.append((ap, av))
    bid_vol = sum(v for _, v in bid_pairs)
    ask_vol = sum(v for _, v in ask_pairs)
    if bid_vol == 0 and ask_vol == 0:
        return mid(row)
    # crossed micro: each side weights the opposite.
    bid_side = sum(p * v for p, v in bid_pairs)
    ask_side = sum(p * v for p, v in ask_pairs)
    if bid_vol == 0:
        return ask_side / ask_vol
    if ask_vol == 0:
        return bid_side / bid_vol
    return (bid_side / bid_vol * ask_vol + ask_side / ask_vol * bid_vol) / (bid_vol + ask_vol)


def stoikov_micro(row: Dict) -> Optional[float]:
    """Stoikov 2018 micro-price: alpha = V_a / (V_a + V_b), mid_adjust = alpha * bid + (1-alpha)*ask."""
    bid = row["bid_price_1"]
    ask = row["ask_price_1"]
    bv = row["bid_volume_1"]
    av = row["ask_volume_1"]
    if bid is None or ask is None or not bv or not av:
        return mid(row)
    alpha = av / (bv + av)
    return alpha * bid + (1 - alpha) * ask


def book_imbalance_l1(row: Dict) -> Optional[float]:
    bv = row["bid_volume_1"]
    av = row["ask_volume_1"]
    if not bv and not av:
        return None
    total = (bv or 0) + (av or 0)
    if total == 0:
        return 0.0
    return ((bv or 0) - (av or 0)) / total


def book_imbalance_l3(row: Dict) -> Optional[float]:
    bid = sum((row[f"bid_volume_{l}"] or 0) for l in (1, 2, 3))
    ask = sum((row[f"ask_volume_{l}"] or 0) for l in (1, 2, 3))
    total = bid + ask
    if total == 0:
        return None
    return (bid - ask) / total


def autocorrelation(series: List[float], lag: int) -> float:
    n = len(series)
    if n <= lag + 1:
        return 0.0
    mean = statistics.mean(series)
    num = sum((series[i] - mean) * (series[i - lag] - mean) for i in range(lag, n))
    denom = sum((value - mean) ** 2 for value in series)
    return num / denom if denom else 0.0


def ols(x: List[float], y: List[float]) -> Tuple[float, float, float]:
    """Returns (slope, intercept, r_squared)."""
    n = len(x)
    if n < 2:
        return 0.0, 0.0, 0.0
    mean_x = statistics.mean(x)
    mean_y = statistics.mean(y)
    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    var_x = sum((v - mean_x) ** 2 for v in x)
    var_y = sum((v - mean_y) ** 2 for v in y)
    if var_x == 0:
        return 0.0, mean_y, 0.0
    slope = cov / var_x
    intercept = mean_y - slope * mean_x
    r_sq = (cov * cov) / (var_x * var_y) if var_y else 0.0
    return slope, intercept, r_sq


def ornstein_uhlenbeck_halflife(series: List[float]) -> Dict[str, float]:
    """Estimate OU half-life via lag-1 AR(1): x_{t+1} - x_t = beta * (mu - x_t) + eps.

    Half-life = log(2) / -log(1 - beta) for 0 < beta < 1.
    """
    if len(series) < 3:
        return {"beta": 0.0, "half_life_ticks": float("inf"), "mu": 0.0}
    diff = [series[i + 1] - series[i] for i in range(len(series) - 1)]
    lag = series[:-1]
    slope, intercept, r_sq = ols(lag, diff)
    beta = -slope
    if beta <= 0 or beta >= 1:
        half_life = float("inf")
    else:
        half_life = math.log(2) / -math.log(1 - beta)
    mu = -intercept / slope if slope else statistics.mean(series)
    return {"beta": beta, "half_life_ticks": half_life, "mu": mu, "ar1_r_squared": r_sq}


def imbalance_predictive_power(rows: List[Dict]) -> Dict[str, Dict]:
    """Correlation of imbalance with next-k-tick mid return for k in {1, 2, 3, 5}."""
    mids = []
    imb_l1 = []
    imb_l3 = []
    for row in rows:
        m = mid(row)
        i1 = book_imbalance_l1(row)
        i3 = book_imbalance_l3(row)
        if m is None or i1 is None:
            continue
        mids.append(m)
        imb_l1.append(i1)
        imb_l3.append(i3 if i3 is not None else 0.0)
    out = {}
    for name, imb in (("l1", imb_l1), ("l3", imb_l3)):
        per_lag = {}
        for k in (1, 2, 3, 5, 10):
            if len(mids) <= k + 1:
                continue
            x = imb[:-k]
            y = [mids[i + k] - mids[i] for i in range(len(mids) - k)]
            slope, intercept, r_sq = ols(x, y)
            direction = sum(1 for a, b in zip(x, y) if (a > 0) == (b > 0) and b != 0)
            nonzero = sum(1 for b in y if b != 0)
            per_lag[f"lag_{k}"] = {
                "beta": slope,
                "intercept": intercept,
                "r_squared": r_sq,
                "directional_accuracy": direction / nonzero if nonzero else 0.0,
            }
        out[name] = per_lag
    return out


def fair_value_fit_compare(rows: List[Dict]) -> Dict[str, float]:
    """Compare estimators on 1-tick-ahead mid MSE."""
    n = len(rows)
    mids = [mid(r) for r in rows]
    estimators = {
        "mid": mids,
        "vwap_micro": [vwap_micro(r) for r in rows],
        "stoikov_micro": [stoikov_micro(r) for r in rows],
        "depth_micro_l3": [depth_micro(r, 3) for r in rows],
    }
    results = {}
    for name, est in estimators.items():
        err_sq = []
        for i in range(n - 1):
            if est[i] is None or mids[i + 1] is None:
                continue
            err_sq.append((mids[i + 1] - est[i]) ** 2)
        results[name] = statistics.mean(err_sq) if err_sq else float("nan")
    return results


def analyze_product(day_rows: Dict[int, List[Dict]], product: str) -> Dict:
    flat: List[Dict] = []
    for ts in sorted(day_rows):
        for row in day_rows[ts]:
            if row["product"] == product:
                m = mid(row)
                if m and m > 0:
                    flat.append(row)
    mids = [mid(r) for r in flat]
    spread = [r["ask_price_1"] - r["bid_price_1"] for r in flat if r["ask_price_1"] and r["bid_price_1"]]
    returns = [mids[i + 1] - mids[i] for i in range(len(mids) - 1)]
    ou = ornstein_uhlenbeck_halflife(mids)
    imb = imbalance_predictive_power(flat)
    fits = fair_value_fit_compare(flat)
    return {
        "rows": len(flat),
        "first_mid": mids[0] if mids else None,
        "last_mid": mids[-1] if mids else None,
        "mean_mid": statistics.mean(mids) if mids else None,
        "stdev_mid": statistics.pstdev(mids) if mids else None,
        "mean_spread": statistics.mean(spread) if spread else None,
        "return_stdev": statistics.pstdev(returns) if returns else None,
        "return_ac1": autocorrelation(returns, 1),
        "return_ac2": autocorrelation(returns, 2),
        "return_ac3": autocorrelation(returns, 3),
        "ou": ou,
        "imbalance_predictive": imb,
        "fair_value_mse_1tick": fits,
    }


def edge_curve(rows: List[Dict], anchor: float) -> Dict[str, Dict]:
    """For each candidate edge, simulate a myopic 'buy if mid<=anchor-e, sell if mid>=anchor+e'
    one-shot policy ignoring inventory limits to see the forward-looking expected payoff.
    """
    mids = [mid(r) for r in rows]
    n = len(mids)
    out = {}
    for edge in (0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 15):
        buys = 0
        buy_pnl = 0.0
        sells = 0
        sell_pnl = 0.0
        # Use terminal mid as mark (last valid)
        terminal = mids[-1]
        for m in mids:
            if m is None:
                continue
            if m <= anchor - edge:
                buys += 1
                buy_pnl += terminal - m
            elif m >= anchor + edge:
                sells += 1
                sell_pnl += m - terminal
        out[f"edge_{edge}"] = {
            "buys": buys,
            "buy_expected_payoff_per_unit": buy_pnl / buys if buys else 0.0,
            "sells": sells,
            "sell_expected_payoff_per_unit": sell_pnl / sells if sells else 0.0,
            "total_opportunities": buys + sells,
            "total_payoff_proxy": buy_pnl + sell_pnl,
        }
    return out


def pepper_drift_fit(day_rows: Dict[int, List[Dict]]) -> Dict:
    per_day = {}
    for day in sorted(day_rows):
        rows = []
        for ts in sorted(day_rows[day]):
            for row in day_rows[day][ts]:
                if row["product"] == "INTARIAN_PEPPER_ROOT":
                    m = mid(row)
                    if m and m > 0:
                        rows.append((ts, m))
        if not rows:
            continue
        x = [r[0] for r in rows]
        y = [r[1] for r in rows]
        slope, intercept, r_sq = ols(x, y)
        residuals = [y[i] - (slope * x[i] + intercept) for i in range(len(x))]
        per_day[str(day)] = {
            "slope": slope,
            "intercept": intercept,
            "r_squared": r_sq,
            "residual_stdev": statistics.pstdev(residuals),
            "first_mid": y[0],
            "last_mid": y[-1],
            "intercept_to_expected_10000": y[0] - slope * x[0],
        }
    return per_day


def main() -> None:
    by_day_ts = load_rows()
    output = {"data_dir": str(DATA_DIR), "per_day": {}, "pepper_drift": pepper_drift_fit(by_day_ts)}
    for day in sorted(by_day_ts):
        day_rows = by_day_ts[day]
        day_summary = {}
        for product in PRODUCTS:
            day_summary[product] = analyze_product(day_rows, product)
            # Edge curve for osmium only (relative to 10,000 anchor)
            if product == "ASH_COATED_OSMIUM":
                osm_rows = [row for ts in sorted(day_rows) for row in day_rows[ts] if row["product"] == product]
                day_summary[product]["edge_curve_anchor10000"] = edge_curve(osm_rows, 10000.0)
        output["per_day"][str(day)] = day_summary
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")

    # Print a terse summary.
    for day in sorted(by_day_ts):
        summary = output["per_day"][str(day)]
        osm = summary["ASH_COATED_OSMIUM"]
        pep = summary["INTARIAN_PEPPER_ROOT"]
        print(f"day {day}")
        print(
            f"  osmium: mid={osm['mean_mid']:.2f} std={osm['stdev_mid']:.2f} spread={osm['mean_spread']:.2f} "
            f"retStd={osm['return_stdev']:.2f} ac1={osm['return_ac1']:.3f} "
            f"ou_hl={osm['ou']['half_life_ticks']:.1f} ou_mu={osm['ou']['mu']:.2f}"
        )
        print(
            f"  pepper: mid={pep['mean_mid']:.2f} spread={pep['mean_spread']:.2f} "
            f"retStd={pep['return_stdev']:.2f} ac1={pep['return_ac1']:.3f}"
        )
        fv = osm["fair_value_mse_1tick"]
        print(
            f"  osmium 1tick MSE: mid={fv['mid']:.3f} vwap={fv['vwap_micro']:.3f} "
            f"stoikov={fv['stoikov_micro']:.3f} depth3={fv['depth_micro_l3']:.3f}"
        )
    pd = output["pepper_drift"]
    for day, stats_ in pd.items():
        print(
            f"pepper day {day}: slope={stats_['slope']:.6f} intercept={stats_['intercept']:.2f} "
            f"r2={stats_['r_squared']:.6f} residStd={stats_['residual_stdev']:.2f}"
        )
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
