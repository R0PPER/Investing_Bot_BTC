"""
telegram_notify.py — FIXED
Διορθώσεις:
  1. Unique timestamp σε κάθε heartbeat (αποφεύγει auto-grouping από Telegram)
  2. Retry logic για αποτυχημένα sends
  3. Καλύτερο error logging
"""

import requests
import logging
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger("btc_bot.telegram")


def send(token: str, chat_id: str, text: str, silent: bool = False,
         retries: int = 3) -> bool:
    """
    Στέλνει μήνυμα Telegram με retry logic.
    Επιστρέφει True αν πέτυχε.
    """
    if not token or not chat_id:
        logger.warning("Telegram not configured — skipping")
        return False
    if token == "YOUR_BOT_TOKEN_HERE":
        logger.warning("Telegram token not set — skipping")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id":             chat_id,
        "text":                text,
        "parse_mode":          "HTML",
        "disable_notification": silent,
    }

    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 200:
                logger.info(f"Telegram OK (attempt {attempt}): {text[:50]}...")
                return True
            elif r.status_code == 429:
                # Rate limit — wait and retry
                retry_after = int(r.json().get("parameters", {}).get("retry_after", 5))
                logger.warning(f"Telegram rate limit — waiting {retry_after}s")
                import time; time.sleep(retry_after)
            else:
                logger.error(f"Telegram HTTP {r.status_code}: {r.text[:150]}")
                if attempt == retries:
                    return False
        except requests.exceptions.Timeout:
            logger.warning(f"Telegram timeout (attempt {attempt}/{retries})")
        except Exception as e:
            logger.error(f"Telegram error (attempt {attempt}/{retries}): {e}")
            if attempt == retries:
                return False

    return False


def alert(token: str, chat_id: str, title: str, body: str) -> bool:
    """Alert με ήχο — για trading actions (LONG, SHORT, PARTIAL SELL κλπ)."""
    text = f"<b>{title}</b>\n{body}"
    return send(token, chat_id, text, silent=False)


def info(token: str, chat_id: str, title: str, body: str) -> bool:
    """Info χωρίς ήχο — για status updates."""
    text = f"<b>{title}</b>\n{body}"
    return send(token, chat_id, text, silent=True)


def get_athens_time() -> datetime:
    """Επιστρέφει την τρέχουσα ώρα Αθήνας."""
    try:
        athens_tz = pytz.timezone("Europe/Athens")
        return datetime.now(athens_tz)
    except Exception:
        return datetime.utcnow() + timedelta(hours=3)


def heartbeat(token: str, chat_id: str, status: dict) -> bool:
    """
    Daily heartbeat με:
    - Ώρα Ελλάδας
    - Unique timestamp (αποφεύγει Telegram auto-grouping)
    - silent=True (δεν κάνει ήχο, απλά εμφανίζεται)
    """
    now_utc    = datetime.utcnow()
    now_athens = get_athens_time()

    pos       = status.get("position", "FLAT")
    pos_emoji = "📈" if pos == "LONG" else "📉" if pos == "SHORT" else "➖"

    hour = now_athens.hour
    if 5 <= hour < 12:
        time_label = "🌅 Πρωινή ενημέρωση"
    elif 12 <= hour < 18:
        time_label = "☀️ Μεσημεριανή ενημέρωση"
    else:
        time_label = "🌙 Βραδινή ενημέρωση"

    action = status.get("action", "NO ACTION")
    action_emoji = "✅" if action == "NO ACTION" else "🚨"

    text = (
        f"{time_label}\n"
        f"🕐 <b>{now_athens.strftime('%H:%M')}</b> ώρα Ελλάδας"
        f"  <i>({now_utc.strftime('%H:%M')} UTC)</i>\n"
        f"📅 {now_athens.strftime('%d/%m/%Y')}\n"
        f"─────────────────────\n"
        f"{pos_emoji} <b>Position:</b> {pos}\n"
        f"💰 <b>Capital:</b> ${status.get('capital', 0):,.2f}\n"
        f"₿  <b>BTC:</b> ${status.get('btc_price', 0):,.0f}\n"
        f"📊 <b>MVRV-Z:</b> {status.get('mvrv', 'N/A')}\n"
        f"📈 <b>RSI(14):</b> {status.get('rsi', 'N/A')}\n"
        f"🔧 <b>Factor:</b> {status.get('factor', 'N/A')}\n"
        f"─────────────────────\n"
        f"{action_emoji} <b>Action:</b> {action}\n"
        f"✅ Bot alive · run #{status.get('run_id', now_utc.strftime('%H%M%S'))}"
    )

    # Heartbeat είναι silent (δεν χτυπά — αλλά φαίνεται στο chat)
    return send(token, chat_id, text, silent=True)


def startup_message(token: str, chat_id: str, version: str = "1.0") -> bool:
    """Μήνυμα εκκίνησης bot — με ήχο."""
    now_athens = get_athens_time()
    text = (
        f"🤖 <b>BTC Bot εκκινήθηκε</b>\n"
        f"📅 {now_athens.strftime('%d/%m/%Y %H:%M')} (Αθήνα)\n"
        f"🔧 Version: {version}\n"
        f"⏰ Schedule: 04:10 + 09:00 ώρα Ελλάδας"
    )
    return send(token, chat_id, text, silent=False)
