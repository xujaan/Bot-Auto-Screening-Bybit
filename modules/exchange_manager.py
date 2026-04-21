"""
Tujuan: Manajemen instansiasi API CCXT secara dinamis lintas bursa (Binance, Bitget, Bybit).
Caller: main.py, auto_trades.py, telegram_listener.py
Dependensi: ccxt, modules.config_loader, modules.database
Main Functions: get_current_exchange()
Side Effects: Membaca database untuk mendapatkan active_cex
"""

import ccxt
from modules.config_loader import CONFIG
from modules.database import get_active_cex

_EXCHANGE_CACHE = {
    'platform': None,
    'instance': None
}

def get_current_exchange(force_reload=False):
    """
    Mengambil instansi CCXT untuk CEX yang sedang aktif di database.
    Mendukung caching agar tidak merekonstruksi instance jika tidak ada perubahan platform.
    """
    platform = get_active_cex().lower()
    
    if not force_reload and _EXCHANGE_CACHE['platform'] == platform and _EXCHANGE_CACHE['instance'] is not None:
        return _EXCHANGE_CACHE['instance']

    api_keys = CONFIG['api'].get(platform, {})
    key = api_keys.get('key', '')
    secret = api_keys.get('secret', '')

    config_opts = {
        'apiKey': key,
        'secret': secret,
        'enableRateLimit': True,
        'options': {'defaultType': 'swap', 'adjustForTimeDifference': True}
    }

    try:
        if platform == 'binance':
            # Binance uses marginMode and defaultType future
            config_opts['options']['defaultType'] = 'future'
            exchange = ccxt.binance(config_opts)
        elif platform == 'bitget':
            exchange = ccxt.bitget(config_opts)
        else: # Default bybit
            exchange = ccxt.bybit(config_opts)
            
        _EXCHANGE_CACHE['platform'] = platform
        _EXCHANGE_CACHE['instance'] = exchange
        return exchange
    except Exception as e:
        print(f"FAILED to init CCXT for {platform}: {e}")
        return None
