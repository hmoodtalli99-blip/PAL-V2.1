import requests, json, os, numpy as np, telebot, threading, time
from datetime import datetime, timezone
from flask import Flask

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
BIQUOTE_KEY = os.environ.get("BIQUOTE_API_KEY")
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask(__name__)
MEMORY_FILE = "brain_memory.json"
RISK_FILE = "risk_status.json"
INSTRUMENTS = [{"key": "XAUUSD"}, {"key": "EURUSD"}]

def send_telegram(msg):
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"})

def fetch_biquote(inst_key, tf, limit=500):
    tf_map = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240"}
    url = "https://api.biquote.io/v1/candles"
    params = {"symbol": inst_key, "interval": tf_map[tf], "limit": limit, "apikey": BIQUOTE_KEY}
    try:
        data = requests.get(url, params=params, timeout=15).json()
        candles = []
        for c in data['data']: candles.append({"open": float(c['o']), "high": float(c['h']), "low": float(c['l']), "close": float(c['c']), "volume": float(c['v'])})
        return candles
    except: return []

def is_news():
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        url = f"https://finnhub.io/api/v1/calendar/economic?from={today}&to={today}&token={FINNHUB_KEY}"
        news = requests.get(url, timeout=10).json()
        now = datetime.now(timezone.utc)
        for e in news.get('economicCalendar', []):
            et = datetime.fromtimestamp(e['time'], tz=timezone.utc)
            if abs((et - now).total_seconds()) < 1800 and e['impact'] == "high": return True
        return False
    except: return False

def session_mapper():
    h = datetime.now(timezone.utc).hour
    if 0 <= h < 9: return "ASIA"
    elif 9 <= h < 15: return "LONDON"
    elif 15 <= h < 22: return "NEWYORK"
    else: return "OVERLAP"

def risk_check():
    if not os.path.exists(RISK_FILE): return {"daily_loss": 0, "trades": 0, "blocked": False}
    return json.load(open(RISK_FILE, 'r'))

def load_memory():
    if not os.path.exists(MEMORY_FILE): return {"last_trade": {}, "stats": {"total": 0, "wins": 0}}
    return json.load(open(MEMORY_FILE, 'r'))

def save_memory(m): json.dump(m, open(MEMORY_FILE, 'w'), indent=2)

def simple_brain(inst):
    h4 = fetch_biquote(inst['key'], "4h"); m15 = fetch_biquote(inst['key'], "15m")
    if len(h4) < 50: return []
    score = 0
    if h4[-1]['close'] > h4[-2]['close']: score += 3
    if m15[-1]['close'] > m15[-2]['close']: score += 2
    if session_mapper() == "LONDON": score += 1
    if score >= 4:
        return [{"instrument": inst['key'], "dir": "BUY", "entry": round(m15[-1]['close'], 2), "sl": round(m15[-1]['low'], 2), "tp": round(m15[-1]['close'] + 10, 2), "score": score}]
    return []

@app.route('/')
def home(): return "PAL is ALIVE"

def main_loop():
    while True:
        try:
            if not is_news() and not risk_check()['blocked']:
                for inst in INSTRUMENTS:
                    alerts = simple_brain(inst)
                    for a in alerts: send_telegram(f"🧠 PAL ALERT\n{a['instrument']} {a['dir']}\nEntry: {a['entry']}")
        except Exception as e: send_telegram(f"ERROR: {e}")
        time.sleep(60)

@bot.message_handler(commands=['start'])
def start(m): bot.reply_to(m, "PAL شغال على Railway 🧠")

def run_bot(): bot.polling(none_stop=True)
def run_web(): app.run(host='0.0.0.0', port=8080)

threading.Thread(target=run_web).start()
threading.Thread(target=run_bot).start()
