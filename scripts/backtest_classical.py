#!/usr/bin/env python3
"""
Offline backtest harness for the CLASSICAL pipeline (main.py analyze_ticker):
geometric patterns -> SMC -> quant (RVOL/Z/OBI) -> derivatives -> fib setup.

Replays the exact gate logic from main.py against historical OHLCV slices (no
exchange calls), then simulates the NORMAL exit model from auto_trades.py
(30/30/40 split TPs, SL to breakeven at ~50% to TP1, 2x ATR Chandelier trail
on the final 40% after TP2). Adaptive management is NOT modeled (v1).

Run:
    venv/bin/python scripts/backtest_classical.py --symbols BTC,ETH,SOL \
        --timeframe 4h --days 180 --output /tmp/bt_classical.csv
"""

import argparse
import csv
import os
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from modules.config_loader import CONFIG
from modules.technicals import (
    get_technicals,
    detect_divergence,
    check_volatility_squeeze,
    detect_regime,
)
from modules.quant import calculate_metrics, check_fakeout
from modules.derivatives import analyze_derivatives
from modules.smc import analyze_smc
from modules.patterns import find_pattern
from scripts.backtest_high_wr_scalp import make_exchange, resolve_symbol, fetch_ohlcv

TP_SPLIT = [0.30, 0.30, 0.40]
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006
SLIPPAGE = 0.0003


@dataclass
class ClassicalResult:
    symbol: str
    timeframe: str
    signal_time: str
    entry_time: str
    exit_time: str
    side: str
    pattern: str
    entry: float
    stop: float
    exit: float
    pnl_pct: float
    rr: float
    tp_hits: int
    outcome: str
    hold_bars: int
    total_score: int


def net_return(entry, exit_price, side, exit_type="limit"):
    gross = (exit_price - entry) / entry if side == "Long" else (entry - exit_price) / entry
    entry_fee = MAKER_FEE
    if exit_type == "market":
        return gross - entry_fee - TAKER_FEE - SLIPPAGE
    return gross - entry_fee - MAKER_FEE


def calculate_rr(entry, sl, target):
    if entry <= 0 or sl <= 0 or target <= 0:
        return 0.0
    risk = abs(entry - sl)
    return round(abs(target - entry) / risk, 2) if risk > 0 else 0.0


def build_signal(slice_df, ticker, symbol, timeframe):
    """Mirror of main.py analyze_ticker's scoring/gating, minus exchange calls."""
    cfg = CONFIG
    df = slice_df.copy()
    s = cfg["setup"]
    try:
        pattern = find_pattern(df)
        side = None
        if pattern:
            side = cfg["pattern_signals"].get(pattern)
        if side:
            valid_smc, smc_score, smc_reasons = analyze_smc(df, side)
        else:
            _, long_score, long_reas = analyze_smc(df, "Long")
            _, short_score, short_reas = analyze_smc(df, "Short")
            if long_score > short_score and long_score > 0:
                side, pattern, smc_score, smc_reasons = "Long", "SMC Zone", long_score, long_reas
            elif short_score > long_score and short_score > 0:
                side, pattern, smc_score, smc_reasons = "Short", "SMC Zone", short_score, short_reas
            else:
                return None
        if side is None or smc_score < cfg["strategy"].get("min_smc_score", 0):
            return None

        df, basis, z_score, zeta_score, obi, quant_score, quant_reasons = calculate_metrics(
            df, ticker, order_book=None
        )
        valid_deriv, deriv_score, deriv_reasons = analyze_derivatives(df, ticker, side)
        if not valid_deriv:
            return None
        if deriv_score < cfg["strategy"].get("min_deriv_score", 0):
            return None

        div_score, div_msg = detect_divergence(df)
        tech_score = 3 + div_score
        regime = detect_regime(df)
        is_squeezing, squeeze_firing = check_volatility_squeeze(df)
        tech_reasons = [f"Pattern: {pattern}", div_msg] + (smc_reasons or [])
        if squeeze_firing:
            tech_score += 2
            tech_reasons.append("Squeeze Firing")
        elif is_squeezing:
            tech_score += 1
            tech_reasons.append("Squeeze ON")
        if regime == "Trending Bull":
            tech_score += 1 if side == "Long" else -1
        elif regime == "Trending Bear":
            tech_score += 1 if side == "Short" else -1
        tech_reasons.append(f"Regime: {regime}")

        valid_fo, fo_msg = check_fakeout(df, cfg["indicators"]["min_rvol"])
        if not valid_fo:
            quant_score -= 1
        elif fo_msg:
            quant_reasons.append(fo_msg)

        if tech_score < cfg["strategy"]["min_tech_score"]:
            return None
        if quant_score < cfg["strategy"].get("min_quant_score", 0):
            return None

        swing_high = df["high"].iloc[-50:].max()
        swing_low = df["low"].iloc[-50:].min()
        rng = swing_high - swing_low
        if side == "Long":
            entry = (swing_high - (rng * s["fib_entry_start"]) + swing_high - (rng * s["fib_entry_end"])) / 2
            sl = swing_low - (rng * s["fib_sl"])
            tp1, tp2, tp3 = swing_low + rng, swing_low + (rng * 1.618), swing_low + (rng * 2.618)
        else:
            entry = (swing_low + (rng * s["fib_entry_start"]) + swing_low + (rng * s["fib_entry_end"])) / 2
            sl = swing_high + (rng * s["fib_sl"])
            tp1, tp2, tp3 = swing_high - rng, swing_high - (rng * 1.618), swing_high - (rng * 2.618)

        rr_tp1 = calculate_rr(entry, sl, tp1)
        rr_tp2 = calculate_rr(entry, sl, tp2)
        if rr_tp1 < cfg["strategy"].get("risk_reward_min_tp1", 0.6):
            return None
        if rr_tp2 < cfg["strategy"].get("risk_reward_min_tp2", 1.2):
            return None

        natr_val = float(df["NATR_14"].iloc[-1]) if "NATR_14" in df.columns else 0.0
        return {
            "Symbol": symbol, "Side": side, "Timeframe": timeframe, "Pattern": pattern,
            "Entry": float(entry), "SL": float(sl), "TP1": float(tp1), "TP2": float(tp2), "TP3": float(tp3),
            "RR": float(rr_tp2), "NATR": float(natr_val),
            "Tech_Score": int(tech_score), "Quant_Score": int(quant_score),
            "Deriv_Score": int(deriv_score), "SMC_Score": int(smc_score),
            "Total_Score": int(tech_score + smc_score + quant_score + deriv_score),
            "Reasons": ", ".join(tech_reasons),
        }
    except Exception:
        return None


def simulate_trade(df, signal_idx, signal, max_hold_bars, entry_wait_bars):
    side = signal["Side"]
    entry = float(signal["Entry"])
    stop = float(signal["SL"])
    tps = [float(signal["TP1"]), float(signal["TP2"]), float(signal["TP3"])]

    entry_idx = None
    for j in range(signal_idx + 1, min(len(df), signal_idx + 1 + entry_wait_bars)):
        if (side == "Long" and df["low"].iloc[j] <= entry) or (side == "Short" and df["high"].iloc[j] >= entry):
            entry_idx = j
            break
    if entry_idx is None:
        return None

    def touched(row, price, is_tp):
        if side == "Long":
            return row["low"] <= price if not is_tp else row["high"] >= price
        return row["high"] >= price if not is_tp else row["low"] <= price

    remaining = 1.0
    pnl_pct = 0.0
    tp_hits = 0
    exit_price = entry
    outcome = "TIME_EXIT"
    trailing = False
    peak = entry
    be_done = False
    atr0 = max(float(df["high"].iloc[signal_idx] - df["low"].iloc[signal_idx]), 1e-9)

    max_idx = min(len(df), entry_idx + 1 + max_hold_bars)
    for idx in range(entry_idx + 1, max_idx):
        row = df.iloc[idx]
        if touched(row, stop, False):
            pnl_pct += remaining * net_return(entry, stop, side, "market") * 100
            remaining = 0.0
            exit_price = stop
            outcome = "SL" if tp_hits < 1 else "TRAIL_SL"
            exit_idx = idx
            break
        while tp_hits < 3 and touched(row, tps[tp_hits], True):
            c = min(TP_SPLIT[tp_hits], remaining)
            pnl_pct += c * net_return(entry, tps[tp_hits], side) * 100
            remaining -= c
            exit_price = tps[tp_hits]
            tp_hits += 1
            if remaining <= 1e-9:
                outcome = "FULL_TP"
                exit_idx = idx
                break
        if remaining <= 1e-9:
            break
        atr = max(float(df["high"].iloc[idx] - df["low"].iloc[idx]), atr0)
        if not be_done and tp_hits >= 1 and not trailing:
            be_done = True
        if be_done and not trailing:
            stop = entry  # breakeven once TP1 filled
        if tp_hits >= 2 and not trailing:
            trailing = True
            stop = entry
            peak = entry
        if trailing:
            if side == "Long":
                peak = max(peak, row["high"])
                stop = max(stop, peak - 2 * atr)
            else:
                peak = min(peak, row["low"])
                stop = min(stop, peak + 2 * atr)
    else:
        exit_idx = max_idx - 1
        close = float(df.iloc[exit_idx]["close"])
        pnl_pct += remaining * net_return(entry, close, side, "market") * 100
        exit_price = close

    if outcome == "TIME_EXIT":
        outcome = "TIME_WIN" if pnl_pct > 0 else ("TIME_LOSS" if pnl_pct < 0 else "FLAT")
    return ClassicalResult(
        symbol=signal["Symbol"], timeframe=signal["Timeframe"],
        signal_time=str(df.iloc[signal_idx]["timestamp"]),
        entry_time=str(df.iloc[entry_idx]["timestamp"]),
        exit_time=str(df.iloc[exit_idx]["timestamp"]),
        side=side, pattern=signal["Pattern"], entry=entry, stop=stop,
        exit=exit_price, pnl_pct=pnl_pct, rr=float(signal["RR"]),
        tp_hits=tp_hits, outcome=outcome, hold_bars=exit_idx - entry_idx,
        total_score=int(signal["Total_Score"]),
    )


def backtest_symbol(df, symbol, timeframe, max_hold_bars, entry_wait_bars):
    tech = get_technicals(df.copy()).reset_index(drop=True)
    results = []
    unresolved_until = -1
    for i in range(300, len(tech)):
        if i < unresolved_until:
            continue
        slice_df = tech.iloc[: i + 1]
        close = float(slice_df["close"].iloc[-1])
        if close <= 0:
            continue
        ticker = {"last": close, "info": {"fundingRate": 0, "indexPrice": close}}
        signal = build_signal(slice_df, ticker, symbol, timeframe)
        if not signal:
            continue
        # Map the technical-slice index back to the raw frame via timestamp
        # (get_technicals drops NaN rows, shifting the index).
        ts = pd.Timestamp(slice_df["timestamp"].iloc[-1])
        pos = df["timestamp"].searchsorted(ts, side="left")
        raw_i = int(max(0, min(pos, len(df) - 1)))
        trade = simulate_trade(df, raw_i, signal, max_hold_bars, entry_wait_bars)
        if trade:
            results.append(trade)
            unresolved_until = i + entry_wait_bars + max_hold_bars
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange", default="bybit", choices=["bybit", "binance", "bitget"])
    parser.add_argument("--symbols", default="BTC,ETH,SOL,DOGE,SUI")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--max-hold-bars", type=int, default=24)
    parser.add_argument("--entry-wait-bars", type=int, default=4)
    parser.add_argument("--output")
    args = parser.parse_args()

    exchange = make_exchange(args.exchange)
    all_results = []
    for raw in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        try:
            symbol = raw if "/" in raw else resolve_symbol(exchange, raw)
            if not symbol:
                print(f"skip {raw}: not found")
                continue
            print(f"fetch {symbol} {args.timeframe} {args.days}d", flush=True)
            df = fetch_ohlcv(exchange, symbol, args.timeframe, args.days)
        except Exception as exc:
            print(f"skip {raw}: {exc}")
            continue
        if df.empty:
            continue
        t0 = time.time()
        results = backtest_symbol(df, symbol, args.timeframe, args.max_hold_bars, args.entry_wait_bars)
        n = len(results)
        if n:
            wins = sum(1 for r in results if r.pnl_pct > 0)
            tot = sum(r.pnl_pct for r in results)
            gross_w = sum(r.pnl_pct for r in results if r.pnl_pct > 0)
            gross_l = abs(sum(r.pnl_pct for r in results if r.pnl_pct < 0))
            print(f"{symbol.split('/')[0]:6} trades={n:3d} wr={wins / n * 100:5.1f}% "
                  f"avg={tot / n:7.4f}% total={tot:8.4f}% pf={gross_w / gross_l if gross_l else 999:5.2f} "
                  f"({time.time() - t0:.0f}s)")
        else:
            print(f"{symbol.split('/')[0]:6} trades=  0")
        all_results.extend(results)

    if not all_results:
        print("\nNo trades. Loosen gates (config strategy thresholds) or use more history.")
        return 0
    n = len(all_results)
    wins = sum(1 for r in all_results if r.pnl_pct > 0)
    tot = sum(r.pnl_pct for r in all_results)
    gross_w = sum(r.pnl_pct for r in all_results if r.pnl_pct > 0)
    gross_l = abs(sum(r.pnl_pct for r in all_results if r.pnl_pct < 0))
    print(f"\nCLASSICAL backtest: trades={n} wr={wins / n * 100:.1f}% "
          f"avg={tot / n:.4f}% total={tot:.4f}% pf={gross_w / gross_l if gross_l else 999:.2f}")
    if args.output:
        with open(args.output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(all_results[0]).keys()))
            writer.writeheader()
            for r in all_results:
                writer.writerow(asdict(r))
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())