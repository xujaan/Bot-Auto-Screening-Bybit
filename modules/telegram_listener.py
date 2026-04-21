"""
Tujuan: Menangani perintah dari user via Telegram Poller.
Caller: main.py
Dependensi: requests, sqlite3
Main Functions: TelegramListener.poll(), TelegramListener.handle_command()
Side Effects: Membaca/Update setting di bot_state SQLite. Mengirim perintah execute/close ke CEX via auto_trades fallback.
"""

import time
import requests
import threading
from modules.database import set_risk_config, get_risk_config, set_active_cex, get_active_cex
from modules.config_loader import CONFIG
import telegramify_markdown

class TelegramListener:
    def __init__(self, exchange=None):
        self.token = CONFIG['api'].get('telegram_bot_token')
        self.offset = 0
        self.running = False
        self.exchange = exchange
        
    def start(self):
        if not self.token: return
        self.running = True
        
        try:
            url = f"https://api.telegram.org/bot{self.token}/setMyCommands"
            commands = [
                {"command": "status", "description": "Show live positions & fast close actions"},
                {"command": "balance", "description": "Check unified balance & exposure"},
                {"command": "live", "description": "Show DB live dashboard & pending signals"},
                {"command": "pending", "description": "Retrieve limit orders queue in Exchange"},
                {"command": "scan", "description": "Force manual market scan instantly"},
                {"command": "fav", "description": "View favorite saved signals"},
                {"command": "log", "description": "View system activity logs"},
                {"command": "reset", "description": "Erase screening histories from database"},
                {"command": "autotrade", "description": "Toggle Autotrade ON/OFF"},
                {"command": "setcapital", "description": "Set trading equity config"},
                {"command": "setquota", "description": "Set maximum allowed open pairs"},
                {"command": "statusrisk", "description": "Check configuration defaults"},
                {"command": "cex", "description": "Switch Active CEX [binance/bitget/bybit]"}
            ]
            requests.post(url, json={"commands": commands}, timeout=5)
        except Exception as e:
            print(f"Failed to register TG commands: {e}")
            
        self.thread = threading.Thread(target=self.poll, daemon=True)
        self.thread.start()
        print("🤖 Telegram Command Listener Started.")
        
    def stop(self):
        self.running = False
        
    def poll(self):
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        while self.running:
            try:
                r = requests.get(url, params={'offset': self.offset, 'timeout': 10}, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    for update in data.get('result', []):
                        self.offset = update['update_id'] + 1
                        if 'callback_query' in update:
                            self.handle_callback(update['callback_query'])
                            continue
                            
                        msg = update.get('message', {})
                        text = msg.get('text', '')
                        chat_id = msg.get('chat', {}).get('id')
                        
                        if text and chat_id:
                            self.handle_command(text, chat_id)
            except: pass
            time.sleep(2)
            
    def handle_callback(self, callback_query):
        from modules.database import get_conn, release_conn, get_risk_config, get_dict_cursor
        callback_id = callback_query.get('id')
        data = callback_query.get('data', '')
        msg = callback_query.get('message', {})
        chat_id = msg.get('chat', {}).get('id')
        
        reply = ""
        if data.startswith('trade_'):
            symbol = data[6:]
            if not self.exchange:
                reply = "❌ Exchange is not initialized."
            else:
                conn = get_conn()
                try:
                    cur = get_dict_cursor(conn)
                    cur.execute("SELECT * FROM trades WHERE symbol = ? AND status = 'Waiting Entry' ORDER BY created_at DESC LIMIT 1", (symbol,))
                    trade = cur.fetchone()
                    
                    if trade:
                        from modules.execution import execute_entry
                        res = {
                            'Symbol': trade['symbol'],
                            'Side': trade['side'],
                            'Entry': float(trade['entry_price']),
                            'SL': float(trade['sl_price']),
                            'TP3': float(trade['tp3']) if trade.get('tp3') else None,
                            'Total_Score': trade.get('tech_score', 0) + trade.get('smc_score', 0) + trade.get('quant_score', 0) + trade.get('deriv_score', 0)
                        }
                        
                        risk_cfg = get_risk_config()
                        active_pos_count = 0
                        try:
                            positions = self.exchange.fetch_positions()
                            active_pos_count = len([p for p in positions if float(p.get('contracts', 0)) > 0])
                        except: pass
                        
                        if active_pos_count < risk_cfg.get('max_concurrent_trades', 2):
                            result = execute_entry(self.exchange, res)
                            if result:
                                def fmt_price(p): return f"{p:.8f}".rstrip('0').rstrip('.') if p < 1 else f"{p:.4f}"
                                reply = (
                                    f"✅ **TRADE LIMIT SUCCESS!**\n\n"
                                    f"🪙 **Symbol:** `{result['symbol']}`\n"
                                    f"🧭 **Mode:** `{result['side']}`\n"
                                    f"🎯 **Entry:** `{fmt_price(result['entry_price'])}`\n"
                                    f"📦 **Quantity:** `{result['qty']}`\n"
                                    f"🔩 **Leverage:** `{result['leverage']}x`\n"
                                    f"💵 **Margin Used:** `${result['margin']:.2f}`\n"
                                    f"🛑 **Stop Loss:** `{fmt_price(result['sl'])}`\n"
                                    f"🛒 **Order ID:** `{result['order_id']}`"
                                )
                            else: reply = f"❌ Failed to place order for {symbol}."
                        else: reply = f"❌ Trade limit reached ({active_pos_count}/{risk_cfg.get('max_concurrent_trades', 2)})"
                    else: reply = f"❌ No 'Waiting Entry' found for {symbol}."
                except Exception as e: reply = f"❌ DB Error: {e}"
                finally: release_conn(conn)
                    
        elif data.startswith('endtrade_'):
            symbol = data.split('_', 1)[1]
            if not self.exchange:
                reply = "❌ Exchange is not initialized."
            else:
                from modules.execution import close_position
                from modules.database import get_conn, release_conn, get_dict_cursor, log_action
                success, msg_response = close_position(self.exchange, symbol)
                if success:
                    reply = f"✅ **{msg_response}**"
                    conn = get_conn()
                    try:
                        cur = get_dict_cursor(conn)
                        cur.execute("UPDATE trades SET status = 'Closed (Manual)' WHERE symbol = ? AND status NOT LIKE '%Closed%'", (symbol,))
                        conn.commit()
                        log_action('MANUAL_CLOSE', f"User manually closed position for {symbol}")
                    except: pass
                    finally: release_conn(conn)
                else: 
                    log_action('MANUAL_CLOSE_ERROR', f"Failed to close {symbol}: {msg_response}")
                    reply = f"❌ **{msg_response}**"
                    
        elif data.startswith('fav_'):
            symbol = data[4:]
            from modules.database import get_conn, release_conn, get_dict_cursor
            conn = get_conn()
            try:
                cur = get_dict_cursor(conn)
                cur.execute("SELECT * FROM trades WHERE symbol = ? ORDER BY created_at DESC LIMIT 1", (symbol,))
                trade = cur.fetchone()
                if trade:
                    cur.execute("INSERT INTO favorites_list (symbol, side, timeframe, pattern, entry_price) VALUES (?, ?, ?, ?, ?)", (trade['symbol'], trade['side'], trade['timeframe'], trade['pattern'], trade['entry_price']))
                    conn.commit()
                    reply = f"⭐ Pinned **{symbol}** ({trade['side']}) to your Favorites! Use /fav to view."
                else: reply = f"❌ Cannot find recent screening for {symbol}."
            except Exception as e: reply = f"❌ DB Error: {e}"
            finally: release_conn(conn)
                    
        elif data == 'confirmreset_true':
            from modules.database import get_conn, release_conn, log_action
            conn = get_conn()
            try:
                cur = conn.cursor()
                cur.execute("DELETE FROM trades")
                conn.commit()
                log_action('SCREENING_RESET', 'User wiped the screening histories.')
                reply = "✅ **SUCCESS!** Screening histories have been wiped from DB. Active Trades remain untouched."
            except Exception as e: reply = f"❌ Failed to wipe db: {e}"
            finally: release_conn(conn)
                    
        if reply and chat_id:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={'chat_id': chat_id, 'text': telegramify_markdown.markdownify(reply), 'parse_mode': 'MarkdownV2'})
            
        ans_url = f"https://api.telegram.org/bot{self.token}/answerCallbackQuery"
        requests.post(ans_url, json={'callback_query_id': callback_id})

    def handle_command(self, text, chat_id):
        parts = text.split()
        cmd = parts[0].lower()
        reply = ""
        
        if cmd == '/cex' and len(parts) > 1:
            val = parts[1].lower()
            if val in ['binance', 'bitget', 'bybit']:
                if set_active_cex(val):
                    from modules.exchange_manager import get_current_exchange
                    self.exchange = get_current_exchange(force_reload=True) 
                    reply = f"✅ **Platform Switched Successfully**\nBot is now scanning and trading entirely on **{val.upper()}**.\n*(Note: Make sure your keys are mapped in config.json)*"
                else: reply = "❌ Failed to update active CEX in DB."
            else: reply = "❌ Invalid platform. Provide `bybit`, `binance`, or `bitget`."
            
        elif cmd == '/setcapital' and len(parts) > 1:
            try:
                val = float(parts[1])
                if set_risk_config('total_trading_capital_usdt', val):
                    reply = f"✅ Trading Capital Set To: **${val}**"
            except: reply = "❌ Format error. Example: /setcapital 10"
            
        elif cmd == '/setquota' and len(parts) > 1:
            try:
                val = int(parts[1])
                if set_risk_config('max_concurrent_trades', val):
                    reply = f"✅ Maximum Concurrent Pair Set To: **{val}** pairs"
            except: reply = "❌ Format error. Example: /setquota 2"
            
        elif cmd == '/autotrade' and len(parts) > 1:
            val = parts[1].lower()
            if val in ['on', 'off']:
                if set_risk_config('auto_trade', val):
                    reply = f"✅ Auto Trade Mode **{'ENABLED' if val == 'on' else 'DISABLED'}**"
            else: reply = "❌ Format error. Example: /autotrade on"
            
        elif cmd == '/statusrisk':
            cfg = get_risk_config()
            reply = f"📊 **RISK MANAGER STATUS** 📊\n\n"
            reply += f"🏢 Current Node: **{get_active_cex().upper()}**\n"
            reply += f"🤖 Auto Trade: **{'ON' if cfg['auto_trade'] else 'OFF'}**\n"
            reply += f"💰 Trading Pool: **${cfg['total_trading_capital_usdt']}**\n"
            reply += f"🛑 Slot Ceiling: **{cfg['max_concurrent_trades']}** active pairs"
            
        elif cmd == '/live':
            from modules.database import get_conn, release_conn, get_dict_cursor
            conn = get_conn()
            lines = []
            try:
                cur = get_dict_cursor(conn)
                cur.execute("SELECT symbol, side, status, entry_hit_at, created_at FROM trades WHERE status NOT LIKE '%Closed%' ORDER BY created_at DESC")
                trades = cur.fetchall()
                def fmt_time(t_val):
                    if hasattr(t_val, 'strftime'): return t_val.strftime('%H:%M')
                    if isinstance(t_val, str) and len(t_val) >= 16: return t_val[11:16]
                    return str(t_val)
                lines = [f"[{fmt_time(t['entry_hit_at'] or t['created_at'])}] {'🟢' if 'Active' in t['status'] else '⏳'} {t['symbol'].split(':')[0]} ({t['side']}): {t['status']}" for t in trades]
            except Exception as e: reply = f"❌ Error fetching DB: {e}"
            finally: release_conn(conn)
            
            if lines: 
                block = "\n".join(lines)
                reply = f"**📊 LIVE DASHBOARD (DB)**\n\n```text\n{block}\n```"
            elif not reply: 
                reply = "**📊 LIVE DASHBOARD (DB)**\n\n```text\n⚪ No active or pending trades mapped.\n```"
            
        elif cmd == '/scan':
            import threading
            def run_manual_scan():
                import main
                import requests
                url = f"https://api.telegram.org/bot{self.token}/sendMessage"
                res = requests.post(url, json={'chat_id': chat_id, 'text': telegramify_markdown.markdownify('⏳ Firing algorithm scan cycle...'), 'parse_mode': 'MarkdownV2'}).json()
                msg_id = None
                if res.get('ok'): msg_id = res['result']['message_id']
                
                def prog_cb(text):
                    if msg_id:
                        e_url = f"https://api.telegram.org/bot{self.token}/editMessageText"
                        requests.post(e_url, json={'chat_id': chat_id, 'message_id': msg_id, 'text': telegramify_markdown.markdownify(text), 'parse_mode': 'MarkdownV2'})
                try: main.scan(prog_cb)
                except Exception as e: prog_cb(f"❌ System Fault: {e}")
            threading.Thread(target=run_manual_scan, daemon=True).start()
            return
            
        elif cmd == '/pending':
            if not self.exchange: reply = "❌ Exchange architecture empty."
            else:
                try:
                    open_orders = self.exchange.fetch_open_orders()
                    if not open_orders: reply = f"⚪ No active limit queues on {get_active_cex().title()}"
                    else:
                        block = ""
                        for o in open_orders:
                            sym = o['symbol'].split(':')[0]
                            side = o['side'].upper()
                            qty = o['amount']
                            price = o['price']
                            block += f"{sym} ({side})\n"
                            block += f" ├ Size: {qty}\n └ Bid : {price}\n\n"
                            if len(block) > 3500:
                                block += "...(Truncated)...\n"
                                break
                        reply = f"⏳ **BROKER QUEUE ({get_active_cex().title()})** ⏳\n\n```text\n{block}\n```"
                except Exception as e: reply = f"❌ Fetch limits failed: {e}"
            
        elif cmd == '/reset':
            keyboard = [[{"text": "⚠️ PROCEED WIPE", "callback_data": "confirmreset_true"}]]
            import json
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={'chat_id': chat_id, 'text': telegramify_markdown.markdownify("⚠️ **WARNING:** This purely purges old screening histories.\n\nYour Active Auto Trades will **NOT** be erased."), 'parse_mode': 'MarkdownV2', 'reply_markup': json.dumps({"inline_keyboard": keyboard})})
            return
            
        elif cmd == '/balance':
            if not self.exchange: reply = "❌ Exchange engine disjointed."
            else:
                try:
                    active_cex = get_active_cex().title()
                    b = self.exchange.fetch_balance()
                    total = float(b['total'].get('USDT', 0))
                    free = float(b['free'].get('USDT', 0))
                    used = float(b['used'].get('USDT', 0))
                    
                    reply = f"🏦 **{active_cex} UNIFIED WALLET (USDT)** 🏦\n\n"
                    reply += f"💵 **Total Equity:** `${total:.2f}`\n"
                    reply += f"🛡️ **Margin Used:** `${used:.2f}`\n"
                    reply += f"🟢 **Available:** `${free:.2f}`\n"
                except Exception as e: reply = f"❌ Fetch Balance failed: {e}"
                
        elif cmd == '/log':
            from modules.database import get_conn, release_conn, get_dict_cursor
            conn = get_conn()
            try:
                cur = get_dict_cursor(conn)
                cur.execute("SELECT type, message, created_at FROM system_logs ORDER BY created_at DESC LIMIT 10")
                logs = cur.fetchall()
                if not logs: reply = "⚪ No system logs mapped yet."
                else:
                    block = ""
                    for lg in logs:
                        dt = str(lg['created_at'])[11:19]
                        block += f"[{dt}] {lg['type']}\n > {lg['message']}\n"
                    reply = f"📜 **SYSTEM LOGS (Last 10)**\n\n```text\n{block}\n```"
            except Exception as e: reply = f"❌ Fetch logs failed: {e}"
            finally: release_conn(conn)
            
        elif cmd == '/fav':
            from modules.database import get_conn, release_conn, get_dict_cursor
            conn = get_conn()
            try:
                cur = get_dict_cursor(conn)
                cur.execute("SELECT symbol, side, timeframe, pattern, entry_price FROM favorites_list ORDER BY added_at DESC LIMIT 15")
                favs = cur.fetchall()
                if not favs: reply = "⚪ You have no favorites saved."
                else:
                    block = ""
                    for f in favs:
                        block += f"⭐ {f['symbol']} ({f['side']})\n"
                        block += f" ├ TF: {f['timeframe']} [{f['pattern']}]\n └ Entry: {f['entry_price']}\n\n"
                    reply = f"🌟 **SAVED FAVORITES** 🌟\n\n```text\n{block}\n```"
            except Exception as e: reply = f"❌ Fetch favorites failed: {e}"
            finally: release_conn(conn)
            
        elif cmd == '/status':
            if not self.exchange: reply = "❌ Exchange engine disjointed."
            else:
                try:
                    positions = self.exchange.fetch_positions()
                    active_pos = [p for p in positions if float(p.get('contracts', 0)) > 0]
                    if not active_pos: reply = f"⚪ Zero exposure on {get_active_cex().title()}"
                    else:
                        reply = f"🟢 **MARKET POSITIONS ({get_active_cex().title()})** 🟢\n\n"
                        keyboard = []
                        for p in active_pos:
                            sym = p['symbol']
                            side = p['side'].upper()
                            qty = float(p.get('contracts', 0))
                            pnl = float(p.get('unrealizedPnl', 0) or 0)
                            entry_price = float(p.get('entryPrice', 1))
                            
                            # Estimate percentage PnL if not provided natively
                            pct = float(p.get('percentage', 0) or 0)
                            if pct == 0 and qty > 0 and entry_price > 0:
                                margin_est = (qty * entry_price) / 25  # Rough approx if leverage is unknown
                                pct = (pnl / margin_est * 100) if margin_est > 0 else 0
                                
                            icon = "🟩" if pnl > 0 else "🟥"
                            reply += f"{icon} **{sym}** (`{side}`)\n"
                            reply += f"   • Margin: `{qty}`\n"
                            reply += f"   • B. Entry: `{entry_price}`\n"
                            reply += f"   • Est uNL: `${pnl:.2f} ({pct:.2f}%)`\n\n"
                            keyboard.append([{"text": f"🛑 Kill {sym}", "callback_data": f"endtrade_{sym}"}])
                        import json
                        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
                        requests.post(url, json={'chat_id': chat_id, 'text': telegramify_markdown.markdownify(reply), 'parse_mode': 'MarkdownV2', 'reply_markup': json.dumps({"inline_keyboard": keyboard})})
                        return 
                except Exception as e: reply = f"❌ Socket link fault: {e}"
            
        if reply:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={'chat_id': chat_id, 'text': telegramify_markdown.markdownify(reply), 'parse_mode': 'MarkdownV2'})
