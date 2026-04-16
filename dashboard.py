import streamlit as st
import pandas as pd
import json
import warnings
warnings.filterwarnings('ignore') # Suppress pandas future warnings

# Import database and config from existing modules
from modules.database import get_conn, release_conn
from modules.config_loader import CONFIG
import plotly.express as px

st.set_page_config(page_title="Bybit Quant Dashboard", page_icon="📈", layout="wide")

def load_data(query, params=None):
    conn = get_conn()
    try:
        df = pd.read_sql_query(query, conn, params=params)
        return df
    except Exception as e:
        st.error(f"Database Error: {e}")
        return pd.DataFrame()
    finally:
        release_conn(conn)

def _sanitize_config(config_dict):
    """Hide sensitive API keys from the web view"""
    safe_config = config_dict.copy()
    if 'api' in safe_config:
        safe_api = safe_config['api'].copy()
        for k in ['bybit_key', 'bybit_secret', 'discord_webhook', 'discord_live_webhook', 'discord_dashboard_webhook']:
            if k in safe_api and safe_api[k]:
                safe_api[k] = "********"
        safe_config['api'] = safe_api
    
    # Hide DB password
    if 'database' in safe_config:
        safe_db = safe_config['database'].copy()
        if 'password' in safe_db and safe_db['password']:
            safe_db['password'] = "********"
        safe_config['database'] = safe_db
        
    return safe_config

def main():
    st.sidebar.markdown("# ⚡ Bybit Algo Dashboard")
    st.sidebar.title("🤖 Quant Bot v8")
    
    # Inject styling
    st.markdown("""
        <style>
        .metric-container { background: #f0f2f6; border-radius: 8px; padding: 10px; }
        </style>
    """, unsafe_allow_html=True)
    
    st.sidebar.markdown("### 🖥️ Engine Status")
    st.sidebar.markdown(f"**Timezone:** `{CONFIG['system']['timezone']}`")
    st.sidebar.markdown(f"**Max Threads:** `{CONFIG['system']['max_threads']}`")
    
    from modules.database import get_risk_config
    try:
        r_cfg = get_risk_config()
        st.sidebar.markdown("### 🚦 Risk Profile (Telegram API)")
        st.sidebar.markdown(f"**Auto Trade:** `{'🟢 ON' if r_cfg['auto_trade'] else '🔴 OFF'}`")
        if r_cfg['auto_trade']:
            st.sidebar.markdown(f"**Total Capital:** `${r_cfg['total_trading_capital_usdt']}`")
            st.sidebar.markdown(f"**Max Slots:** `{r_cfg['max_concurrent_trades']} pairs`")
    except: pass

    menu = st.sidebar.radio("Navigation", ["🔴 Live Monitoring", "📋 Trade History", "📊 Analytics", "⚙️ Configuration"])

    if menu == "🔴 Live Monitoring":
        st.title("🔴 Live & Waiting Trades")
        st.markdown("Papan pantau untuk semua order yang sedang menunggu area *Entry* dan posisi yang sedang *Aktif*.")
        
        query = """
            SELECT symbol, side, timeframe, pattern, entry_price, sl_price, tp3 as tp_max, 
            status, tech_score, quant_score, created_at 
            FROM trades 
            WHERE status NOT LIKE '%%Closed%%' 
            AND status NOT LIKE '%%Cancelled%%' 
            AND status NOT LIKE '%%Stop Loss%%'
            ORDER BY created_at DESC
        """
        df = load_data(query)
        
        if not df.empty:
            # Metrics
            col1, col2, col3 = st.columns(3)
            col1.metric("📌 Active/Waiting Signals", len(df))
            
            longs = len(df[df['side'] == 'Long'])
            col2.metric("📈 Long / 📉 Short", f"{longs} / {len(df) - longs}")
            
            recent = df['created_at'].max()
            col3.metric("⏱️ Last Signal", recent.strftime('%H:%M:%S') if pd.notnull(recent) else "-")
            
            # Format Dataframe
            st.markdown("### 📋 Tabel Signal Berjalan")
            def color_side(val):
                return 'background-color: rgba(46, 189, 133, 0.2)' if val == 'Long' else 'background-color: rgba(246, 70, 93, 0.2)'
            styled_df = df.style.map(color_side, subset=['side']).format({
                'entry_price': '{:.5f}', 'sl_price': '{:.5f}', 'tp_max': '{:.5f}'
            })
            st.dataframe(styled_df, use_container_width=True, hide_index=True)
        else:
            st.success("🟢 Bot sedang berjaga (Idle). Tidak ada antrean posisi / Limit order saat ini.")
            st.markdown("*(Menunggu konfirmasi scan sinyal teknikal berikutnya...)*")

    elif menu == "📋 Trade History":
        st.title("📋 Riwayat Trade (Closed)")
        
        query = """
            SELECT symbol, side, timeframe, pattern, entry_price, status, closed_at 
            FROM trades 
            WHERE status LIKE '%%Closed%%' 
            OR status LIKE '%%Cancelled%%' 
            OR status LIKE '%%Stop Loss%%' 
            ORDER BY closed_at DESC 
            LIMIT 100
        """
        df = load_data(query)
        
        if not df.empty:
            st.dataframe(df, use_container_width=True, hide_index=True)
            
            st.download_button(
                label="📥 Download CSV",
                data=df.to_csv(index=False).encode('utf-8'),
                file_name='closed_trades.csv',
                mime='text/csv',
            )
        else:
            st.info("Riwayat trade masih kosong.")
            
    elif menu == "📊 Analytics":
        st.title("📊 Performa Bot")
        
        query_stats = """
            SELECT status
            FROM trades 
            WHERE status LIKE '%%Closed%%' 
            OR status LIKE '%%Stop Loss%%'
        """
        df_stats = load_data(query_stats)
        
        if not df_stats.empty:
            win_count = len(df_stats[df_stats['status'].str.contains('TP', case=False, na=False)])
            loss_count = len(df_stats[df_stats['status'].str.contains('Stop Loss', case=False, na=False)])
            total = win_count + loss_count
            
            if total > 0:
                win_rate = (win_count / total) * 100
                st.markdown(f"### Win Rate: **{win_rate:.1f}%**")
                
                # Chart
                fig = px.pie(values=[win_count, loss_count], names=['Take Profit (Win)', 'Stop Loss (Loss)'], 
                             title='Rasio Kemenangan Total', color_discrete_sequence=['#2ebd85', '#f6465d'])
                st.plotly_chart(fig)
            else:
                st.warning("Belum ada trade yang menyentuh TP atau SL untuk dihitung statistiknya.")
        else:
            st.info("Data performa belum tersedia.")

    elif menu == "⚙️ Configuration":
        st.title("⚙️ Current System Configuration")
        st.info("💡 **Mode Read-Only**: Halaman ini murni untuk meninjau konfigurasi bot yang sedang berjalan. Jika Anda ingin mengubah angka parameter (seperti fibonacci, risk reward, webhook), ubahlah file `config.json` di PC Anda langsung.")
        
        safe_json = _sanitize_config(CONFIG)
        st.json(safe_json)

if __name__ == "__main__":
    main()
