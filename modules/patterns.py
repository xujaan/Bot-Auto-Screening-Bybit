import numpy as np
from scipy.signal import argrelextrema
from scipy.stats import linregress
from modules.config_loader import CONFIG

def get_slope(values):
    try: return linregress(np.arange(len(values)), values)[0]
    except: return 0.0

def get_normalized_slope(values):
    """
    Slope of the latest pivot values normalized by their average price.

    The pattern thresholds (e.g. 0.0005) are meant as per-bar price
    fractions; normalizing by the average price makes them scale-invariant
    across symbols of very different price levels ($0.01 memecoins vs $60k
    BTC), which raw price slopes are not.
    """
    slope = get_slope(values)
    avg = float(np.mean(values))
    if avg <= 0:
        return 0.0
    return slope / avg

def check_alignment(values):
    if len(values) < 2: return False
    tol = CONFIG['patterns'].get('tolerance', 0.015)
    avg = np.mean(values)
    return all(abs(v - avg) / avg < tol for v in values)

def _double_bottom_ok(valleys, valley_pos, peaks, peak_pos):
    """
    A double bottom needs two near-equal lows separated by a real bounce:
    at least one peak printed between the two bottoms, above their level.
    """
    if not check_alignment(valleys[-2:]):
        return False
    v1_idx, v2_idx = valley_pos[-2], valley_pos[-1]
    bounce = [p for p, pos in zip(peaks, peak_pos) if v1_idx < pos < v2_idx]
    return bool(bounce) and max(bounce) > max(valleys[-2], valleys[-1])

def _double_top_ok(peaks, peak_pos, valleys, valley_pos):
    """
    A double top needs two near-equal highs separated by a real dip:
    at least one valley printed between the two tops, below their level.
    """
    if not check_alignment(peaks[-2:]):
        return False
    p1_idx, p2_idx = peak_pos[-2], peak_pos[-1]
    dip = [v for v, pos in zip(valleys, valley_pos) if p1_idx < pos < p2_idx]
    return bool(dip) and min(dip) < min(peaks[-2], peaks[-1])

def find_pattern(df):
    if len(df) < 50: return None
    df_idx = df.reset_index(drop=True)
    n = 3
    # Positional indices double as chronological order (index == position).
    ext_lo = argrelextrema(df_idx.low.values, np.less_equal, order=n)[0]
    ext_hi = argrelextrema(df_idx.high.values, np.greater_equal, order=n)[0]
    peaks = df_idx.high.values[ext_hi]
    valleys = df_idx.low.values[ext_lo]
    if len(peaks) < 3 or len(valleys) < 3: return None

    enabled = CONFIG['patterns']
    s_high, s_low = get_normalized_slope(peaks[-4:]), get_normalized_slope(valleys[-4:])

    if enabled.get('ascending_triangle') and abs(s_high) < 0.0005 and s_low > 0.0002: return 'ascending_triangle'
    if enabled.get('descending_triangle') and abs(s_low) < 0.0005 and s_high < -0.0002: return 'descending_triangle'
    if enabled.get('double_bottom') and _double_bottom_ok(valleys, ext_lo, peaks, ext_hi): return 'double_bottom'
    if enabled.get('double_top') and _double_top_ok(peaks, ext_hi, valleys, ext_lo): return 'double_top'
    if enabled.get('bull_flag') and -0.002 < s_high < -0.0002 and -0.002 < s_low < -0.0002: return 'bull_flag'
    if enabled.get('bullish_rectangle') and abs(s_high) < 0.0005 and abs(s_low) < 0.0005: return 'bullish_rectangle'
    return None