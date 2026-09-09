"""
High win-rate manual scalp signal model.

This module builds Telegram-style setups: entry zone, tight partial targets,
and breakeven after TP2. It is analysis-only; execution stays manual unless
another caller explicitly trades the generated signal.
"""

import math
from typing import Dict, List, Optional

import pandas as pd

from modules.technicals import calculate_atr, detect_regime


DEFAULT_CONFIG = {
    "enabled": False,
    "timeframes": ["15m", "30m"],
    "allow_longs": True,
    "allow_shorts": False,
    "min_rvol": 1.35,
    "min_adx": 18.0,
    "min_natr": 0.25,
    "max_natr": 7.5,
    "entry_atr_near": 0.25,
    "entry_atr_far": 0.95,
    "sl_atr": 0.85,
    "swing_lookback": 18,
    "tp_atr_multipliers": [0.80, 1.20, 1.70, 2.30, 3.00, 4.00],
    "tp_splits": [0.20, 0.80],
    "tp1_r": 1.0,
    "use_trailing_runner": True,
    "trail_atr_mult": 3.0,
    "move_sl_to_be_after_tp": 1,
    "btc_bias_filter": "none",
    "btc_bias_penalty": 2,
    "btc_bias_timeframe": "4h",
    "max_entry_distance_pct": 0.45,
    "min_score": 10,
    "max_sl_pct": 1.35,
    "min_tp2_r": 0.85,
    "min_runner_r": 2.00,
    "cool_funding_abs": 0.0008,
    "require_sma200_alignment": True,
    "min_trend_spread_atr": 0.08,
    "min_ema_slope_atr": 0.015,
    "max_extension_atr": 1.35,
    "pullback_lookback": 5,
    "max_last_range_atr": 2.20,
    "max_last_body_atr": 1.10,
    "max_opposite_wick_ratio": 0.45,
    "min_close_position_long": 0.52,
    "max_close_position_short": 0.48,
    "require_momentum_turn": True,
    "enable_breakout_retest": True,
    "breakout_lookback": 24,
    "retest_lookback": 6,
    "breakout_buffer_atr": 0.08,
    "min_breakout_rvol": 1.55,
    "min_breakout_body_atr": 0.55,
    "min_breakout_close_position_long": 0.68,
    "max_breakout_close_position_short": 0.32,
    "max_retest_distance_atr": 0.45,
    "max_chase_from_breakout_atr": 1.05,
    "breakout_entry_atr_near": 0.08,
    "breakout_entry_atr_far": 0.55,
    "sl_breakout_buffer_atr": 0.35,
    "require_30m_confirmation": True,
    "confirmation_timeframe": "30m",
    "trend_pullback_min_score": 8,
    "trend_pullback_min_rvol": 1.35,
    "trend_pullback_min_adx": 20.0,
    "require_trend_pullback_confirm_regime": False,
    "require_trend_pullback_rvol_or_adx": True,
    "require_trend_pullback_macd_sign": True,
    "min_breakout_score": 8,
    "require_4h_confirmation": True,
    "macro_confirmation_mode": "penalty",
    "macro_penalty": 1,
    "macro_timeframe": "4h",
    "max_macro_extension_atr": 2.20,
    "use_order_book": True,
    "order_book_limit": 25,
    "min_high_probability_rvol": 1.0,
    "min_high_probability_obi": -0.15,
    "max_against_wall_ratio": 1.8,
    "wall_adjust_entry_atr": 0.20,
    "order_book_penalty": 2,
}


def get_high_wr_config(config: Optional[Dict] = None) -> Dict:
    cfg = DEFAULT_CONFIG.copy()
    if config:
        cfg.update(config)
    return cfg


def is_enabled_for_timeframe(timeframe: str, config: Optional[Dict] = None) -> bool:
    cfg = get_high_wr_config(config)
    return bool(cfg.get("enabled")) and str(timeframe) in set(cfg.get("timeframes", []))


def _safe_float(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return default
        return value
    except Exception:
        return default


def _last(df: pd.DataFrame, column: str, default=0.0) -> float:
    if column not in df.columns or df.empty:
        return default
    return _safe_float(df[column].iloc[-1], default)


def _record_rejection(diagnostics: Optional[Dict], reason: str, symbol: str, detail: str = "") -> None:
    if diagnostics is None:
        return
    lock = diagnostics.get("lock")

    def write():
        counts = diagnostics.setdefault("counts", {})
        examples = diagnostics.setdefault("examples", {})
        counts[reason] = counts.get(reason, 0) + 1
        bucket = examples.setdefault(reason, [])
        if len(bucket) < int(diagnostics.get("max_examples", 5)):
            item = symbol if not detail else f"{symbol}: {detail}"
            bucket.append(item)

    if lock:
        with lock:
            write()
    else:
        write()


def _rvol(df: pd.DataFrame, window=20) -> float:
    if len(df) < window + 1:
        return 0.0
    if "RVOL" in df.columns:
        return _last(df, "RVOL")
    vol_sma = df["volume"].rolling(window).mean()
    df["RVOL"] = df["volume"] / vol_sma
    avg = _safe_float(vol_sma.iloc[-1])
    if avg <= 0:
        return 0.0
    return _safe_float(df["volume"].iloc[-1]) / avg


def _funding_rate(ticker: Optional[Dict]) -> float:
    if not ticker:
        return 0.0
    info = ticker.get("info", {}) if isinstance(ticker, dict) else {}
    return _safe_float(info.get("fundingRate"), 0.0)


def _candle_stats(row) -> Dict[str, float]:
    high = _safe_float(row.get("high"))
    low = _safe_float(row.get("low"))
    open_ = _safe_float(row.get("open"))
    close = _safe_float(row.get("close"))
    candle_range = max(high - low, 1e-12)
    body = abs(close - open_)
    upper_wick = high - max(open_, close)
    lower_wick = min(open_, close) - low
    close_pos = (close - low) / candle_range
    return {
        "range": candle_range,
        "body": body,
        "upper_wick": max(upper_wick, 0.0),
        "lower_wick": max(lower_wick, 0.0),
        "close_pos": close_pos,
    }


def _ema_slope_atr(df: pd.DataFrame, column: str, atr: float, lookback: int = 5) -> float:
    if atr <= 0 or column not in df.columns or len(df) <= lookback:
        return 0.0
    current = _safe_float(df[column].iloc[-1])
    previous = _safe_float(df[column].iloc[-1 - lookback])
    return (current - previous) / atr


def _trend_confirmed(df: Optional[pd.DataFrame], side: str, cfg: Dict) -> (bool, List[str]):
    if df is None or len(df) < 80:
        return False, ["30m confirmation missing"]

    close = _last(df, "close")
    atr = _last(df, "ATR_14")
    ema_fast = _last(df, "EMA_Fast")
    ema_slow = _last(df, "EMA_Slow")
    sma50 = _last(df, "SMA_50", ema_slow)
    sma200 = _last(df, "SMA_200", sma50)
    regime = detect_regime(df)
    if close <= 0 or atr <= 0 or ema_fast <= 0 or ema_slow <= 0:
        return False, ["30m confirmation invalid"]

    extension = abs(close - ema_slow) / atr
    if extension > float(cfg.get("max_extension_atr", 99.0)) * 1.25:
        return False, [f"30m overextended {extension:.2f} ATR"]

    if side == "Long":
        if not (ema_fast > ema_slow and close >= sma50):
            return False, ["30m trend not bullish"]
        if cfg.get("require_sma200_alignment", True) and sma200 > 0 and close < sma200:
            return False, ["30m below SMA200"]
        if "Trending Bear" in str(regime):
            return False, ["30m regime bear"]
    else:
        if not (ema_fast < ema_slow and close <= sma50):
            return False, ["30m trend not bearish"]
        if cfg.get("require_sma200_alignment", True) and sma200 > 0 and close > sma200:
            return False, ["30m above SMA200"]
        if "Trending Bull" in str(regime):
            return False, ["30m regime bull"]

    return True, [f"30m confirmed {regime}"]


def _macro_confirmed(df: Optional[pd.DataFrame], side: str, cfg: Dict) -> (bool, List[str]):
    if not cfg.get("require_4h_confirmation", True):
        return True, []
    if df is None or len(df) < 80:
        return False, ["4h confirmation missing"]

    close = _last(df, "close")
    atr = _last(df, "ATR_14")
    ema_fast = _last(df, "EMA_Fast")
    ema_slow = _last(df, "EMA_Slow")
    sma50 = _last(df, "SMA_50", ema_slow)
    regime = detect_regime(df)
    if close <= 0 or atr <= 0 or ema_fast <= 0 or ema_slow <= 0:
        return False, ["4h confirmation invalid"]

    extension = abs(close - ema_slow) / atr
    if extension > float(cfg.get("max_macro_extension_atr", 2.20)):
        return False, [f"4h overextended {extension:.2f} ATR"]

    mode = str(cfg.get("macro_confirmation_mode", "penalty")).lower()
    if side == "Long":
        is_bear = ema_fast < ema_slow and close < sma50
        if is_bear or "Trending Bear" in str(regime):
            return False, [f"4h bearish {regime}"]
        if mode == "hard" and not (ema_fast > ema_slow and close >= sma50):
            return False, ["4h trend not bullish"]
        if not (ema_fast > ema_slow and close >= sma50):
            return True, [f"4h neutral {regime}"]
    else:
        is_bull = ema_fast > ema_slow and close > sma50
        if is_bull or "Trending Bull" in str(regime):
            return False, [f"4h bullish {regime}"]
        if mode == "hard" and not (ema_fast < ema_slow and close <= sma50):
            return False, ["4h trend not bearish"]
        if not (ema_fast < ema_slow and close <= sma50):
            return True, [f"4h neutral {regime}"]
    return True, [f"4h confirmed {regime}"]


def _order_book_stats(order_book: Optional[Dict], side: str, entry: float, atr: float, cfg: Dict) -> Dict:
    stats = {
        "available": False,
        "obi": 0.0,
        "bid_notional": 0.0,
        "ask_notional": 0.0,
        "wall_ratio": 0.0,
        "wall_price": 0.0,
        "entry_adjust": 0.0,
        "reason": "OBI unavailable",
    }
    if not order_book:
        return stats
    bids = order_book.get("bids") or []
    asks = order_book.get("asks") or []
    if not bids or not asks:
        return stats

    def notional(levels):
        total = 0.0
        for price, qty, *_ in levels:
            total += _safe_float(price) * _safe_float(qty)
        return total

    bid_notional = notional(bids)
    ask_notional = notional(asks)
    denom = bid_notional + ask_notional
    obi = (bid_notional - ask_notional) / denom if denom > 0 else 0.0
    stats.update({"available": True, "obi": obi, "bid_notional": bid_notional, "ask_notional": ask_notional})

    against_levels = asks if side == "Long" else bids
    support_levels = bids if side == "Long" else asks
    support_avg = notional(support_levels[:5]) / max(len(support_levels[:5]), 1)
    wall_price, wall_notional = 0.0, 0.0
    for price, qty, *_ in against_levels[:10]:
        price_f = _safe_float(price)
        if atr > 0 and abs(price_f - entry) > atr * 1.5:
            continue
        level_notional = price_f * _safe_float(qty)
        if level_notional > wall_notional:
            wall_price, wall_notional = price_f, level_notional
    wall_ratio = wall_notional / support_avg if support_avg > 0 else 0.0
    stats["wall_ratio"] = wall_ratio
    stats["wall_price"] = wall_price
    stats["reason"] = f"OBI {obi:.2f} wall {wall_ratio:.1f}x"
    if wall_ratio >= float(cfg.get("max_against_wall_ratio", 1.8)):
        adjust = float(cfg.get("wall_adjust_entry_atr", 0.20)) * atr
        stats["entry_adjust"] = -adjust if side == "Long" else adjust
    return stats


def _apply_order_book_gate(score: int, reasons: List[str], side: str, rvol: float, ob: Dict, cfg: Dict) -> (int, List[str], str):
    reasons = list(reasons)
    quality = "Standard"
    min_rvol = float(cfg.get("min_high_probability_rvol", 1.0))
    min_obi = float(cfg.get("min_high_probability_obi", -0.15))
    wall_limit = float(cfg.get("max_against_wall_ratio", 1.8))
    obi = float(ob.get("obi", 0.0))
    wall_ratio = float(ob.get("wall_ratio", 0.0))
    penalty = int(cfg.get("order_book_penalty", 2))
    if not ob.get("available"):
        reasons.append("OBI unavailable")
        if rvol >= min_rvol:
            reasons.append(f"RVOL ok {rvol:.2f}x")
        return score, reasons, "Standard"

    against_obi = (side == "Long" and obi < min_obi) or (side == "Short" and obi > -min_obi)
    if against_obi:
        score -= penalty
        reasons.append(f"OBI against {obi:.2f}")
    else:
        reasons.append(f"OBI ok {obi:.2f}")

    if wall_ratio >= wall_limit:
        score -= penalty
        wall_price = ob.get("wall_price", 0.0)
        reasons.append(f"order book wall {wall_ratio:.1f}x near {wall_price:.8g}")

    if rvol >= min_rvol and not against_obi and wall_ratio < wall_limit:
        quality = "High Probability"
        score += 1
        reasons.append(f"High Probability: RVOL {rvol:.2f}x + order book ok")
    else:
        reasons.append(f"Standard Probability: RVOL {rvol:.2f}x")
    return score, reasons, quality


def _trend_pullback_allowed(
    df: pd.DataFrame,
    confirmation_df: Optional[pd.DataFrame],
    side: str,
    score: int,
    rvol: float,
    adx: float,
    cfg: Dict,
) -> (bool, List[str]):
    reasons = []
    min_score = int(cfg.get("trend_pullback_min_score", cfg.get("min_score", 8)))
    if score < min_score:
        return False, [f"trend pullback score {score} < {min_score}"]

    confirm_source = confirmation_df if confirmation_df is not None else df
    confirm_regime = detect_regime(confirm_source)
    if cfg.get("require_trend_pullback_confirm_regime", True):
        if side == "Long" and "Trending Bull" not in str(confirm_regime):
            return False, [f"trend pullback needs 30m bull, got {confirm_regime}"]
        if side == "Short" and "Trending Bear" not in str(confirm_regime):
            return False, [f"trend pullback needs 30m bear, got {confirm_regime}"]
    reasons.append(f"trend pullback regime {confirm_regime}")

    min_rvol = float(cfg.get("trend_pullback_min_rvol", cfg.get("min_rvol", 1.35)))
    min_adx = float(cfg.get("trend_pullback_min_adx", cfg.get("min_adx", 18.0)))
    if cfg.get("require_trend_pullback_rvol_or_adx", True) and rvol < min_rvol and adx < min_adx:
        return False, [f"trend pullback weak RVOL/ADX {rvol:.2f}x/{adx:.1f}"]
    if rvol >= min_rvol:
        reasons.append(f"trend pullback RVOL {rvol:.2f}x")
    if adx >= min_adx:
        reasons.append(f"trend pullback ADX {adx:.1f}")

    if cfg.get("require_trend_pullback_macd_sign", True):
        macd_h = _last(df, "MACD_h")
        if side == "Long" and macd_h <= 0:
            return False, [f"trend pullback MACD not positive {macd_h:.5f}"]
        if side == "Short" and macd_h >= 0:
            return False, [f"trend pullback MACD not negative {macd_h:.5f}"]
        reasons.append("trend pullback MACD confirmed")

    return True, reasons


def _find_breakout_retest(df: pd.DataFrame, side: str, atr: float, cfg: Dict) -> Optional[Dict]:
    if not cfg.get("enable_breakout_retest", True) or atr <= 0:
        return None

    breakout_lookback = int(cfg.get("breakout_lookback", 24))
    retest_lookback = int(cfg.get("retest_lookback", 6))
    needed = breakout_lookback + retest_lookback + 3
    if len(df) < needed:
        return None

    setup_df = df.iloc[-needed:]
    base = setup_df.iloc[:breakout_lookback]
    recent = setup_df.iloc[breakout_lookback:]
    last = df.iloc[-1]
    buffer = float(cfg.get("breakout_buffer_atr", 0.08)) * atr
    max_retest_distance = float(cfg.get("max_retest_distance_atr", 0.45)) * atr
    min_body = float(cfg.get("min_breakout_body_atr", 0.55)) * atr
    min_rvol = float(cfg.get("min_breakout_rvol", cfg.get("min_rvol", 1.35)))

    if side == "Long":
        level = float(base["high"].max())
        breakout_rows = recent[recent["close"] > level + buffer]
    else:
        level = float(base["low"].min())
        breakout_rows = recent[recent["close"] < level - buffer]
    if breakout_rows.empty:
        return None

    breakout = breakout_rows.iloc[0]
    stats = _candle_stats(breakout)
    breakout_rvol = _safe_float(breakout.get("RVOL"), _rvol(df.iloc[: df.index.get_loc(breakout.name) + 1] if breakout.name in df.index else df))
    if breakout_rvol < min_rvol:
        return None
    if stats["body"] < min_body:
        return None

    if side == "Long":
        if stats["close_pos"] < float(cfg.get("min_breakout_close_position_long", 0.68)):
            return None
        retest_rows = recent[recent["low"] <= level + max_retest_distance]
        if retest_rows.empty or _safe_float(last.get("close")) < level:
            return None
        chase = (_safe_float(last.get("close")) - level) / atr
        if chase > float(cfg.get("max_chase_from_breakout_atr", 1.05)):
            return None
    else:
        if stats["close_pos"] > float(cfg.get("max_breakout_close_position_short", 0.32)):
            return None
        retest_rows = recent[recent["high"] >= level - max_retest_distance]
        if retest_rows.empty or _safe_float(last.get("close")) > level:
            return None
        chase = (level - _safe_float(last.get("close"))) / atr
        if chase > float(cfg.get("max_chase_from_breakout_atr", 1.05)):
            return None

    return {
        "level": level,
        "rvol": breakout_rvol,
        "chase_atr": chase,
        "body_atr": stats["body"] / atr,
    }


def _recent_pullback_touch(df: pd.DataFrame, side: str, lookback: int) -> bool:
    if len(df) < 2:
        return False
    recent = df.iloc[-max(2, lookback):]
    ema_fast = recent["EMA_Fast"] if "EMA_Fast" in recent.columns else None
    ema_slow = recent["EMA_Slow"] if "EMA_Slow" in recent.columns else None
    if ema_fast is None or ema_slow is None:
        return False
    if side == "Long":
        touched_fast = (recent["low"] <= ema_fast).any()
        touched_slow = (recent["low"] <= ema_slow).any()
        reclaimed = _safe_float(df["close"].iloc[-1]) >= min(_last(df, "EMA_Fast"), _last(df, "EMA_Slow"))
    else:
        touched_fast = (recent["high"] >= ema_fast).any()
        touched_slow = (recent["high"] >= ema_slow).any()
        reclaimed = _safe_float(df["close"].iloc[-1]) <= max(_last(df, "EMA_Fast"), _last(df, "EMA_Slow"))
    return bool((touched_fast or touched_slow) and reclaimed)


def _quality_gate(
    df: pd.DataFrame,
    side: str,
    atr: float,
    cfg: Dict,
    setup_type: str = "TREND_PULLBACK",
    breakout: Optional[Dict] = None,
) -> (bool, List[str]):
    reasons = []
    if atr <= 0 or len(df) < 8:
        return False, ["invalid ATR"]

    close = _last(df, "close")
    ema_fast = _last(df, "EMA_Fast")
    ema_slow = _last(df, "EMA_Slow")
    sma50 = _last(df, "SMA_50", ema_slow)
    sma200 = _last(df, "SMA_200", sma50)
    macd_h = _last(df, "MACD_h")
    macd_prev = _safe_float(df["MACD_h"].iloc[-2]) if "MACD_h" in df.columns and len(df) > 1 else macd_h
    stoch_k = _last(df, "stoch_rsi_k", 50)
    stoch_prev = _safe_float(df["stoch_rsi_k"].iloc[-2], stoch_k) if "stoch_rsi_k" in df.columns and len(df) > 1 else stoch_k

    trend_spread = abs(ema_fast - ema_slow) / atr
    min_trend_spread = float(cfg.get("min_trend_spread_atr", 0.0))
    if trend_spread < min_trend_spread:
        return False, [f"weak EMA spread {trend_spread:.2f} ATR"]

    slope = _ema_slope_atr(df, "EMA_Slow", atr)
    min_slope = float(cfg.get("min_ema_slope_atr", 0.0))
    if side == "Long":
        if not (ema_fast > ema_slow and close > ema_slow and close >= sma50):
            return False, ["trend alignment failed"]
        if cfg.get("require_sma200_alignment", True) and sma200 > 0 and close < sma200:
            return False, ["below SMA200"]
        if slope < min_slope:
            return False, [f"EMA slope too flat {slope:.3f} ATR"]
    else:
        if not (ema_fast < ema_slow and close < ema_slow and close <= sma50):
            return False, ["trend alignment failed"]
        if cfg.get("require_sma200_alignment", True) and sma200 > 0 and close > sma200:
            return False, ["above SMA200"]
        if slope > -min_slope:
            return False, [f"EMA slope too flat {slope:.3f} ATR"]
    reasons.append("trend aligned")

    extension = abs(close - ema_slow) / atr
    if extension > float(cfg.get("max_extension_atr", 99.0)):
        return False, [f"overextended {extension:.2f} ATR"]

    if setup_type == "BREAKOUT_RETEST":
        if not breakout:
            return False, ["breakout retest missing"]
        reasons.append(
            f"breakout retest level {breakout['level']:.8g} RVOL {breakout['rvol']:.2f}x"
        )
    else:
        if not _recent_pullback_touch(df, side, int(cfg.get("pullback_lookback", 5))):
            return False, ["no recent EMA pullback reclaim"]
        reasons.append("EMA pullback reclaim")

    stats = _candle_stats(df.iloc[-1])
    if stats["range"] / atr > float(cfg.get("max_last_range_atr", 99.0)):
        return False, [f"last candle range too large {stats['range'] / atr:.2f} ATR"]
    if stats["body"] / atr > float(cfg.get("max_last_body_atr", 99.0)):
        return False, [f"last candle body too large {stats['body'] / atr:.2f} ATR"]

    if side == "Long":
        if stats["upper_wick"] / stats["range"] > float(cfg.get("max_opposite_wick_ratio", 1.0)):
            return False, ["upper wick rejection"]
        if stats["close_pos"] < float(cfg.get("min_close_position_long", 0.0)):
            return False, ["weak candle close"]
        if cfg.get("require_momentum_turn", True) and not (macd_h >= macd_prev and stoch_k >= stoch_prev):
            return False, ["momentum not turning up"]
    else:
        if stats["lower_wick"] / stats["range"] > float(cfg.get("max_opposite_wick_ratio", 1.0)):
            return False, ["lower wick rejection"]
        if stats["close_pos"] > float(cfg.get("max_close_position_short", 1.0)):
            return False, ["weak candle close"]
        if cfg.get("require_momentum_turn", True) and not (macd_h <= macd_prev and stoch_k <= stoch_prev):
            return False, ["momentum not turning down"]
    reasons.append("clean trigger candle")

    return True, reasons


def _score_long(df: pd.DataFrame, rvol: float, adx: float, funding: float, cfg: Dict) -> (int, List[str]):
    score, reasons = 0, []
    close = _last(df, "close")
    ema_fast = _last(df, "EMA_Fast")
    ema_slow = _last(df, "EMA_Slow")
    sma50 = _last(df, "SMA_50", ema_slow)
    stoch_k = _last(df, "stoch_rsi_k", 50)
    macd_h = _last(df, "MACD_h")

    prev = df.iloc[-2]
    last = df.iloc[-1]

    if ema_fast > ema_slow and close >= ema_slow:
        score += 2
        reasons.append("EMA trend up")
    if close >= sma50:
        score += 1
        reasons.append("above SMA50")
    if _safe_float(prev["low"]) <= ema_slow <= close or _safe_float(last["low"]) <= ema_fast <= close:
        score += 2
        reasons.append("pullback reclaim")
    if 35 <= stoch_k <= 82:
        score += 1
        reasons.append("momentum not overheated")
    if macd_h > 0:
        score += 1
        reasons.append("MACD positive")
    if len(df) > 1 and "MACD_h" in df.columns and macd_h >= _safe_float(df["MACD_h"].iloc[-2]):
        score += 1
        reasons.append("MACD improving")
    if len(df) > 1 and "stoch_rsi_k" in df.columns and stoch_k >= _safe_float(df["stoch_rsi_k"].iloc[-2], stoch_k):
        score += 1
        reasons.append("stoch turning up")
    if rvol >= cfg["min_rvol"]:
        score += 1
        reasons.append(f"RVOL {rvol:.2f}x")
    if adx >= cfg["min_adx"]:
        score += 1
        reasons.append(f"ADX {adx:.1f}")
    if abs(funding) <= cfg["cool_funding_abs"]:
        score += 1
        reasons.append("cool funding")
    return score, reasons


def _score_short(df: pd.DataFrame, rvol: float, adx: float, funding: float, cfg: Dict) -> (int, List[str]):
    score, reasons = 0, []
    close = _last(df, "close")
    ema_fast = _last(df, "EMA_Fast")
    ema_slow = _last(df, "EMA_Slow")
    sma50 = _last(df, "SMA_50", ema_slow)
    stoch_k = _last(df, "stoch_rsi_k", 50)
    macd_h = _last(df, "MACD_h")

    prev = df.iloc[-2]
    last = df.iloc[-1]

    if ema_fast < ema_slow and close <= ema_slow:
        score += 2
        reasons.append("EMA trend down")
    if close <= sma50:
        score += 1
        reasons.append("below SMA50")
    if _safe_float(prev["high"]) >= ema_slow >= close or _safe_float(last["high"]) >= ema_fast >= close:
        score += 2
        reasons.append("pullback reject")
    if 18 <= stoch_k <= 65:
        score += 1
        reasons.append("momentum not exhausted")
    if macd_h < 0:
        score += 1
        reasons.append("MACD negative")
    if len(df) > 1 and "MACD_h" in df.columns and macd_h <= _safe_float(df["MACD_h"].iloc[-2]):
        score += 1
        reasons.append("MACD weakening")
    if len(df) > 1 and "stoch_rsi_k" in df.columns and stoch_k <= _safe_float(df["stoch_rsi_k"].iloc[-2], stoch_k):
        score += 1
        reasons.append("stoch turning down")
    if rvol >= cfg["min_rvol"]:
        score += 1
        reasons.append(f"RVOL {rvol:.2f}x")
    if adx >= cfg["min_adx"]:
        score += 1
        reasons.append(f"ADX {adx:.1f}")
    if abs(funding) <= cfg["cool_funding_abs"]:
        score += 1
        reasons.append("cool funding")
    return score, reasons


def _targets(entry: float, atr: float, side: str, cfg: Dict) -> List[float]:
    mults = cfg.get("tp_atr_multipliers") or DEFAULT_CONFIG["tp_atr_multipliers"]
    if side == "Long":
        return [entry + atr * float(m) for m in mults]
    return [entry - atr * float(m) for m in mults]


def _build_signal(
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    ticker: Optional[Dict],
    side: str,
    score: int,
    reasons: List[str],
    cfg: Dict,
    setup_type: str = "TREND_PULLBACK",
    breakout: Optional[Dict] = None,
    order_book: Optional[Dict] = None,
    quality_tier: str = "Standard",
    diagnostics: Optional[Dict] = None,
    btc_bias: Optional[str] = None,
) -> Optional[Dict]:
    close = _last(df, "close")
    atr = _last(df, "ATR_14")
    if atr <= 0:
        atr = _safe_float(calculate_atr(df, length=14))
    if close <= 0 or atr <= 0:
        _record_rejection(diagnostics, "invalid_atr_or_price", symbol)
        return None

    quality_ok, quality_reasons = _quality_gate(df, side, atr, cfg, setup_type, breakout)
    if not quality_ok:
        _record_rejection(diagnostics, "quality_gate", symbol, "; ".join(quality_reasons[:2]))
        return None
    reasons.extend(quality_reasons)

    natr = (atr / close) * 100
    if natr < cfg["min_natr"] or natr > cfg["max_natr"]:
        _record_rejection(diagnostics, "natr_out_of_range", symbol, f"{natr:.2f}%")
        return None

    lookback = int(cfg.get("swing_lookback", 18))
    recent = df.iloc[-lookback:]
    near = float(cfg["entry_atr_near"]) * atr
    far = float(cfg["entry_atr_far"]) * atr
    sl_atr = float(cfg["sl_atr"]) * atr

    breakout_level = _safe_float((breakout or {}).get("level"))
    ob_stats = _order_book_stats(order_book, side, close, atr, cfg)
    if side == "Long":
        if setup_type == "BREAKOUT_RETEST" and breakout_level > 0:
            entry_high = min(close, breakout_level + float(cfg.get("breakout_entry_atr_near", 0.08)) * atr)
            entry_low = max(float(recent["low"].min()), breakout_level - float(cfg.get("breakout_entry_atr_far", 0.55)) * atr)
        else:
            entry_high = close - near
            entry_low = max(float(recent["low"].min()), close - far)
        if entry_low >= entry_high:
            entry_low = entry_high - (atr * 0.35)
        if ob_stats.get("entry_adjust"):
            entry_low += ob_stats["entry_adjust"]
            entry_high += ob_stats["entry_adjust"]
        entry = (entry_low + entry_high) / 2
        swing_sl = float(recent["low"].min()) - atr * 0.20
        if setup_type == "BREAKOUT_RETEST" and breakout_level > 0:
            level_sl = breakout_level - float(cfg.get("sl_breakout_buffer_atr", 0.35)) * atr
            sl = min(swing_sl, level_sl)
        else:
            sl = max(swing_sl, entry_low - sl_atr)
        if sl >= entry:
            _record_rejection(diagnostics, "invalid_sl", symbol, setup_type)
            return None
    else:
        if setup_type == "BREAKOUT_RETEST" and breakout_level > 0:
            entry_low = max(close, breakout_level - float(cfg.get("breakout_entry_atr_near", 0.08)) * atr)
            entry_high = min(float(recent["high"].max()), breakout_level + float(cfg.get("breakout_entry_atr_far", 0.55)) * atr)
        else:
            entry_low = close + near
            entry_high = min(float(recent["high"].max()), close + far)
        if entry_low >= entry_high:
            entry_high = entry_low + (atr * 0.35)
        if ob_stats.get("entry_adjust"):
            entry_low += ob_stats["entry_adjust"]
            entry_high += ob_stats["entry_adjust"]
        entry = (entry_low + entry_high) / 2
        swing_sl = float(recent["high"].max()) + atr * 0.20
        if setup_type == "BREAKOUT_RETEST" and breakout_level > 0:
            level_sl = breakout_level + float(cfg.get("sl_breakout_buffer_atr", 0.35)) * atr
            sl = max(swing_sl, level_sl)
        else:
            sl = min(swing_sl, entry_high + sl_atr)
        if sl <= entry:
            _record_rejection(diagnostics, "invalid_sl", symbol, setup_type)
            return None

    entry_dist = abs(close - entry) / entry * 100
    if entry_dist > float(cfg["max_entry_distance_pct"]):
        _record_rejection(diagnostics, "entry_too_far", symbol, f"{entry_dist:.2f}%")
        return None

    tps = _targets(entry, atr, side, cfg)
    risk = abs(entry - sl)
    if risk <= 0:
        _record_rejection(diagnostics, "invalid_risk", symbol)
        return None

    # Risk-based TP1 override: place the first partial target at tp1_r x risk
    # (default 1R) so the exit ladder scales with the actual stop distance instead
    # of a fixed ATR multiple (RR model validated in backtest_compare_rr.py).
    tp1_r = float(cfg.get("tp1_r", 0.0) or 0.0)
    if tp1_r > 0:
        tps[0] = entry + risk * tp1_r if side == "Long" else entry - risk * tp1_r

    risk_pct = risk / entry * 100
    if risk_pct > float(cfg.get("max_sl_pct", 1.35)):
        _record_rejection(diagnostics, "sl_too_wide", symbol, f"{risk_pct:.2f}%")
        return None

    tp2_reward = abs(tps[1] - entry)
    if tp2_reward / risk < float(cfg.get("min_tp2_r", 0.85)):
        _record_rejection(diagnostics, "tp2_rr_low", symbol, f"{tp2_reward / risk:.2f}R")
        return None

    runner_r = abs(tps[-1] - entry) / risk
    if runner_r < float(cfg.get("min_runner_r", 2.0)):
        _record_rejection(diagnostics, "runner_rr_low", symbol, f"{runner_r:.2f}R")
        return None

    splits = cfg.get("tp_splits") or DEFAULT_CONFIG["tp_splits"]
    if cfg.get("use_trailing_runner", True):
        # Runner model: TP1 partial only; the rest is a Chandelier-trailed runner
        # (SL to BE after TP1, then trail_atr_mult ATR behind the peak) — mirrors
        # the RR 1:5 / trail 3 ATR variant validated in backtest_compare_rr.py.
        tp_plan = [{"price": float(tps[0]), "close_ratio": float(splits[0] if splits else 0.20)}]
    else:
        tp_plan = [
            {"price": float(price), "close_ratio": float(splits[i]) if i < len(splits) else 0.0}
            for i, price in enumerate(tps)
        ]

    total_score = int(score)
    return {
        "Symbol": symbol,
        "Side": side,
        "Timeframe": timeframe,
        "Pattern": setup_type,
        "Mode": "HIGH_WR_SCALP",
        "BTC_Bias": btc_bias or "",
        "Entry": float(entry),
        "Entry_Low": float(min(entry_low, entry_high)),
        "Entry_High": float(max(entry_low, entry_high)),
        "SL": float(sl),
        "TP1": float(tps[0]),
        "TP2": float(tps[1]),
        "TP3": float(tps[2]),
        "TP_Plan": tp_plan,
        "RR": round(runner_r, 2),
        "Tech_Score": total_score,
        "Quant_Score": 0,
        "Deriv_Score": 0,
        "SMC_Score": 0,
        "Basis": 0.0,
        "Z_Score": 0.0,
        "Zeta_Score": 50.0,
        "OBI": float(ob_stats.get("obi", 0.0)),
        "OB_Wall_Ratio": float(ob_stats.get("wall_ratio", 0.0)),
        "OB_Wall_Price": float(ob_stats.get("wall_price", 0.0)),
        "NATR": float(natr),
        "ATR": float(atr),
        "Reason": f"High WR {setup_type.lower()}",
        "Tech_Reasons": ", ".join(reasons),
        "Quant_Reasons": f"NATR {natr:.2f}%, {ob_stats.get('reason', 'OBI unavailable')}",
        "SMC_Reasons": "",
        "Deriv_Reasons": f"funding {_funding_rate(ticker):.5f}",
        "High_WR_Score": total_score,
        "Setup_Type": setup_type,
        "Quality_Tier": quality_tier,
        "Breakout_Level": float(breakout_level) if breakout_level > 0 else 0.0,
        "Move_SL_To_BE_After_TP": int(cfg.get("move_sl_to_be_after_tp", 2)),
        "df": df,
    }


def analyze_high_wr_scalp(
    df: pd.DataFrame,
    ticker: Optional[Dict],
    symbol: str,
    timeframe: str,
    config: Optional[Dict] = None,
    macro_regime: Optional[str] = None,
    confirmation_df: Optional[pd.DataFrame] = None,
    macro_df: Optional[pd.DataFrame] = None,
    order_book: Optional[Dict] = None,
    diagnostics: Optional[Dict] = None,
    btc_bias: Optional[str] = None,
) -> Optional[Dict]:
    cfg = get_high_wr_config(config)
    if not is_enabled_for_timeframe(timeframe, cfg):
        _record_rejection(diagnostics, "disabled_timeframe", symbol, timeframe)
        return None
    allowed = set(cfg.get("allowed_symbols") or [])
    blocked = set(cfg.get("blocked_symbols") or [])
    if allowed and symbol not in allowed:
        _record_rejection(diagnostics, "symbol_not_allowed", symbol)
        return None
    if symbol in blocked:
        _record_rejection(diagnostics, "symbol_blocked", symbol)
        return None
    if df is None or len(df) < 80:
        _record_rejection(diagnostics, "not_enough_candles", symbol, str(len(df) if df is not None else 0))
        return None

    rvol = _rvol(df)
    adx = _last(df, "ADX_14")
    funding = _funding_rate(ticker)
    regime = macro_regime or detect_regime(df)
    atr = _last(df, "ATR_14")
    if atr <= 0:
        atr = _safe_float(calculate_atr(df, length=14))

    ob_long = _order_book_stats(order_book, "Long", _last(df, "close"), atr, cfg) if cfg.get("use_order_book", True) else {}
    ob_short = _order_book_stats(order_book, "Short", _last(df, "close"), atr, cfg) if cfg.get("use_order_book", True) else {}

    long_score, long_reasons = _score_long(df, rvol, adx, funding, cfg)
    short_score, short_reasons = _score_short(df, rvol, adx, funding, cfg)
    long_score, long_reasons, long_quality = _apply_order_book_gate(long_score, long_reasons, "Long", rvol, ob_long, cfg)
    short_score, short_reasons, short_quality = _apply_order_book_gate(short_score, short_reasons, "Short", rvol, ob_short, cfg)
    long_breakout = _find_breakout_retest(df, "Long", atr, cfg)
    short_breakout = _find_breakout_retest(df, "Short", atr, cfg)

    if long_breakout:
        long_score += 3
        long_reasons.append("breakout retest")
    if short_breakout:
        short_score += 3
        short_reasons.append("breakout retest")

    if "Trending Bear" in str(regime):
        long_score -= 2
        long_reasons.append("macro bear penalty")
    if "Trending Bull" in str(regime):
        short_score -= 2
        short_reasons.append("macro bull penalty")

    allow_longs = bool(cfg.get("allow_longs", True))
    allow_shorts = bool(cfg.get("allow_shorts", True))
    if not allow_longs and not allow_shorts:
        _record_rejection(diagnostics, "side_disabled", symbol, "long+short")
        return None
    if not allow_longs:
        long_score = -999
    if not allow_shorts:
        short_score = -999

    confirmation_timeframe = str(cfg.get("confirmation_timeframe", "30m"))
    confirm_source = confirmation_df
    if timeframe == confirmation_timeframe and confirm_source is None:
        confirm_source = df

    candidates = []
    min_score = int(cfg.get("min_score", 7))
    min_breakout_score = int(cfg.get("min_breakout_score", min_score))
    side_rows = [
        ("Long", long_score, long_reasons, long_breakout),
        ("Short", short_score, short_reasons, short_breakout),
    ]
    btc_filter = str(cfg.get("btc_bias_filter", "none")).lower()
    for candidate_side, candidate_score, candidate_reasons, candidate_breakout in side_rows:
        if candidate_side == "Long" and not allow_longs:
            continue
        if candidate_side == "Short" and not allow_shorts:
            continue

        # BTC regime filter: "hard" rejects counter-bias signals outright;
        # "soft" applies a score penalty instead (mirrors macro confirmation).
        if btc_filter != "none" and btc_bias:
            bias = str(btc_bias)
            counter_bias = (candidate_side == "Long" and "Bearish" in bias) or \
                           (candidate_side == "Short" and "Bullish" in bias)
            if counter_bias:
                if btc_filter == "hard":
                    _record_rejection(diagnostics, "btc_bias_counter", symbol, f"{candidate_side} vs {bias}")
                    continue
                candidate_score -= int(cfg.get("btc_bias_penalty", 2))
                candidate_reasons = list(candidate_reasons) + [f"BTC {bias} penalty"]

        if candidate_score < min_score:
            _record_rejection(diagnostics, "score_below_min", symbol, f"{candidate_side} {candidate_score} < {min_score}")
            continue

        if cfg.get("require_30m_confirmation", True):
            confirmed, confirm_reasons = _trend_confirmed(confirm_source, candidate_side, cfg)
            if not confirmed:
                _record_rejection(diagnostics, "confirm_30m_failed", symbol, "; ".join(confirm_reasons[:2]))
                continue
        else:
            confirm_reasons = []
        macro_ok, macro_reasons = _macro_confirmed(macro_df, candidate_side, cfg)
        if not macro_ok:
            _record_rejection(diagnostics, "macro_4h_failed", symbol, "; ".join(macro_reasons[:2]))
            continue
        macro_penalty = 0
        if macro_reasons and any("neutral" in str(reason).lower() for reason in macro_reasons):
            macro_penalty = int(cfg.get("macro_penalty", 1))
            candidate_score -= macro_penalty
            candidate_reasons = list(candidate_reasons) + [f"4h neutral penalty -{macro_penalty}"]
            if candidate_score < min_score:
                _record_rejection(diagnostics, "score_below_min_after_4h", symbol, f"{candidate_side} {candidate_score} < {min_score}")
                continue

        candidate_quality = long_quality if candidate_side == "Long" else short_quality

        if candidate_breakout and candidate_score >= min_breakout_score:
            reasons = list(candidate_reasons) + list(confirm_reasons) + list(macro_reasons) + [f"regime {regime}"]
            candidates.append((0, -candidate_score, candidate_side, candidate_score, reasons, candidate_breakout, "BREAKOUT_RETEST", candidate_quality))

        allowed_pullback, pullback_reasons = _trend_pullback_allowed(
            df,
            confirm_source,
            candidate_side,
            candidate_score,
            rvol,
            adx,
            cfg,
        )
        if allowed_pullback:
            reasons = list(candidate_reasons) + list(confirm_reasons) + list(macro_reasons) + pullback_reasons + [f"regime {regime}"]
            candidates.append((1, -candidate_score, candidate_side, candidate_score, reasons, None, "TREND_PULLBACK", candidate_quality))
        else:
            _record_rejection(diagnostics, "pullback_gate_failed", symbol, "; ".join(pullback_reasons[:2]))

    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    for _, _, side, score, reasons, breakout, setup_type, quality_tier in candidates:
        signal = _build_signal(symbol, timeframe, df, ticker, side, score, reasons, cfg, setup_type, breakout, order_book, quality_tier, diagnostics)
        if signal:
            return signal
    return None
