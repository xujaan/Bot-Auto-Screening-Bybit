# Futurabot (Multi-CEX)

An institutional-grade, modular cryptocurrency trading bot previously designed for Bybit and now expanded to support **Binance, Bitget, and Bybit**. This bot leverages a hybrid analysis engine combining geometric pattern recognition, Smart Money Concepts (SMC), quantitative metrics (RVOL, OBI, VPIN), and derivative market data (CVD, Open Interest, Funding Rates) to identify high-probability setups.

It features a robust production architecture with a lightweight **SQLite** persistence layer, dynamic CCXT API execution, Telegram bot control hub, and a local Web Dashboard.

## 🚀 Key Features

### 🧠 Advanced Analysis Engine

- **Smart Money Concepts (SMC):**
  - **Order Blocks:** Identifies Bullish (Demand) and Bearish (Supply) zones. Skips trades entering opposing zones.
  - **Market Structure:** Filters entries based on Higher Highs/Lows and Lower Highs/Lows.
  - **Validation:** Detects Break of Structure (BOS) and Change of Character (CHoCH) for confirmation.
- **Multi-Timeframe Confluence (MTC):** Caches Macro Market Regimes (1D, 4H) and aggressively rejects micro-timeframe signals (1H) that oppose the overarching macro trend, effectively filtering out fakeouts.
- **Geometric Pattern Recognition:** Automatically detects Double Tops/Bottoms, Bull/Bear Flags, Ascending Triangles, and Rectangles.
- **Market Context Awareness:**
  - **Regime Detection:** Uses ADX alongside SMA-50/200 hierarchy to classify the environment into Trending Bull, Trending Bear, or Volatile Expansion.
  - **Volatility Squeeze:** Implements TTM Squeeze logic (Bollinger Bands nested inside Keltner Channels) to reward breakout setups that are about to detonate.
- **Divergence Logic:** Scans for divergences on Stochastic RSI and CVD (Cumulative Volume Delta).

### 🛡️ Risk Management & Multi-CEX Auto Trade

- **Dynamic Active CEX:** Allows swapping the active execution node globally between Bybit, Binance, and Bitget via a simple `/cex` command on Telegram.
- **Hybrid Polling & WebSockets:** Implements high-frequency REST polling for CCXT-compatible exchanges (Binance, Bitget) while retaining optimized PyBit WebSocket bindings for Bybit automatically.
- **ATR-Based Position Sizing:** Automatically halves position capital allocation (Margin constraint) for ultra-volatile pairs (e.g. Memecoins with NATR > 15%) to universally map risk equivalent across stable vs volatile tokens.
- **Dynamic Trailing Stops (Chandelier ATR):** Instead of using a static TP3, the bot lets the final 40% of the trade's equity ride the trend by consistently trailing the Stop Loss at a mapped distance of `2x ATR` behind the Highest-High.
- **Derivative Filters:** Skips setups if Funding Rate is overheated or Spot premiums misalign.

### ⚙️ Production Infrastructure

- **Modular Architecture:** Logic decoupled into domains (`technicals`, `derivatives`, `quant`, `patterns`, `exchange_manager`).
- **Database Persistence:** Operates natively on **SQLite3** for maximum portability while guaranteeing thread safety using WAL mode. No heavy background database services required.
- **Telegram Command Hub:**
  - Replaces complex integrations completely. Trigger `/live`, `/status`, `/scan`, `/autotrade on`, and `/setquota` securely without touching code files.
  - **Rich Alerts:** Sends marked-up charts with annotated entries.
- **Local Web Dashboard:** A `streamlit` dashboard running entirely locally providing historical trade exports and Win-Rate analytics without leaking API keys.

---

## 📂 Directory Structure

```text
/
├── auto_trades.py          # Auto Execution & Background Target Tracker
├── futurabot.sqlite        # SQLite Native Database File (Generated automatically)
├── config.json             # Core Configuration (Telegram Token & API Nodes)
├── dashboard.py            # Local Streamlit Analytics Server
├── main.py                 # Algorithmic Signal Scanner (Crons)
└── modules/
    ├── bot.py              # Telegram Formatter & Charting logic
    ├── config_loader.py    # Reads config.json safely
    ├── database.py         # SQLite connection manager & DB State
    ├── exchange_manager.py # CCXT Multi-CEX Dynamic Instance Loader
    ├── execution.py        # Generic Limit/Market Trade executors
    ├── telegram_listener.py# Handles Telegram Commands (Poller)
    └── (Analytical Engines): patterns.py, technicals.py, quant.py, smc.py, derivatives.py
```

## 🛠️ Installation & Setup

### 1. Prerequisites

- **Python 3.8+**
- API Keys with Trading Permissions from **Binance**, **Bitget**, or **Bybit**.
- A Telegram Bot Token (from BotFather) and your Chat ID.

### 2. Clone & Install

```bash
git clone https://github.com/yourusername/futurabot.git
cd futurabot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configuration

Make sure to create a `config.json` file in the root directory.

**Configuration Template:**

```json
{
    "api": {
        "telegram_bot_token": "YOUR_TOKEN_HERE",
        "telegram_chat_id": "YOUR_CHAT_ID",
        "bybit": {
            "key": "BYBIT_KEY",
            "secret": "BYBIT_SECRET"
        },
        "binance": {
            "key": "BINANCE_KEY",
            "secret": "BINANCE_SECRET"
        },
        "bitget": {
            "key": "BITGET_KEY",
            "secret": "BITGET_SECRET"
        }
    },
    "system": {
        "timezone": "Asia/Jakarta",
        "max_threads": 20,
        "check_interval_hours": 1,
        "timeframes": ["1h", "4h", "1d", "1w"]
    },
    ... (See config.example.json for strategy thresholds)
}
```

## 🚀 Running the Bot

The bot consists of three core components that are designed to run in parallel in separate terminal instances or via `systemd` handlers/screen blocks:

**1. The Telegram Poller & Live Execution Engine**
Tracks open targets, sets stop losses to breakeven, takes profit, and listens to your Telegram Commands.

```bash
python auto_trades.py
```

**2. The Scanner Daemon**
Constantly combs the exchange for pattern opportunities, executes SMC logic, and issues new trade signals to the database.

```bash
python main.py
```

**3. The Local Analytics Dashboard**
_(Optional)_ Runs a UI over `http://localhost:8501` to view history.

```bash
streamlit run dashboard.py
```

## 📊 Logic Overview

### Trade Lifecycle

1.  **Scan:** `main.py` fetches top perps for the currently active exchange (`CCXT`).
2.  **Filter & Score:** Passes technical criteria (SMC, Z-Score, Divergence). Evaluates minimum risk to reward constraint.
3.  **Signal Integration:** Bot inserts logic to SQLite and fires an image-annotated Telegram post.
4.  **Auto Trading:** If `/autotrade on` is toggled in Telegram, `auto_trades.py` grabs the signal. It parses the configured max ceiling array (`/setcapital` and `/setquota`), then calculates contract bounds dynamically.
5.  **Lifecycle TPs:** For Bybit, it listens using lightning fast WebSockets. For Binance/Bitget, it spins a local 10s-polling loop replicating CCXT boundaries. TPs trigger instantly.

---

## ⚠️ Disclaimer

This software is for educational purposes only. Cryptocurrency trading involves significant financial risk. The authors are not responsible for any financial losses incurred or liquidated portfolios while using this bot. Use at your own risk.
