import time
import requests
import threading
from modules.database import set_risk_config, get_risk_config
from modules.config_loader import CONFIG

class TelegramListener:
    def __init__(self):
        self.token = CONFIG['api'].get('telegram_bot_token')
        self.offset = 0
        self.running = False
        
    def start(self):
        if not self.token: return
        self.running = True
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
                        msg = update.get('message', {})
                        text = msg.get('text', '')
                        chat_id = msg.get('chat', {}).get('id')
                        
                        if text and chat_id:
                            self.handle_command(text, chat_id)
            except: pass
            time.sleep(2)
            
    def handle_command(self, text, chat_id):
        parts = text.split()
        cmd = parts[0].lower()
        
        reply = ""
        if cmd == '/setcapital' and len(parts) > 1:
            try:
                val = float(parts[1])
                if set_risk_config('total_trading_capital_usdt', val):
                    reply = f"✅ Modal Total Trading berhasil diatur ke: **${val}**"
            except: reply = "❌ Format salah. Contoh: /setcapital 10"
            
        elif cmd == '/setkuota' and len(parts) > 1:
            try:
                val = int(parts[1])
                if set_risk_config('max_concurrent_trades', val):
                    reply = f"✅ Maksimal Koin Bersamaan berhasil diatur ke: **{val}** koin"
            except: reply = "❌ Format salah. Contoh: /setkuota 2"
            
        elif cmd == '/autotrade' and len(parts) > 1:
            val = parts[1].lower()
            if val in ['on', 'off']:
                if set_risk_config('auto_trade', val):
                    reply = f"✅ Auto Trade otomatis **{'DIHIDUPKAN' if val == 'on' else 'DIMATIKAN'}**"
            else: reply = "❌ Format salah. Contoh: /autotrade on"
            
        elif cmd == '/statusrisk':
            cfg = get_risk_config()
            reply = f"📊 **STATUS RISK & MODAL** 📊\n\n"
            reply += f"🤖 Auto Trade: **{'ON' if cfg['auto_trade'] else 'OFF'}**\n"
            reply += f"💰 Modal Total: **${cfg['total_trading_capital_usdt']}**\n"
            reply += f"🛑 Limit Koin Bersamaan: **{cfg['max_concurrent_trades']}** koin"
            
        if reply:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={'chat_id': chat_id, 'text': reply, 'parse_mode': 'Markdown'})
