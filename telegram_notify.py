"""
telegram_notify.py
Αποστολή μηνυμάτων Telegram για το BTC bot.
"""

import requests
import logging
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger("btc_bot.telegram")


def send(token: str, chat_id: str, text: str, silent: bool = False) -> bool:
    """Στέλνει μήνυμα Telegram. Επιστρέφει True αν πέτυχε."""
    if not token or not chat_id:
        logger.warning("Telegram not configured — skipping notification")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": silent,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            logger.info(f"Telegram sent: {text[:60]}...")
            return True
        else:
            logger.error(f"Telegram error {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"Telegram exception: {e}")
        return False


def alert(token: str, chat_id: str, title: str, body: str) -> bool:
    """Στέλνει alert με ήχο (για actions: LONG, SHORT, SELL κλπ)."""
    text = f"<b>{title}</b>\n{body}"
    return send(token, chat_id, text, silent=False)


def info(token: str, chat_id: str, title: str, body: str) -> bool:
    """Στέλνει info χωρίς ήχο (για heartbeat, status)."""
    text = f"<b>{title}</b>\n{body}"
    return send(token, chat_id, text, silent=True)


def get_athens_time():
    """Επιστρέφει την τρέχουσα ώρα Αθήνας (UTC+2 ή UTC+3 ανάλογα εποχή)."""
    # Δημιουργία timezone για Αθήνα (χειμώνας/καλοκαίρι αυτόματα)
    try:
        athens_tz = pytz.timezone('Europe/Athens')
        return datetime.now(athens_tz)
    except:
        # Fallback: αν δεν υπάρχει pytz, χρησιμοποίησε UTC+3 (καλοκαίρι)
        return datetime.utcnow() + timedelta(hours=3)


def heartbeat(token: str, chat_id: str, status: dict) -> bool:
    """Daily heartbeat message με ώρα Ελλάδας."""
    now_athens = get_athens_time()
    
    pos = status.get("position", "FLAT")
    pos_emoji = "📈" if pos == "LONG" else "📉" if pos == "SHORT" else "➖"
    
    # Προσδιόρισε αν είναι πρωινό ή βραδινό heartbeat
    hour = now_athens.hour
    if 5 <= hour < 12:
        time_indicator = "🌅 Πρωινή ενημέρωση"
    elif 12 <= hour < 18:
        time_indicator = "☀️ Μεσημεριανή ενημέρωση"
    else:
        time_indicator = "🌙 Βραδινή ενημέρωση"
    
    text = (
        f"{time_indicator}\n"
        f"🕐 {now_athens.strftime('%H:%M')} ώρα Ελλάδας\n"
        f"─────────────────\n"
        f"{pos_emoji} <b>Position:</b> {pos}\n"
        f"💰 <b>Capital:</b> ${status.get('capital', 0):,.2f}\n"
        f"₿ <b>BTC:</b> ${status.get('btc_price', 0):,.0f}\n"
        f"📊 <b>MVRV-Z:</b> {status.get('mvrv', 'N/A')}\n"
        f"📈 <b>RSI(14):</b> {status.get('rsi', 'N/A')}\n"
        f"🔧 <b>Factor:</b> {status.get('factor', 'N/A')}\n"
        f"⚙️ <b>Action:</b> {status.get('action', 'NO ACTION')}\n"
        f"─────────────────\n"
        f"✅ Bot is alive"
    )
    return send(token, chat_id, text, silent=False)  # silent=False για να το προσέχεις
