#!/usr/bin/env python3
"""
Multi-window validation for HIGH_WR_SCALP.

Fetches N days of OHLCV per symbol once, splits the history into W equal
windows, and runs the backtest independently on each window (each window gets
full indicator warmup from earlier data). Reports per-window and per-symbol
stats so you can see whether the edge is consistent or a single-window fluke.

Use for periodic re-screening: symbols that stay positive across most windows
are the ones worth whitelisting.

Run:
    venv/bin/python scripts/backtest_multi_window.py --symbols DOGE,SUI,XRP \
        --days 60 --windows 3 --config-override '{"min_score": 6}'
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from modules.config_loader import CONFIG
from modules.high_wr_scalp import get_high_wr_config
from scripts.backtest_high_wr_scalp import (
    make_exchange,
    resolve_symbol,
    fetch_ohlcv,
    run_backtest_set,
    summarize,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange", default="bybit", choices=["bybit", "binance", "bitget"])
    parser.add_argument("--symbols", default="DOGE,SUI,SOXL,SOPH,HYPE,XRP,ETH")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--windows", type=int, default=3)
    parser.add_argument("--config-override", default="", help="JSON dict merged over high_wr_scalp config")
    parser.add_argument("--fee-rate", type=float, default=0.0006)
    parser.add_argument("--maker-fee-rate", type=float, default=0.0002)
    parser.add_argument("--slippage-pct", type=float, default=0.0003)
    parser.add_argument("--max-hold-bars", type=int, default=32)
    parser.add_argument("--entry-wait-bars", type=int, default=8)
    args = parser.parse_args()

    cfg = get_high_wr_config(CONFIG.get("high_wr_scalp", {}))
    if args.config_override:
        cfg.update(json.loads(args.config_override))
    cfg["enabled"] = True
    cfg["timeframes"] = [args.timeframe]

    exchange = make_exchange(args.exchange)
    datasets = []
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
            print(f"skip {symbol}: no candles")
            continue
        datasets.append((symbol, df))

    if not datasets:
        print("no data")
        return 1

    n_windows = max(2, args.windows)
    total_bars = min(len(df) for _, df in datasets)
    win_size = total_bars // n_windows
    print(f"\n{len(datasets)} symbols, {total_bars} bars available, "
          f"{n_windows} windows x ~{win_size} bars\n")

    all_window_results = []
    for w in range(n_windows):
        start = w * win_size
        end = min((w + 1) * win_size, total_bars) if w < n_windows - 1 else None
        ranges = {symbol: (start, end) for symbol, _ in datasets}
        results = run_backtest_set(
            datasets,
            args.timeframe,
            cfg,
            args.max_hold_bars,
            args.entry_wait_bars,
            args.fee_rate,
            args.slippage_pct,
            "ideal",
            ranges=ranges,
            progress_label=f"window {w + 1}",
            maker_fee=args.maker_fee_rate,
        )
        all_window_results.append(results)
        summary = summarize(results)
        by_sym = {}
        for row in results:
            by_sym.setdefault(row.symbol, []).append(row)
        print(f"\n===== WINDOW {w + 1} (bars {start}-{end or total_bars}) =====")
        print(f"trades={summary.get('trades', 0):4d} wr={summary.get('win_rate', 0):6.2f}% "
              f"avg={summary.get('avg_pnl_pct', 0):7.4f}% total={summary.get('total_pnl_pct', 0):8.4f}% "
              f"pf={summary.get('profit_factor', 0):5.2f} dd={summary.get('max_drawdown_pct', 0):7.4f}%")
        for symbol, _ in datasets:
            rows = by_sym.get(symbol, [])
            if not rows:
                print(f"  {symbol.split('/')[0]:6} no trades")
                continue
            s = summarize(rows)
            print(f"  {symbol.split('/')[0]:6} n={s['trades']:3d} wr={s['win_rate']:6.2f}% "
                  f"avg={s['avg_pnl_pct']:7.4f}% pf={s['profit_factor']:5.2f}")

    print("\n===== SYMBOL CONSISTENCY (positive windows / total) =====")
    for symbol, _ in datasets:
        pos = 0
        for results in all_window_results:
            rows = [r for r in results if r.symbol == symbol]
            if rows and summarize(rows).get("profit_factor", 0.0) >= 1.0 \
                    and summarize(rows).get("total_pnl_pct", 0.0) > 0:
                pos += 1
        flag = "  <-- KEEP" if pos >= (n_windows + 1) // 2 else ""
        print(f"  {symbol.split('/')[0]:6} {pos}/{n_windows} positive windows{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())