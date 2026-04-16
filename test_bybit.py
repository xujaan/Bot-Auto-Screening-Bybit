import ccxt
import json

def test_connection():
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
    except Exception as e:
        print(f"❌ Failed to load config.json: {e}")
        return

    key = config.get('api', {}).get('bybit_key', '')
    secret = config.get('api', {}).get('bybit_secret', '')
    
    print(f"🔌 Using API Key: {key[:5]}...{key[-3:] if len(key) > 8 else ''}")

    exchange = ccxt.bybit({
        'apiKey': key,
        'secret': secret,
        'options': {'defaultType': 'swap'},
        'enableRateLimit': True,
    })

    print("\n[1/2] Menguji Endpoint Publik (Market Data)...")
    try:
        markets = exchange.load_markets()
        print(f"✅ Sukses! Berhasil memuat {len(markets)} data pasar.")
    except ccxt.NetworkError as e:
        print("❌ GAGAL (NetworkError): Tidak bisa terhubung ke server Bybit.")
        print("💡 INDIKASI: Internet Anda memblokir Bybit (Sering terjadi di provider Indihome/Telkomsel). Gunakan VPN atau VPS luar negeri.")
        print(f"Detail teknis: {e}")
        return
    except Exception as e:
        print(f"❌ GAGAL: {e}")
        return

    print("\n[2/2] Menguji Endpoint Privat (Authentication/Validasi Key)...")
    try:
        balance = exchange.fetch_balance()
        usdt_free = balance.get('USDT', {}).get('free', 0)
        print("✅ Sukses! Kunci API Valid dan Berfungsi.")
        print(f"💰 Saldo USDT Anda: {usdt_free}")
    except ccxt.AuthenticationError as e:
        print("❌ GAGAL (AuthenticationError): Kunci API ditolak oleh Bybit.")
        print("💡 INDIKASI: Salah copy-paste, atau API Key belum diberi izin (Read-Write).")
        print(f"Detail teknis: {e}")
    except Exception as e:
        print(f"❌ GAGAL: {e}")

if __name__ == "__main__":
    test_connection()
