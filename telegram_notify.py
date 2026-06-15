"""
telegram_notify.py
Αποστολή μηνυμάτων Telegram για το BTC bot.
"""

import requests
import logging
from datetime import datetime

logger = logging.getLogger("btc_bot.telegram")


def send(token: str, chat_id: str, text: str, silent: bool = False) -> bool:
    """
    Στέλνει μήνυμα Telegram. Επιστρέφει True αν πέτυχε.
    silent=True → notification χωρίς ήχο (για heartbeat/status).
    """
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


def heartbeat(token: str, chat_id: str, status: dict) -> bool:
    """Daily heartbeat message."""
    pos = status.get("position", "FLAT")
    pos_emoji = "📈" if pos == "LONG" else "📉" if pos == "SHORT" else "➖"
    text = (
        f"✅ <b>Bot Alive</b> — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"{pos_emoji} Position: <b>{pos}</b>\n"
        f"💰 Capital: <b>${status.get('capital', 0):,.2f}</b>\n"
        f"₿  BTC: <b>${status.get('btc_price', 0):,.0f}</b>\n"
        f"📊 MVRV-Z: <b>{status.get('mvrv', 'N/A')}</b>\n"
        f"📈 RSI(14): <b>{status.get('rsi', 'N/A')}</b>\n"
        f"🔧 Factor: <b>{status.get('factor', 'N/A')}</b>\n"
        f"⚙️  Action today: <b>{status.get('action', 'NO ACTION')}</b>"
    )
    return send(token, chat_id, text, silent=True)