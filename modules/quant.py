import numpy as np
import pandas_ta_classic as ta
from scipy.special import expit

def calculate_z_score(series, window=20):
    mean = series.rolling(window=window).mean()
    std = series.rolling(window=window).std()
    if std is None or len(std) == 0:
        return series * 0.0
    # Guard: zero or undefined std (e.g. flat volume) would produce inf/NaN
    # z-scores that could inflate the score; map those values to 0 instead.
    safe_std = std.where(std > 0)
    z = (series - mean) / safe_std
    return z.fillna(0.0)

def calculate_zeta_field(df, basis):
    try:
        natr = ta.natr(df['high'], df['low'], df['close'], length=14)
        v_term = expit(natr.iloc[-1])
        cmf = ta.cmf(df['high'], df['low'], df['close'], df['volume'], length=20)
        f_term = (cmf.iloc[-1] + 1) / 2
        cci = ta.cci(df['high'], df['low'], df['close'], length=20)
        c_term = expit(cci.iloc[-1] / 100)
        b_term = 1.0 - min(abs(basis) * 100, 1.0)
        rsi = ta.rsi(df['close'], length=14)
        s_term = rsi.iloc[-1] / 100.0
        roc = ta.roc(df['close'], length=9)
        a_term = expit(roc.iloc[-1])
        rvol = df['volume'].iloc[-1] / df['volume'].rolling(20).mean().iloc[-1]
        h_term = min(rvol / 5.0, 1.0)
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)
        t_term = adx['ADX_14'].iloc[-1] / 100.0
        
        zeta_raw = (v_term + f_term + c_term + b_term + s_term + a_term + h_term + t_term)
        zeta_score = (zeta_raw / 8.0) * 100.0
        
        score_add, reason = 0, ""
        if zeta_score > 70: score_add, reason = 1, f"ζ-High ({zeta_score:.1f})"
        elif zeta_score < 30: score_add, reason = 1, f"ζ-Low ({zeta_score:.1f})"
        return zeta_score, score_add, reason
    except: return 50.0, 0, ""

def calculate_obi(ticker, order_book=None):
    """
    Order Book Imbalance (OBI): (bid_notional - ask_notional) / total notional.

    Prefers exchange-provided bid/ask volume when present; falls back to
    computing from an order book depth snapshot, mirroring the HIGH_WR_SCALP
    approach (swap tickers often leave bidVolume/askVolume empty).
    """
    try:
        bid, ask = ticker.get('bidVolume'), ticker.get('askVolume')
        if bid is not None and ask is not None:
            bid, ask = float(bid), float(ask)
            if bid + ask > 0:
                return (bid - ask) / (bid + ask)
    except Exception:
        pass

    if order_book:
        try:
            bids = order_book.get('bids') or []
            asks = order_book.get('asks') or []
            if bids and asks:
                bid_notional = sum(float(p) * float(q) for p, q, *_ in bids)
                ask_notional = sum(float(p) * float(q) for p, q, *_ in asks)
                total = bid_notional + ask_notional
                if total > 0:
                    return (bid_notional - ask_notional) / total
        except Exception:
            pass
    return 0.0

def calculate_metrics(df, ticker, order_book=None):
    mark = float(ticker.get('last', 0))
    index = float(ticker.get('info', {}).get('indexPrice', mark))
    basis = (mark - index) / index if index > 0 else 0
    
    df['Vol_SMA'] = ta.sma(df['volume'], length=20)
    df['RVOL'] = df['volume'] / df['Vol_SMA']
    df['Vol_Z'] = calculate_z_score(df['volume'], window=20)
    z_score = df['Vol_Z'].iloc[-1]
    
    zeta_score, zeta_bonus, zeta_reason = calculate_zeta_field(df, basis)
    obi = calculate_obi(ticker, order_book)
    
    score, reasons = 2, []
    if df['RVOL'].iloc[-1] > 5.0: score += 1; reasons.append("Nuclear RVOL")
    elif df['RVOL'].iloc[-1] > 2.0: reasons.append("Valid RVOL")
    
    if z_score > 3.0: score += 2; reasons.append(f"Z-Score ({z_score:.1f})")
    if zeta_bonus > 0: score += zeta_bonus; reasons.append(zeta_reason)
    if abs(obi) > 0.3: score += 1; reasons.append(f"OBI {obi:.2f}")

    return df, basis, z_score, zeta_score, obi, score, reasons

def check_fakeout(df, min_rvol):
    if df['RVOL'].iloc[-1] < min_rvol: return False, "Fakeout (Low Vol)"
    return True, ""