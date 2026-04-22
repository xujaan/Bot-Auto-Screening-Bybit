"""
Tujuan: Mengelola koneksi database secara eksklusif menggunakan SQLite untuk performa ringan dan penghapusan dependensi yang berat.
Caller: main.py, auto_trades.py, telegram_listener.py, execution.py
Dependensi: sqlite3, modules.config_loader
Main Functions: init_db(), get_conn(), release_conn(), migrate_schema(), get_dict_cursor(), get_active_cex(), set_active_cex()
Side Effects: Membaca dan menulis ke file futurabot.sqlite di direktori lokal.
"""

import sqlite3
import os
from modules.config_loader import CONFIG

DB_FILE = 'futurabot.sqlite'

class SQLiteCursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, query, params=None):
        # Convert PostgreSQL parameter bindings '%s' to sqlite '?' safely
        query = query.replace('%s', '?')
        
        if params is not None:
            self.cursor.execute(query, params)
        else:
            self.cursor.execute(query)
            
        self.description = self.cursor.description
        return self

    def fetchone(self):
        row = self.cursor.fetchone()
        if not row: return None
        if isinstance(row, sqlite3.Row): return dict(row)
        return row
        
    def fetchall(self):
        rows = self.cursor.fetchall()
        if not rows: return []
        if rows and isinstance(rows[0], sqlite3.Row): return [dict(r) for r in rows]
        return rows

    def __getattr__(self, name):
        return getattr(self.cursor, name)

class SQLiteConnWrapper:
    def __init__(self, conn):
        self.conn = conn
    
    def cursor(self, cursor_factory=None):
        if cursor_factory == 'dict':
            self.conn.row_factory = sqlite3.Row
        else:
            self.conn.row_factory = None
        cur = self.conn.cursor()
        return SQLiteCursorWrapper(cur)
        
    def commit(self): self.conn.commit()
    def rollback(self): self.conn.rollback()
    def close(self): self.conn.close()


def init_db():
    conn = get_conn()
    migrate_schema(conn)
    release_conn(conn)
    print("✅ SQLite Connected & Schema Synced.")

def get_conn():
    # Use check_same_thread=False for easy threading
    # Apply WAL mode for better concurrency performance with writes
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return SQLiteConnWrapper(conn)

def release_conn(conn):
    try:
        conn.close()
    except Exception:
        pass

def get_dict_cursor(conn):
    return conn.cursor(cursor_factory='dict')

def migrate_schema(conn):
    cur = conn.cursor()
    
    required_columns = {
        "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "symbol": "VARCHAR(100)", 
        "side": "VARCHAR(10)", 
        "timeframe": "VARCHAR(5)", 
        "pattern": "VARCHAR(50)",
        "entry_price": "DECIMAL", 
        "sl_price": "DECIMAL", 
        "tp1": "DECIMAL", "tp2": "DECIMAL", "tp3": "DECIMAL",
        "rr": "DECIMAL",
        "status": "VARCHAR(50) DEFAULT 'Waiting Entry'", 
        "reason": "TEXT",
        "tech_score": "INT", 
        "quant_score": "INT", 
        "deriv_score": "INT", 
        "smc_score": "INT DEFAULT 0",
        "z_score": "DECIMAL DEFAULT 0", 
        "zeta_score": "DECIMAL DEFAULT 0", 
        "obi": "DECIMAL DEFAULT 0",
        "basis": "DECIMAL", 
        "btc_bias": "VARCHAR(50)",
        "tech_reasons": "TEXT",
        "quant_reasons": "TEXT",
        "deriv_reasons": "TEXT",
        "smc_reasons": "TEXT",
        "natr": "DECIMAL DEFAULT 0",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP", 
        "entry_hit_at": "TIMESTAMP", 
        "closed_at": "TIMESTAMP", 
        "exit_price": "DECIMAL", 
        "message_id": "VARCHAR(50)", 
        "channel_id": "VARCHAR(50)"
    }

    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades';")
        table_exists = cur.fetchone() is not None
            
        if not table_exists:
            print("🆕 Table 'trades' not found. Creating fresh...")
            cols = [f"{k} {v}" for k, v in required_columns.items()]
            query = f"CREATE TABLE trades ({', '.join(cols)});"
            cur.execute(query)
            print("✅ Table 'trades' created successfully.")
            
        else:
            print("🔍 Checking 'trades' schema for missing columns...")
            cur.execute("PRAGMA table_info('trades');")
            existing_cols = {row['name'] if isinstance(row, dict) else row[1] for row in cur.fetchall()}
                
            missing_cols = []
            for col, dtype in required_columns.items():
                if col not in existing_cols:
                    clean_type = dtype.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "INTEGER")
                    missing_cols.append(col + " " + clean_type)

            if missing_cols:
                for mc in missing_cols:
                    cur.execute(f"ALTER TABLE trades ADD COLUMN {mc};")
                print("✅ Migration Complete.")
            else:
                print("✅ Schema is up to date.")

        cur.execute("CREATE TABLE IF NOT EXISTS bot_state (key_name VARCHAR(50) PRIMARY KEY, value_text TEXT);")
        cur.execute("CREATE TABLE IF NOT EXISTS system_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, type VARCHAR(50), message TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
        cur.execute("CREATE TABLE IF NOT EXISTS favorites_list (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol VARCHAR(50), side VARCHAR(20), timeframe VARCHAR(10), pattern VARCHAR(50), entry_price DECIMAL, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
        conn.commit()

        # Check default active_cex
        cur.execute("SELECT value_text FROM bot_state WHERE key_name = 'active_cex'")
        if not cur.fetchone():
            cur.execute("INSERT INTO bot_state (key_name, value_text) VALUES ('active_cex', 'bybit')")
            conn.commit()

    except Exception as e:
        print(f"❌ Migration Failed: {e}")
        conn.rollback()

def get_active_signals():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT symbol, timeframe 
            FROM trades 
            WHERE status NOT LIKE '%Closed%' 
            AND status NOT LIKE '%Cancelled%'
            AND status NOT LIKE '%Stop Loss%'
        """)
        return {(r['symbol'] if isinstance(r, dict) else r[0], r['timeframe'] if isinstance(r, dict) else r[1]) for r in cur.fetchall()}
    except Exception as e:
        print(f"⚠️ Error fetching active signals: {e}")
        return set()
    finally:
        release_conn(conn)

def get_risk_config():
    conn = get_conn()
    defaults = {
        'auto_trade': 'off',
        'total_trading_capital_usdt': '10.0',
        'max_concurrent_trades': '2',
        'max_leverage_limit': '50'
    }
    try:
        cur = conn.cursor()
        cur.execute("SELECT key_name, value_text FROM bot_state WHERE key_name IN ('auto_trade', 'total_trading_capital_usdt', 'max_concurrent_trades', 'max_leverage_limit')")
        rows = cur.fetchall()
        for row in rows:
            k = row['key_name'] if isinstance(row, dict) else row[0]
            v = row['value_text'] if isinstance(row, dict) else row[1]
            defaults[k] = v
    except: pass
    finally: release_conn(conn)
    return {
        'auto_trade': defaults.get('auto_trade', 'off') == 'on',
        'total_trading_capital_usdt': float(defaults.get('total_trading_capital_usdt', 10)),
        'max_concurrent_trades': int(defaults.get('max_concurrent_trades', 2)),
        'max_leverage_limit': int(defaults.get('max_leverage_limit', 50))
    }

def set_risk_config(key, value):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO bot_state (key_name, value_text) VALUES (%s, %s) ON CONFLICT(key_name) DO UPDATE SET value_text = excluded.value_text", (key, str(value)))
        conn.commit()
        return True
    except: return False
    finally: release_conn(conn)

def get_active_cex():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT value_text FROM bot_state WHERE key_name = 'active_cex'")
        row = cur.fetchone()
        return row['value_text'] if isinstance(row, dict) and row['value_text'] else (row[0] if row else 'bybit')
    except: 
        return 'bybit'
    finally: 
        release_conn(conn)

def set_active_cex(platform_name):
    conn = get_conn()
    try:
        cur = conn.cursor()
        platform = platform_name.lower()
        if platform not in ['bybit', 'binance', 'bitget']:
            return False
        cur.execute("INSERT INTO bot_state (key_name, value_text) VALUES ('active_cex', %s) ON CONFLICT(key_name) DO UPDATE SET value_text = excluded.value_text", (platform,))
        conn.commit()
        return True
    except Exception as e:
        print("Error saving active CEX:", e)
        return False
    finally: 
        release_conn(conn)

def log_action(log_type, message):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO system_logs (type, message) VALUES (?, ?)", (str(log_type), str(message)))
        conn.commit()
    except Exception as e:
        print(f"Failed to write log: {e}")
    finally:
        release_conn(conn)