#!/usr/bin/env python3
"""
Compare HIGH_WR_SCALP exit structures: configured default vs RR 1:5 runner style.

All variants generate the SAME signals (identical entry/SL/confirmation gates);
only the take-profit ladder and position splits differ:

  - configured_default : new default (validated winner): 20% closed at TP1 = 1R,
                         80% runner trailed with a Chandelier 3 ATR stop, BE after TP1
  - rr5_runner         : 20% closed at TP1 (1.0R), 80% rides the runner to 5.0R,
                         SL moved to breakeven after TP1

Run:  venv/bin/python scripts/backtest_compare_rr.py [--symbols ...] [--days ...]
"""

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from modules.config_loader import CONFIG
from scripts.backtest_high_wr_scalp import (
    fetch_ohlcv,
    get_high_wr_config,
    make_exchange,
    resolve_symbol,
    run_backtest_set,
    summarize,
)

# RR-based exit variants. Signal generation stays IDENTICAL to the configured
# default (same entry/SL/score gates); only the exit ladder is reworked via
# exit_transform: 20% taken at TP1 = 1R, the remaining 80% either rides to a
# hard runner target (runner_r) or is trailed with a Chandelier stop
# (trail_atr_mult ATR behind the peak), with SL moved to breakeven after TP1.
RR5_OVERRIDES = {
    "move_sl_to_be_after_tp": 1,
}

# Pre-trailing-release default (v1 HIGH_WR): min_score 8, 6 partial TPs with the
# 70/15/8/4/2/1 split, no Chandelier runner. Kept for train/test comparisons.
OLD_DEFAULT = {
    "min_score": 8,
    "tp_splits": [0.70, 0.15, 0.08, 0.04, 0.02, 0.01],
    "use_trailing_runner": False,
    "trail_atr_mult": 0.0,
    "tp1_r": 0.0,
}


def make_rr_transform(tp1_r=1.0, tp1_split=0.20, runner_r=None):
    def transform(signal, cfg):
        side = signal["Side"]
        entry = float(signal["Entry"])
        sl = float(signal["SL"])
        risk = abs(entry - sl)
        if risk <= 0:
            return None

        def tp_at(r):
            return entry + r * risk if side == "Long" else entry - r * risk

        tp1 = tp_at(tp1_r)
        plan = [{"price": tp1, "close_ratio": tp1_split}]
        runner = tp_at(runner_r) if runner_r else tp1
        if runner_r:
            plan.append({"price": runner, "close_ratio": 1.0 - tp1_split})
        signal["TP1"] = tp1
        signal["TP2"] = runner
        signal["TP3"] = runner
        signal["TP_Plan"] = plan
        signal["Move_SL_To_BE_After_TP"] = 1
        return signal
    return transform


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange", default="bybit", choices=["bybit", "binance", "bitget"])
    parser.add_argument("--symbols", default="DUSK,RESOLV,LUMIA,UB,GWEI,TRADOOR,ENSO")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--max-hold-bars", type=int, default=32)
    parser.add_argument("--entry-wait-bars", type=int, default=8)
    parser.add_argument("--fee-rate", type=float, default=0.0006)
    parser.add_argument("--slippage-pct", type=float, default=0.0003)
    parser.add_argument("--entry-fill", default="ideal", choices=["ideal", "aggressive", "conservative"])
    parser.add_argument("--variants", default="configured_default,rr5_runner,rr5_trail_2atr,rr5_trail_3atr",
                        help="comma-separated subset of the variants to run")
    parser.add_argument("--walk-forward", action="store_true",
                        help="split each symbol into train/test windows and report both")
    parser.add_argument("--train-ratio", type=float, default=0.6)
    args = parser.parse_args()

    base_cfg = get_high_wr_config(CONFIG.get("high_wr_scalp", {}))
    base_cfg["enabled"] = True
    base_cfg["timeframes"] = [args.timeframe]

    all_variants = {
        "configured_default": (dict(base_cfg), None, "new default: 20% @1R, 80% runner Chandelier trail 3 ATR"),
        "old_default_v1": ({**base_cfg, **OLD_DEFAULT}, None, "v1: 6 partial TPs 70/15/8/4/2/1, no runner"),
        "rr5_runner": ({**base_cfg, **RR5_OVERRIDES}, make_rr_transform(1.0, 0.20, runner_r=5.0),
                        "20% @1R, 80% hard target 5R"),
        "rr5_trail_2atr": ({**base_cfg, **RR5_OVERRIDES, "trail_atr_mult": 2.0, "trail_start_after_tp": 1},
                           make_rr_transform(1.0, 0.20, runner_r=None),
                           "20% @1R, 80% Chandelier trail 2.0 ATR"),
        "rr5_trail_3atr": ({**base_cfg, **RR5_OVERRIDES, "trail_atr_mult": 3.0, "trail_start_after_tp": 1},
                           make_rr_transform(1.0, 0.20, runner_r=None),
                           "20% @1R, 80% Chandelier trail 3.0 ATR, hold 32"),
        "rr5_trail_3atr_hold48": ({**base_cfg, **RR5_OVERRIDES, "trail_atr_mult": 3.0, "trail_start_after_tp": 1, "max_hold_bars": 48},
                                  make_rr_transform(1.0, 0.20, runner_r=None),
                                  "trail 3.0 ATR, hold 48"),
        "rr5_trail_3atr_hold64": ({**base_cfg, **RR5_OVERRIDES, "trail_atr_mult": 3.0, "trail_start_after_tp": 1, "max_hold_bars": 64},
                                  make_rr_transform(1.0, 0.20, runner_r=None),
                                  "trail 3.0 ATR, hold 64"),
        "rr5_trail_3atr_ms9": ({**base_cfg, **RR5_OVERRIDES, "trail_atr_mult": 3.0, "trail_start_after_tp": 1, "min_score": 9},
                               make_rr_transform(1.0, 0.20, runner_r=None),
                               "trail 3.0 ATR, min_score 9"),
        "rr5_trail_3atr_ms10": ({**base_cfg, **RR5_OVERRIDES, "trail_atr_mult": 3.0, "trail_start_after_tp": 1, "min_score": 10},
                                make_rr_transform(1.0, 0.20, runner_r=None),
                                "trail 3.0 ATR, min_score 10"),
        "rr5_trail_3atr_ms10_shorts": ({**base_cfg, **RR5_OVERRIDES, "trail_atr_mult": 3.0, "trail_start_after_tp": 1, "min_score": 10, "allow_shorts": True},
                                       make_rr_transform(1.0, 0.20, runner_r=None),
                                       "long+short, trail 3.0 ATR, min_score 10"),
        "rr5_trail_3atr_ms10_shorts_only": ({**base_cfg, **RR5_OVERRIDES, "trail_atr_mult": 3.0, "trail_start_after_tp": 1, "min_score": 10, "allow_longs": False, "allow_shorts": True},
                                            make_rr_transform(1.0, 0.20, runner_r=None),
                                            "short-only, trail 3.0 ATR, min_score 10"),
        "btc_bias_hard_4h": ({**base_cfg, "btc_bias_filter": "hard"},
                              None, "+ BTC bias hard reject (4h)"),
        "btc_bias_soft_4h": ({**base_cfg, "btc_bias_filter": "soft"},
                              None, "+ BTC bias soft penalty (4h)"),
        "btc_bias_hard_1d": ({**base_cfg, "btc_bias_filter": "hard", "btc_bias_timeframe": "1d"},
                              None, "+ BTC bias hard reject (1d, matches live get_btc_bias)"),
    }
    requested = [v.strip() for v in args.variants.split(",") if v.strip()]
    missing = [v for v in requested if v not in all_variants]
    if missing:
        parser.error(f"unknown variants: {missing}")
    variants = {name: all_variants[name] for name in requested}
    for name, (cfg, _, desc) in variants.items():
        runner_r = cfg["tp_atr_multipliers"][-1] / cfg["sl_atr"]
        print(f"{name}: SL {cfg['sl_atr']} ATR | signal TPs {cfg['tp_atr_multipliers']} ATR "
              f"| exit: {desc} | min_runner_r {cfg['min_runner_r']}")

    exchange = make_exchange(args.exchange)
    datasets = []
    for raw in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        try:
            symbol = raw if "/" in raw else resolve_symbol(exchange, raw)
            if not symbol:
                print(f"skip {raw}: symbol not found"); continue
            print(f"fetch {symbol} {args.timeframe} {args.days}d")
            df = fetch_ohlcv(exchange, symbol, args.timeframe, args.days)
        except Exception as exc:
            print(f"skip {raw}: fetch failed: {exc}"); continue
        if df.empty:
            print(f"skip {symbol}: no candles"); continue
        datasets.append((symbol, df))
    if not datasets:
        print("no data"); return 1

    # BTC regime data for the btc_bias_filter variants (optional).
    btc_df = None
    try:
        btc_raw = "BTC/USDT:USDT" if "/" in datasets[0][0] else "BTC/USDT"
        print(f"fetch {btc_raw} 4h {args.days}d (for BTC bias)")
        btc_df = fetch_ohlcv(exchange, btc_raw, "4h", args.days)
        if btc_df.empty:
            btc_df = None
            print("BTC fetch empty; btc_bias_filter variants will be no-ops")
    except Exception as exc:
        btc_df = None
        print(f"BTC fetch failed: {exc}; btc_bias_filter variants will be no-ops")

    if args.walk_forward:
        from scripts.backtest_high_wr_scalp import build_walk_forward_ranges
        train_ranges, test_ranges = build_walk_forward_ranges(datasets, args.train_ratio)
        pct = int(args.train_ratio * 100)
        print(f"\n===== WALK-FORWARD (train {pct}% / test {100 - pct}%) =====")
        rows = []
        for name, (cfg, transform, desc) in variants.items():
            hold = int(cfg.get("max_hold_bars", args.max_hold_bars))
            print(f"\n== {name}: {desc}")
            by_label = {}
            for label, ranges in (("train", train_ranges), ("test", test_ranges)):
                t0 = time.time()
                results = run_backtest_set(
                    datasets, args.timeframe, cfg, hold, args.entry_wait_bars,
                    args.fee_rate, args.slippage_pct, args.entry_fill,
                    ranges=ranges, exit_transform=transform, btc_df=btc_df,
                )
                s = summarize(results)
                by_label[label] = (s, results)
                print(f"  {label:5s}: trades={s.get('trades', 0):3d} wr={s.get('win_rate', 0):6.2f}% "
                      f"pf={s.get('profit_factor', 0):5.2f} total={s.get('total_pnl_pct', 0):8.4f}% "
                      f"dd={s.get('max_drawdown_pct', 0):8.4f}% ({time.time() - t0:.0f}s)")
            rows.append((name, by_label))
        print("\n===== WALK-FORWARD COMPARISON =====")
        hdr = f"{'variant':<30}{'tr_trades':>10}{'tr_pf':>8}{'te_trades':>10}{'te_pf':>8}{'te_total%':>11}"
        print(hdr)
        for name, by_label in rows:
            tr, te = by_label["train"][0], by_label["test"][0]
            print(f"{name:<30}{tr.get('trades', 0):>10}{tr.get('profit_factor', 0):>8.2f}"
                  f"{te.get('trades', 0):>10}{te.get('profit_factor', 0):>8.2f}"
                  f"{te.get('total_pnl_pct', 0):>11.4f}")
        return 0

    summaries, all_results = {}, {}
    for name, (cfg, transform, _) in variants.items():
        t0 = time.time()
        hold = int(cfg.get("max_hold_bars", args.max_hold_bars))
        results = run_backtest_set(
            datasets, args.timeframe, cfg, hold, args.entry_wait_bars,
            args.fee_rate, args.slippage_pct, args.entry_fill,
            exit_transform=transform, btc_df=btc_df,
        )
        summaries[name] = summarize(results)
        all_results[name] = results
        print(f"\n=== {name} ({time.time() - t0:.0f}s) ===")
        from scripts.backtest_high_wr_scalp import print_summary
        print_summary(summaries[name], results)

    print("\n===== COMPARISON =====")
    keys = ["trades", "win_rate", "tp1_rate", "tp2_rate", "avg_pnl_pct", "total_pnl_pct",
            "profit_factor", "max_drawdown_pct"]
    print(f"{'metric':<18}" + "".join(f"{n:>14}" for n in summaries))
    for k in keys:
        print(f"{k:<18}" + "".join(f"{summaries[n].get(k, 0):>14.4f}" for n in summaries))

    # breakeven win rate for each variant, given observed avg win/loss
    print("\n===== BREAKEVEN / EXPECTANCY =====")
    for name in summaries:
        rows = all_results[name]
        pnls = [r.pnl_pct for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(losses)) / len(losses) if losses else 0.0
        if avg_loss > 0:
            be_wr = avg_loss / (avg_win + avg_loss) * 100
            print(f"{name:<18} avg_win={avg_win:8.4f}% avg_loss={avg_loss:8.4f}% "
                  f"breakeven_wr={be_wr:6.2f}% observed_wr={summaries[name]['win_rate']:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())