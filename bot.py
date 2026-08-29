import os
import time
import threading
import datetime
import pytz
import requests
import psycopg2
import pandas as pd
import telebot
from ta import trend, momentum, volatility
from ta.trend import IchimokuIndicator

# ========== 1. الإعدادات ==========
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
FINNHUB_KEY = os.environ.get("FINNHUB_KEY")
DB_URL = os.environ.get("DATABASE_URL")
CHAT_ID = os.environ.get("CHAT_ID")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
GZA = pytz.timezone("Asia/Gaza")
DEMO_BALANCE = 50.0
RISK_PERCENT = 0.01
SPREAD_PIPS = 0.3

TEAM_RULES = {
    "sniper": {"lock_profit": 0.15, "target1": 0.3, "risk": 0.002},  # 0.2%
    "scalp": {"lock_profit": 0.5, "target1": 1.0, "risk": 0.01},
    "daily": {"lock_profit": 2.0, "target1": 4.0, "risk": 0.01},
    "swing": {"lock_profit": 5.0, "target1": 10.0, "risk": 0.01}
}

GOLD_KING = {"key": "XAUUSD", "name": "الذهب"}

OPEN_TRADES = {}
TRADING_ALGORITHMS = {}
TRADING_LIBRARY_RAW = {}
RECENT_EXITS = {}


# ========== 2. قاعدة البيانات ==========
def db_connect():
    return psycopg2.connect(DB_URL, sslmode='require')

def db_setup():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS trades 
                   (id SERIAL, inst TEXT, team TEXT, dir TEXT, result TEXT, profit FLOAT, time TIMESTAMP, report TEXT)''')
    cur.execute("CREATE TABLE IF NOT EXISTS brain (id int, balance float, wins int, losses int)")
    cur.execute("INSERT INTO brain VALUES (1, 50.0, 'G') ON CONFLICT (id) DO NOTHING")
    conn.commit()
    conn.close()

def get_balance():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM brain WHERE id=1")
    bal = cur.fetchone()[0]
    conn.close()
    return bal

def update_balance(change):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("UPDATE brain SET balance = balance + %s WHERE id=1", (change,))
    if change > 0:
        cur.execute("UPDATE brain SET wins = wins + 1 WHERE id=1")
    else:
        cur.execute("UPDATE brain SET losses = losses + 1 WHERE id=1")
    conn.commit()
    conn.close()

def save_trade(inst, team, direction, result, profit, report):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO trades (inst, team, dir, result, profit, time, report) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (inst, team, direction, result, profit, datetime.datetime.now(), report)
    )
    conn.commit()
    conn.close()


# ========== 3. جلب البيانات ==========
def fetch_biquote(symbol, timeframe, limit=100):
    try:
        url = f"https://api.finnhub.io/api/v1/forex/candle?symbol=OANDA:{symbol}&resolution={timeframe}&count={limit}&token={FINNHUB_KEY}"
        if symbol == "XAUUSD":
            url = f"https://api.finnhub.io/api/v1/crypto/candle?symbol=BINANCE:XAUUSD&resolution={timeframe}&count={limit}&token={FINNHUB_KEY}"
        
        data = requests.get(url, timeout=10).json()
        if data.get('s') != 'ok':
            return pd.DataFrame()
        
        df = pd.DataFrame({'o': data['o'], 'h': data['h'], 'l': data['l'], 'c': data['c']})
        return df
    except Exception as e:
        print(f"Error fetching data: {e}")
        return pd.DataFrame()

def gza_now():
    return datetime.datetime.now(GZA)


# ========== 4. الـ 15 عقل ==========
def Engineer_Brain(df):
    ema50 = trend.EMAIndicator(df['c'], 50).ema_indicator().iloc[-1]
    ema200 = trend.EMAIndicator(df['c'], 200).ema_indicator().iloc[-1]
    return (3, "الاتجاه صاعد") if ema50 > ema200 else (-3, "الاتجاه هابط")

def MarketMaker_Brain(df):
    return (2, "سيولة") if df['c'].iloc[-1] > df['c'].iloc[-2] else (-2, "لا سيولة")

def LibraryBrain(df):
    for key, rule in TRADING_ALGORITHMS.items():
        if "OTE" in rule and df['c'].iloc[-1] > df['c'].iloc[-2]:
            return 3, f"المكتبة: {rule}"
    return 0, "المكتبة: محايد"

def Sniper_Brain(df_1m, df_5m):
    score, report = 0, ""
    
    # تم تصحيح المعطيات لتقبل high و low فقط
    ichi = IchimokuIndicator(high=df_5m['h'], low=df_5m['l'])
    if df_5m['c'].iloc[-1] > ichi.ichimoku_a().iloc[-1]:
        score += 3
        report += "Ichi فوق السحابة +3\n"
        
    c5 = df_5m.iloc[-1]
    c5_prev = df_5m.iloc[-2]
    
    if c5['c'] > c5['o'] and c5_prev['c'] < c5_prev['o'] and c5['c'] > c5_prev['o']:
        score += 3
        report += "ابتلاع 5د +3\n"
        
    c1 = df_1m.iloc[-1]
    body = abs(c1['c'] - c1['o'])
    tail = c1['l'] - min(c1['c'], c1['o'])
    
    if tail > body * 2:
        score += 2
        report += "ذيل قناص 1د +2\n"
        
    return score, report

def Portfolio_Brain(inst, team):
    return 0, "المحفظة: تمام"

def Risk_Brain():
    today_loss = abs(get_today_loss())
    if today_loss >= DEMO_BALANCE * 0.03:
        return -10, "خسارة اليوم 3%"
    return 0, "المخاطر: تمام"

def get_today_loss():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT SUM(profit) FROM trades WHERE time > CURRENT_DATE AND profit < 0")
    res = cur.fetchone()[0]
    conn.close()
    return res if res else 0


# ========== 5. الملك والتنفيذ ==========
def King_Brain(inst, team="daily"):
    if team == "sniper":
        tf_trend, tf_entry, tp_mul, threshold = "5m", "1m", 0.3, 4
    elif team == "scalp":
        tf_trend, tf_entry, tp_mul, threshold = "1h", "5m", 1, 3
    elif team == "daily":
        tf_trend, tf_entry, tp_mul, threshold = "4h", "15m", 2, 4
    else:
        tf_trend, tf_entry, tp_mul, threshold = "1D", "1h", 3, 5

    df_trend = fetch_biquote(inst['key'], tf_trend, 200)
    df_entry = fetch_biquote(inst['key'], tf_entry, 200)
    
    if len(df_trend) < 50 or len(df_entry) < 50:
        return

    if team == "sniper":
        s_s, s_t = Sniper_Brain(df_entry, df_trend)
        total, report = s_s, s_t
    else:
        e_s, e_t = Engineer_Brain(df_trend)
        mm_s, mm_t = MarketMaker_Brain(df_entry)
        lib_s, lib_t = LibraryBrain(df_entry)
        p_s, p_t = Portfolio_Brain(inst, team)
        r_s, r_t = Risk_Brain()
        
        total = e_s + mm_s + lib_s + p_s + r_s
        report = f"<b>[{team.upper()}] {inst['key']}</b>\n{e_t}\n{mm_t}\n{lib_t}\n{p_t}\n{r_t}"

    if total >= threshold:
        entry = df_entry['c'].iloc[-1]
        rules = TEAM_RULES[team]
        sl = entry - rules['target1'] * tp_mul / 2
        tp = entry + rules['target1'] * tp_mul
        feedback_loop(inst['key'], "BUY", entry, sl, tp, report, team)

def feedback_loop(inst, direction, entry, sl, tp, report, team):
    rules = TEAM_RULES[team]
    r = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
    
    if r < 2.0:
        return
        
    risk_amount = get_balance() * rules['risk']
    lot = round(risk_amount / (abs(entry - sl) * 100), 2) if abs(entry - sl) > 0 else 0.01
    trade_id = f"{inst}_{team}_{int(time.time())}"
    
    OPEN_TRADES[trade_id] = {
        "inst": inst, "dir": direction, "entry": entry, "sl": sl, 
        "tp": tp, "lot": lot, "team": team, "report": report, "stage": 0
    }
    
    send_telegram(f"👑 [{team}] {direction} {inst}\nLot: {lot}\nEntry: {entry}\nSL: {sl}\nTP: {tp}\nRisk: ${risk_amount:.2f}")
    threading.Thread(target=monitor_trade, args=(trade_id,)).start()

def monitor_trade(trade_id):
    trade = OPEN_TRADES[trade_id]
    team = trade['team']
    rules = TEAM_RULES[team]
    
    while trade_id in OPEN_TRADES:
        time.sleep(30)
        df = fetch_biquote(trade['inst'], "1m" if team == "sniper" else "5m", 5)
        
        if not len(df):
            continue
            
        price = df['c'].iloc[-1]
        profit_now = price - trade['entry'] if trade['dir'] == "BUY" else trade['entry'] - price

        # التأمين الذكي
        if trade['stage'] == 0 and profit_now >= rules['lock_profit']:
            new_sl = trade['entry'] + rules['lock_profit'] + SPREAD_PIPS / 100
            trade['sl'] = new_sl
            trade['stage'] = 1
            send_telegram(f"🛡️ [{team}] تأمين SL -> {new_sl:.2f}")

        # جني أرباح النصف
        if trade['stage'] == 1 and profit_now >= rules['target1']:
            profit = rules['target1'] * trade['lot'] * 100 * 0.5
            update_balance(profit)
            trade['sl'] = trade['entry'] + rules['lock_profit']
            trade['stage'] = 2
            send_telegram(f"🎯 [{team}] هدف أول. قفلنا النصف +${profit:.2f}")

        # الإغلاق النهائي (حاليًا مبرمج لصفقات الشراء BUY فقط)
        if trade['dir'] == "BUY":
            if price >= trade['tp']:
                profit = (trade['tp'] - trade['entry']) * trade['lot'] * 100
                update_balance(profit)
                save_trade(trade['inst'], team, trade['dir'], "WIN", profit, trade['report'])
                break
                
            if price <= trade['sl']:
                profit = (trade['sl'] - trade['entry']) * trade['lot'] * 100
                update_balance(profit)
                save_trade(trade['inst'], team, trade['dir'], "LOSS", profit, trade['report'])
                break
                
    if trade_id in OPEN_TRADES:
        del OPEN_TRADES[trade_id]

def send_telegram(msg):
    try:
        bot.send_message(CHAT_ID, msg, parse_mode="HTML")
    except Exception as e:
        print(f"Telegram Error: {e}")


# ========== 6. الحلقات ==========
def sniper_loop():
    while True:
        King_Brain(GOLD_KING, "sniper")
        time.sleep(60)

def scalp_loop():
    while True:
        King_Brain(GOLD_KING, "scalp")
        time.sleep(300)

def daily_loop():
    while True:
        King_Brain(GOLD_KING, "daily")
        time.sleep(900)

def swing_loop():
    while True:
        King_Brain(GOLD_KING, "swing")
        time.sleep(3600)


# ========== 7. أوامر التليجرام ==========
@bot.message_handler(commands=['start'])
def start(m):
    send_telegram("👑 PAL v7.5 اشتغل. 4 فرق في الخدمة")

@bot.message_handler(commands=['balance'])
def balance(m):
    send_telegram(f"💰 الرصيد الديمو: ${get_balance():.2f}")

@bot.message_handler(commands=['report'])
def report(m):
    send_telegram("التقرير اليومي: شغال")


# ========== 8. التشغيل ==========
if __name__ == "__main__":
    db_setup()
    threading.Thread(target=sniper_loop, daemon=True).start()
    threading.Thread(target=scalp_loop, daemon=True).start()
    threading.Thread(target=daily_loop, daemon=True).start()
    threading.Thread(target=swing_loop, daemon=True).start()
    
    print("Bot is running...")
    try:
        bot.infinity_polling()
    except Exception as e:
        print(f"Polling Error: {e}")
