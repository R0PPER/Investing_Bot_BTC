"""
state.py
Persistent state για το bot — αποθηκεύεται σε state.json.
Επιβιώνει reboots, crashes, updates.

Περιέχει:
  - Ανοιχτή θέση (position, entry, qty, partials κλπ)
  - Capital
  - Flags (mvrv_factor_override, leverage_next_long)
  - Trade history summary
"""

import json
import logging
from pathlib import Path
from datetime import datetime, date

logger = logging.getLogger("btc_bot.state")

STATE_FILE = Path(__file__).parent / "state.json"


def _serialize(obj):
    """JSON serializer για dates/datetimes."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _deserialize(d: dict) -> dict:
    """Μετατρέπει ISO strings πίσω σε datetime όπου χρειάζεται."""
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
    # ── Position ──────────────────────────────────────────────────────
    "position":                0,       # 0=flat | 1=long | -1=short
    "entry_price":             0.0,
    "entry_date":              None,
    "entry_value":             0.0,
    "entry_qty":               0.0,
    "entry_fee":               0.0,
    "entry_balance":           0.0,
    "qty":                     0.0,     # remaining qty (after partial sells)
    "stop_price":              0.0,
    "leverage":                1.0,

    # ── Partial sells tracking ────────────────────────────────────────
    "current_long_has_partials": False,
    "last_partial_sell_date":    None,
    "partial_sells":             [],    # list of {date, price, qty_sold, pct_sold, pnl}

    # ── Capital ───────────────────────────────────────────────────────
    "capital":                 10000.0,

    # ── MVRV / Leverage overrides ─────────────────────────────────────
    "mvrv_factor_override":    False,
    "mvrv_override_start_date": None,
    "leverage_next_long":      False,

    # ── Run metadata ──────────────────────────────────────────────────
    "last_run_date":           None,
    "last_action":             "NONE",
    "last_action_date":        None,
    "total_trades":            0,
    "total_pnl":               0.0,

    # ── Audit log (last 30 actions) ───────────────────────────────────
    "action_log":              [],
}


def load() -> dict:
    """Φορτώνει το state από αρχείο. Αν δεν υπάρχει, επιστρέφει default."""
    if not STATE_FILE.exists():
        logger.info("No state file found — using default state")
        return dict(DEFAULT_STATE)

    try:
        with open(STATE_FILE, "r") as f:
            raw = json.load(f)
        state = {**DEFAULT_STATE, **raw}   # merge με defaults για νέα πεδία
        state = _deserialize(state)
        logger.info(f"State loaded — position={state['position']} capital=${state['capital']:,.2f}")
        return state
    except Exception as e:
        logger.error(f"State load error: {e} — using default state")
        return dict(DEFAULT_STATE)


def save(state: dict) -> bool:
    """Αποθηκεύει το state στο αρχείο. Επιστρέφει True αν πέτυχε."""
    # Atomic write: γράφει σε temp file και μετά rename
    tmp = STATE_FILE.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2, default=_serialize)
        tmp.rename(STATE_FILE)
        logger.debug("State saved")
        return True
    except Exception as e:
        logger.error(f"State save error: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def log_action(state: dict, action: str, details: str = "") -> None:
    """Προσθέτει action στο audit log (διατηρεί τα τελευταία 90)."""
    entry = {
        "date": datetime.utcnow().isoformat(),
        "action": action,
        "details": details,
        "capital": state.get("capital", 0),
        "position": state.get("position", 0),
    }
    log = state.get("action_log", [])
    log.append(entry)
    state["action_log"] = log[-90:]   # κρατά τα 90 τελευταία
    state["last_action"]      = action
    state["last_action_date"] = datetime.utcnow()


def reset(confirm: bool = False) -> None:
    """Reset state — ΜΟΝΟ για testing. Απαιτεί confirm=True."""
    if not confirm:
        raise ValueError("Pass confirm=True to reset state")
    if STATE_FILE.exists():
        STATE_FILE.rename(STATE_FILE.with_suffix(".bak"))
    logger.warning("State RESET — backup saved as state.bak")


def summary(state: dict) -> str:
    """Human-readable summary του τρέχοντος state."""
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
            f"Has partials   : {state.get('current_long_has_partials', False)}",
            f"Partial sells  : {len(state.get('partial_sells', []))}",
        ]
    lines += [
        f"MVRV override  : {state.get('mvrv_factor_override', False)}",
        f"Leverage next  : {state.get('leverage_next_long', False)}",
        f"Last action    : {state.get('last_action', 'NONE')}",
        f"Total trades   : {state.get('total_trades', 0)}",
        f"Total PnL      : ${state.get('total_pnl', 0):,.2f}",
    ]
    return "\n".join(lines)