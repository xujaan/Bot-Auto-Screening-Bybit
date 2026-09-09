import numpy as np
from scipy.stats import linregress

from modules.config_loader import CONFIG

def get_slope(series):
    try: return linregress(np.arange(len(series)), np.array(series))[0]
    except: return 0

def _funding_thresholds():
    """
    Funding rate scale: normal perp funding is ~0.0001 (0.01%), hot is
    ~0.001 (0.1%). Thresholds live in config under 'derivatives'.
    """
    cfg = CONFIG.get("derivatives", {}) or {}
    return {
        "reject": float(cfg.get("funding_reject_threshold", 0.001)),
        "cool": float(cfg.get("funding_cool_threshold", 0.0008)),
    }

def analyze_derivatives(df, ticker, side):
    """
    Analyzes derivative metrics (Funding, Basis, CVD Divergence).
    """
    score = 1
    reasons = []
    th = _funding_thresholds()
    
    # 1. Funding Rate Check
    funding = float(ticker.get('info', {}).get('fundingRate', 0))
    if side == "Long" and funding > th["reject"]:
        return False, 0, [f"Funding Hot ({funding * 100:.3f}% > {th['reject'] * 100:.2f}%)"]
    if side == "Short" and funding < -th["reject"]:
        return False, 0, [f"Funding Squeeze Risk ({funding * 100:.3f}% < -{th['reject'] * 100:.2f}%)"]
    
    if abs(funding) < th["cool"]:
        score += 1
        reasons.append(f"Cool Funding ({funding * 100:.3f}%)")

    # 2. Basis Calculation
    mark = float(ticker.get('last', 0))
    index = float(ticker.get('info', {}).get('indexPrice', mark))
    
    # 3. CVD Calculation (Defensive Fix)
    # If 'CVD' is missing, we calculate it right here.
    if 'CVD' not in df.columns:
        df['delta'] = np.where(df['close'] > df['open'], df['volume'], -df['volume'])
        df['CVD'] = df['delta'].cumsum()

    # 4. Divergence Analysis (Price Slope vs CVD Slope)
    # Look at the last 10 candles
    p_slope = get_slope(df['close'].iloc[-10:])
    cvd_slope = get_slope(df['CVD'].iloc[-10:])
    
    # Bearish Divergence: Price Rising, CVD Falling (Sellers absorbing)
    if p_slope > 0 and cvd_slope < 0:
        if side == "Short":
            score += 2
            reasons.append("Bear CVD Div")
        elif side == "Long":
            score -= 2 # Penalty for longing into selling pressure

    # Bullish Divergence: Price Falling, CVD Rising (Buyers absorbing)
    elif p_slope < 0 and cvd_slope > 0:
        if side == "Long":
            score += 2
            reasons.append("Bull CVD Div")
        elif side == "Short":
            score -= 2 # Penalty for shorting into buying pressure

    return True, score, reasons