#!/usr/bin/env python3
"""
Monte Carlo simulation of monthly profit from a HIGH_WR_SCALP backtest CSV.

Uses the bot's actual sizing model (auto_trades.py):
    margin_cost   = equity * RISK_PERCENT (default 1%)
    position_value = margin_cost * leverage (default 25x) * volatility_multiplier
    notional       = max(position_value, MIN_POSITION), capped at equity

Per-trade returns are sampled with replacement from the real backtest trades
(pnl_pct is the % return on the traded NOTIONAL). Trade counts per day are
sampled with replacement from the actual daily counts observed in the CSV.

Run:
    venv/bin/python scripts/simulate_monthly_profit.py --csv /tmp/bt_loose.csv \
        --capital 20 100 200 --runs 10000
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import csv
import random
from collections import Counter
from datetime import datetime


def load_trades(path: str):
    pnls = []
    daily = Counter()
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                pnls.append(float(row["pnl_pct"]) / 100.0)
            except Exception:
                continue
            try:
                day = str(row.get("signal_time", ""))[:10]
                if day:
                    daily[day] += 1
            except Exception:
                pass
    return pnls, daily


def simulate_month(
    pnls,
    daily_counts,
    capital,
    risk_percent,
    leverage,
    min_position,
    edge_scale,
    days,
    rng,
):
    notional_frac = risk_percent * leverage  # e.g. 0.01 * 25 = 0.25
    daily_keys = list(daily_counts) if daily_counts else None
    fallback_rate = (len(pnls) / max(days, 1)) if daily_keys is None else None

    equity = capital
    peak = capital
    max_dd = 0.0

    for _ in range(days):
        if daily_keys:
            key = daily_keys[rng.randrange(len(daily_keys))]
            n = daily_counts[key]
        else:
            n = rng.poisson(fallback_rate)
        for _ in range(n):
            if not pnls:
                break
            r = pnls[rng.randrange(len(pnls))] * edge_scale
            notional = max(capital * notional_frac, min_position)
            notional = min(notional, equity)
            equity += r * notional
            peak = max(peak, equity)
            dd = equity / peak - 1.0
            if dd < max_dd:
                max_dd = dd
    return equity - capital, max_dd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="backtest trade CSV (TradeResult format)")
    parser.add_argument("--capital", type=float, nargs="+", default=[20, 100, 200])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--runs", type=int, default=10000)
    parser.add_argument("--risk-percent", type=float, default=0.01, help="bot RISK_PERCENT")
    parser.add_argument("--leverage", type=float, default=25.0, help="bot TARGET_LEVERAGE")
    parser.add_argument("--min-position", type=float, default=6.0, help="CEX min position value (USD)")
    parser.add_argument("--edge-scale", type=float, default=1.0, help="scale the per-trade edge (sensitivity)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    pnls, daily_counts = load_trades(args.csv)
    if not pnls:
        print("No trades in CSV; nothing to simulate.")
        return 1

    rng = random.Random(args.seed)
    n_days = len(daily_counts) or args.days
    print(f"trades in CSV : {len(pnls)}")
    print(f"observed days : {n_days}  (avg {len(pnls) / max(n_days, 1):.2f} trades/day)")
    print(f"avg pnl/trade : {sum(pnls) / len(pnls) * 100:.4f}% (notional)")
    print(f"win rate      : {sum(1 for p in pnls if p > 0) / len(pnls) * 100:.1f}%")
    print(f"sizing        : risk {args.risk_percent:.1%} x {args.leverage:.0f}x = "
          f"notional {args.risk_percent * args.leverage:.0%} of equity, min ${args.min_position:.0f}")
    print(f"edge scale    : {args.edge_scale:.2f}x  |  runs: {args.runs}  |  horizon: {args.days} days")
    print()

    header = f"{'capital':>8} | {'mean $/mo':>10} {'median $':>9} {'p5 $':>8} {'P(loss mo)':>11} {'worst DD':>9} {'P(>= $30/mo)':>13} {'mean %/mo':>10}"
    print(header)
    print("-" * len(header))
    for capital in args.capital:
        profits = []
        dds = []
        for _ in range(args.runs):
            p, dd = simulate_month(
                pnls, daily_counts, capital, args.risk_percent,
                args.leverage, args.min_position, args.edge_scale, args.days, rng,
            )
            profits.append(p)
            dds.append(dd)
        profits.sort()
        mean = sum(profits) / len(profits)
        median = profits[len(profits) // 2]
        p5 = profits[int(len(profits) * 0.05)]
        p_loss = sum(1 for p in profits if p < 0) / len(profits)
        worst_dd = min(dds)
        p_target = sum(1 for p in profits if p >= 30.0) / len(profits)
        mean_pct = mean / capital * 100
        print(
            f"${capital:>7.0f} | {mean:>10.2f} {median:>9.2f} {p5:>8.2f} "
            f"{p_loss:>11.1%} {worst_dd:>9.1%} {p_target:>13.1%} {mean_pct:>9.2f}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())