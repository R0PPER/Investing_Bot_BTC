"""
config.py
Κεντρικές παράμετροι για το BTC bot.
ΜΟΝΟ εδώ αλλάζεις ρυθμίσεις — δεν αγγίζεις τα άλλα αρχεία.
"""

from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
MVRV_FILE  = BASE_DIR / "mvrv_zscore_full_history.csv"
STATE_FILE = BASE_DIR / "state.json"
LOG_DIR    = BASE_DIR / "logs"

# ── Telegram ──────────────────────────────────────────────────────────
# Βάλε τα δικά σου tokens εδώ ή χρησιμοποίησε env variables (προτιμότερο)
import os
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "8752009499:AAHLMnB8FL_gDSycY8bjU0mrEeFz-1Lp0RY")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1928885179")

# ── MVRV API ──────────────────────────────────────────────────────────
MVRV_API_KEYS = [
    os.getenv("MVRV_API_KEY_1", "y8zSqnwzJG"),
    os.getenv("MVRV_API_KEY_2", "pgtqoKHW4h"),
    os.getenv("MVRV_API_KEY_3", "ZM9FGh8kVM"),
]
MVRV_API_BASE = "https://api.bgeometrics.com/v1/mvrv-zscore"

# ── Trading Parameters ────────────────────────────────────────────────
INITIAL_CAPITAL       = 10000.0
RISK_FRACTION         = 1.00
MIN_TRADE_NOTIONAL    = 10000.0
COMMISSION_PCT        = 0.001
STOP_LOSS_PCT         = 0.25

# ── Supertrend ────────────────────────────────────────────────────────
ATR_PERIOD            = 14
BASE_FACTOR           = 8.6
MVRV_EXIT_BASE_FACTOR = 6.0    # factor override μετά από MVRV+RSI exit

# Dynamic factor decay (Σεπ→Δεκ των top cycle years)
FACTOR_DECAY_MONTHS = {
    9:  7.5,
    10: 6.2,
    11: 5.0,
    12: 4.5,
}
TOP_CYCLE_YEARS = [2017, 2021, 2025, 2029]

# ── Leverage ──────────────────────────────────────────────────────────
DEFAULT_LEVERAGE    = 1.0
LEVERAGE_NEXT_LONG  = 3.0   # x leverage στο long μετά από MVRV+RSI short exit

# ── RSI ───────────────────────────────────────────────────────────────
RSI_LENGTH           = 14
RSI_OVERBOUGHT       = 64
RSI_SHORT_EXIT_LEVEL = 30

# ── MVRV thresholds ───────────────────────────────────────────────────
MVRV_SHORT_EXIT_LEVEL = -0.30

# ── Partial Sells ─────────────────────────────────────────────────────
PARTIAL_SELL_COOLDOWN_DAYS = 7
PARTIAL_SELL_MONTHS        = [7, 8, 9, 10, 11, 12]
PARTIAL_SELL_PCT_BY_MONTH  = {
    7: 0.05,
    8: 0.12,
    9: 0.20,
    10: 0.20,
    11: 0.20,
    12: 0.50,
}

# ── BTC Data Source ───────────────────────────────────────────────────
# Primary: Binance public API (no auth needed for klines)
# Fallback: yfinance
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BTC_SYMBOL_BINANCE = "BTCUSDT"
BTC_SYMBOL_YF      = "BTC-USD"
WARMUP_DATE        = "2013-01-01"
START_DATE         = "2015-10-11"

# ── Scheduler ─────────────────────────────────────────────────────────
# Times in UTC
MVRV_UPDATE_TIME   = "00:05"   # update_mvrv.py
STRATEGY_RUN_TIME  = "00:10"   # daily_strategy.py
MVRV_RETRY_MINUTES = 30        # αν το MVRV δεν είναι έτοιμο, retry κάθε N λεπτά
MVRV_MAX_RETRIES   = 6         # max retries (6 * 30min = 3h)

# ── Dry Run ───────────────────────────────────────────────────────────
# True → υπολογίζει αλλά ΔΕΝ αλλάζει state (μόνο Telegram notification)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"