"""
state.py - Firebase version
Persistent state στο Firebase Realtime Database.
"""

import json
import logging
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime, date
import os

logger = logging.getLogger("btc_bot.state")

# ── Firebase Initialization ──────────────────────────────────────────
def init_firebase():
    """Αρχικοποιεί Firebase αν δεν έχει γίνει ήδη."""
    if not firebase_admin._apps:
        cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase_creds.json")
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred, {
            'databaseURL': 'https://btc-trading-bot-e28b6-default-rtdb.europe-west1.firebasedatabase.app/'
            
        })
        logger.info("Firebase initialized")

def _serialize(obj):
    """JSON serializer για dates/datetimes."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

def _deserialize(d: dict) -> dict:
    """Μετατρέπει ISO strings πίσω σε datetime."""
    if not d:
        return {}
    date_fields = ["entry_date", "last_partial_sell_date", "mvrv_override_start_date",
                   "last_run_date", "last_action_date"]
    for f in date_fields:
        if f in d and d[f] is not None:
            try:
                d[f] = datetime.fromisoformat(d[f])
            except (ValueError, TypeError):
                pass

    if "partial_sells" in d and isinstance(d["partial_sells"], list):
        for ps in d["partial_sells"]:
            if "date" in ps and isinstance(ps["date"], str):
                try:
                    ps["date"] = datetime.fromisoformat(ps["date"])
                except (ValueError, TypeError):
                    pass
    return d

DEFAULT_STATE = {
    "position": 0,
    "entry_price": 0.0,
    "entry_date": None,
    "entry_value": 0.0,
    "entry_qty": 0.0,
    "entry_fee": 0.0,
    "entry_balance": 0.0,
    "qty": 0.0,
    "stop_price": 0.0,
    "leverage": 1.0,
    "current_long_has_partials": False,
    "last_partial_sell_date": None,
    "partial_sells": [],
    "capital": 10000.0,
    "mvrv_factor_override": False,
    "mvrv_override_start_date": None,
    "leverage_next_long": False,
    "last_run_date": None,
    "last_action": "NONE",
    "last_action_date": None,
    "total_trades": 0,
    "total_pnl": 0.0,
    "action_log": [],
}

def load() -> dict:
    """Φορτώνει το state από Firebase."""
    init_firebase()
    ref = db.reference('/bot_state')
    raw = ref.get()
    
    if not raw:
        logger.info("No state in Firebase — using default")
        return dict(DEFAULT_STATE)
    
    state = {**DEFAULT_STATE, **raw}
    state = _deserialize(state)
    logger.info(f"State loaded from Firebase — position={state.get('position',0)} capital=${state.get('capital',0):,.2f}")
    return state

def save(state: dict) -> bool:
    """Αποθηκεύει το state στο Firebase."""
    init_firebase()
    
    # Κάνουμε copy για να μην αλλάξουμε το original
    to_save = dict(state)
    
    # Convert datetime objects σε strings για JSON
    date_fields = ["entry_date", "last_partial_sell_date", "mvrv_override_start_date",
                   "last_run_date", "last_action_date"]
    for f in date_fields:
        if f in to_save and to_save[f] is not None:
            if isinstance(to_save[f], (datetime, date)):
                to_save[f] = to_save[f].isoformat()
    
    if "partial_sells" in to_save:
        for ps in to_save["partial_sells"]:
            if "date" in ps and isinstance(ps["date"], (datetime, date)):
                ps["date"] = ps["date"].isoformat()
    
    # Αποθήκευση στο Firebase
    try:
        ref = db.reference('/bot_state')
        ref.set(to_save)
        logger.debug("State saved to Firebase")
        return True
    except Exception as e:
        logger.error(f"Firebase save error: {e}")
        return False

def log_action(state: dict, action: str, details: str = "") -> None:
    """Προσθέτει action στο audit log."""
    entry = {
        "date": datetime.utcnow().isoformat(),
        "action": action,
        "details": details,
        "capital": state.get("capital", 0),
        "position": state.get("position", 0),
    }
    log = state.get("action_log", [])
    log.append(entry)
    state["action_log"] = log[-90:]  # κρατά τα 90 τελευταία
    state["last_action"] = action
    state["last_action_date"] = datetime.utcnow()

def reset(confirm: bool = False) -> None:
    """Reset state — απαιτεί confirm=True."""
    if not confirm:
        raise ValueError("Pass confirm=True to reset state")
    init_firebase()
    ref = db.reference('/bot_state')
    ref.set(DEFAULT_STATE)
    logger.warning("State RESET in Firebase")

def summary(state: dict) -> str:
    """Human-readable summary."""
    pos_map = {0: "FLAT", 1: "LONG", -1: "SHORT"}
    pos = pos_map.get(state.get("position", 0), "?")
    lines = [
        f"Position       : {pos}",
        f"Capital        : ${state.get('capital', 0):,.2f}",
    ]
    if state.get("position", 0) != 0:
        lines += [
            f"Entry Price    : ${state.get('entry_price', 0):,.0f}",
            f"Entry Date     : {state.get('entry_date', 'N/A')}",
            f"Qty remaining  : {state.get('qty', 0):.6f}",
            f"Stop Price     : ${state.get('stop_price', 0):,.0f}",
            f"Leverage       : {state.get('leverage', 1):.0f}x",
        ]
    lines += [
        f"MVRV override  : {state.get('mvrv_factor_override', False)}",
        f"Leverage next  : {state.get('leverage_next_long', False)}",
        f"Last action    : {state.get('last_action', 'NONE')}",
        f"Total trades   : {state.get('total_trades', 0)}",
        f"Total PnL      : ${state.get('total_pnl', 0):,.2f}",
    ]
    return "\n".join(lines)
