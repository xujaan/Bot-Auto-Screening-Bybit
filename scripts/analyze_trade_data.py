#!/usr/bin/env python3
"""
Slice-and-dice analysis of a HIGH_WR_SCALP backtest trade CSV.

Answers "which conditions give the highest win rate with positive expectancy":
  - by side, score bucket, symbol, outcome, hold time
  - by technical indicator values at signal time (RVOL, ADX, NATR, EMA spread,
    stoch, MACD, hour of day) when an OHLCV pickle is supplied.

Run:
    venv/bin/python scripts/analyze_trade_data.py --csv /tmp/bt_livecfg.csv
    venv/bin/python scripts/analyze_trade_data.py --csv /tmp/bt_livecfg.csv --ohlcv-pickle /tmp/mw_data.pkl
"""

import argparse
import os
import pickle
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import pandas as pd

from modules.technicals import get_technicals


def load_trades(path):
    df = pd.read_csv(path)
    df["signal_time"] = pd.to_datetime(df["signal_time"], utc=True)
    return df


def stats(rows):
    n = len(rows)
    if n == 0:
        return None
    pnl = rows["pnl_pct"]
    wins = pnl > 0
    gross_w = pnl[wins].sum()
    gross_l = abs(pnl[~wins].sum())
    return {
        "n": n,
        "wr": wins.mean() * 100,
        "avg": pnl.mean(),
        "total": pnl.sum(),
        "pf": gross_w / gross_l if gross_l > 0 else float("inf"),
    }


def bucket_report(df, key_fn, title, min_n=15, max_rows=12):
    buckets = defaultdict(list)
    for _, row in df.iterrows():
        buckets[key_fn(row)].append(row)
    out = []
    for key, rows in buckets.items():
        s = stats(pd.DataFrame(rows))
        if s and s["n"] >= min_n:
            out.append((key, s))
    out.sort(key=lambda kv: (-kv[1]["wr"], -kv[1]["pf"]))
    print(f"\n### {title}  (min n={min_n})")
    print(f"{'bucket':<22}{'n':>5}{'WR%':>8}{'avg%':>9}{'PF':>7}")
    for key, s in out[:max_rows]:
        pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
        print(f"{str(key):<22}{s['n']:>5}{s['wr']:>8.1f}{s['avg']:>9.3f}{pf:>7}")


def add_indicators(df, ohlcv_pickle):
    data = pickle.load(open(ohlcv_pickle, "rb"))
    tech_by_sym = {}
    for sym, frame in data.items():
        tech_by_sym[sym] = get_technicals(frame.copy()).reset_index(drop=True)

    for idx, row in df.iterrows():
        tech = tech_by_sym.get(row["symbol"])
        if tech is None:
            continue
        pos = tech["timestamp"].searchsorted(row["signal_time"], side="right") - 1
        if pos < 0:
            continue
        bar = tech.iloc[pos]
        atr = float(bar["ATR_14"])
        ema_f = float(bar["EMA_Fast"])
        ema_s = float(bar["EMA_Slow"])
        ema_spread = abs(ema_f - ema_s) / atr if atr > 0 else 0
        df.loc[idx, "RVOL"] = float(bar.get("RVOL", 0))
        df.loc[idx, "ADX"] = float(bar.get("ADX_14", 0))
        df.loc[idx, "NATR"] = float(bar.get("NATR_14", 0))
        df.loc[idx, "EMA_spread"] = ema_spread
        df.loc[idx, "stoch_k"] = float(bar.get("stoch_rsi_k", 50))
        df.loc[idx, "MACD_pos"] = float(bar.get("MACD_h", 0)) > 0
        df.loc[idx, "hour"] = int(row["signal_time"].hour)
    return df.dropna(subset=["RVOL", "ADX", "NATR", "EMA_spread", "stoch_k"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--ohlcv-pickle", default="", help="pickle of {symbol: OHLCV df} to add indicator buckets")
    parser.add_argument("--min-n", type=int, default=15)
    args = parser.parse_args()

    df = load_trades(args.csv)
    print(f"trades: {len(df)} | WR: {stats(df)['wr']:.1f}% | avg: {stats(df)['avg']:.4f}% | PF: {stats(df)['pf']:.2f}")

    bucket_report(df, lambda r: r["side"], "by side", args.min_n)
    bucket_report(df, lambda r: f"score {int(r['score'])}", "by score", args.min_n)
    bucket_report(df, lambda r: r["symbol"].split("/")[0], "by symbol", args.min_n)
    bucket_report(df, lambda r: r["outcome"], "by outcome", 1)
    bucket_report(df, lambda r: f"hold {int(r['hold_bars'])} bars", "by hold bars", 1)

    def bucket(v, edges, labels):
        for i, e in enumerate(edges):
            if v <= e:
                return labels[i]
        return labels[-1]

    if args.ohlcv_pickle:
        df = add_indicators(df, args.ohlcv_pickle)
        bucket_report(df, lambda r: bucket(r["RVOL"], [1.15, 1.5, 2.0], ["RVOL <1.15", "1.15-1.5", "1.5-2", "RVOL >2"]), "by RVOL at signal", args.min_n)
        bucket_report(df, lambda r: bucket(r["ADX"], [15, 20, 25], ["ADX <15", "15-20", "20-25", "ADX >25"]), "by ADX at signal", args.min_n)
        bucket_report(df, lambda r: bucket(r["NATR"], [0.3, 0.5, 0.8, 1.2], ["NATR <0.3", "0.3-0.5", "0.5-0.8", "0.8-1.2", "NATR >1.2"]), "by NATR% at signal", args.min_n)
        bucket_report(df, lambda r: bucket(r["EMA_spread"], [0.1, 0.25, 0.5], ["EMA <0.1", "0.1-0.25", "0.25-0.5", "EMA >0.5"]), "by EMA spread (ATR)", args.min_n)
        bucket_report(df, lambda r: bucket(r["stoch_k"], [30, 70], ["stoch <30", "30-70", "stoch >70"]), "by stoch_k at signal", args.min_n)
        bucket_report(df, lambda r: f"MACD {'pos' if r['MACD_pos'] else 'neg'}", "by MACD sign", args.min_n)
        bucket_report(df, lambda r: bucket(int(r["hour"]), [6, 12, 18], ["UTC 0-6", "6-12", "12-18", "18-24"]), "by hour of day (UTC)", args.min_n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())