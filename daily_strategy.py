"""
daily_strategy.py
Τρέχει κάθε μέρα στις 00:10 UTC (cron).
Εκτελεί όλη τη trading λογική για το τρέχον daily candle.

Ροή:
  1. Validate MVRV + BTC data
  2. Calculate indicators (RSI, ATR, Supertrend)
  3. Check signals vs current state
  4. Execute action (αλλαγή state + Telegram notification)
  5. Heartbeat
"""

import sys
import logging
import math
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os

import config
import state as state_module
import telegram_notify as tg
import data_validator as validator

# ── Logging ───────────────────────────────────────────────────────────
LOG_DIR = config.LOG_DIR
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"strategy_{datetime.utcnow().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
    force=True,
)
logger = logging.getLogger("btc_bot.strategy")


# ══════════════════════════════════════════════════════════════════════
# 1. DATA FETCHING
# ══════════════════════════════════════════════════════════════════════

def fetch_btc_binance() -> pd.DataFrame:
    """Κατεβάζει BTC daily candles από Binance (primary source)."""
    try:
        params = {
            "symbol": config.BTC_SYMBOL_BINANCE,
            "interval": "1d",
            "limit": 500,
        }
        r = requests.get(config.BINANCE_KLINES_URL, params=params, timeout=15)
        r.raise_for_status()
        raw = r.json()
        df = pd.DataFrame(raw, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades", "taker_base", "taker_quote", "ignore"
        ])
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms").dt.normalize()
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df = df[["date", "open", "high", "low", "close"]].sort_values("date").reset_index(drop=True)
        logger.info(f"Binance: {len(df)} candles — latest={df['date'].iloc[-1].date()}")
        return df
    except Exception as e:
        logger.warning(f"Binance fetch failed: {e}")
        return pd.DataFrame()


def fetch_btc_yfinance() -> pd.DataFrame:
    """Fallback: yfinance."""
    try:
        import yfinance as yf
        raw = yf.download(config.BTC_SYMBOL_YF, start=config.WARMUP_DATE,
                          interval="1d", progress=False)
        raw = raw.reset_index()
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [c[0].lower() for c in raw.columns]
        else:
            raw.columns = [c.lower() for c in raw.columns]
        df = raw[["date", "open", "high", "low", "close"]].dropna().reset_index(drop=True)
        df["date"] = pd.to_datetime(df["date"])
        for c in ["open", "high", "low", "close"]:
            df[c] = df[c].astype(float)
        df = df.sort_values("date").reset_index(drop=True)
        logger.info(f"yfinance: {len(df)} candles — latest={df['date'].iloc[-1].date()}")
        return df
    except Exception as e:
        logger.warning(f"yfinance fetch failed: {e}")
        return pd.DataFrame()


def fetch_btc_with_fallback() -> pd.DataFrame:
    """Δοκιμάζει Binance → yfinance. Επιστρέφει empty DataFrame αν αποτύχουν όλα."""
    df = fetch_btc_binance()
    if not df.empty:
        return df
    logger.warning("Falling back to yfinance...")
    df = fetch_btc_yfinance()
    return df


# ══════════════════════════════════════════════════════════════════════
# 2. INDICATORS
# ══════════════════════════════════════════════════════════════════════

def calc_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    alpha = 1 / length
    avg_gain = delta.clip(lower=0).ewm(alpha=alpha, adjust=False).mean()
    avg_loss = (-delta).clip(lower=0).ewm(alpha=alpha, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(50)
    rsi = rsi.where(avg_loss != 0, 100)
    rsi = rsi.where(avg_gain != 0, 0)
    return rsi


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = np.full(len(tr), np.nan)
    if len(tr) >= period:
        atr[period - 1] = tr.iloc[:period].mean()
        for i in range(period, len(tr)):
            atr[i] = (atr[i-1] * (period-1) + tr.iloc[i]) / period
    return pd.Series(atr, index=high.index)


def get_factor(dt: datetime, state: dict) -> float:
    """Επιστρέφει τον Supertrend factor για μια ημερομηνία."""
    if dt.year in config.TOP_CYCLE_YEARS and dt.month in config.FACTOR_DECAY_MONTHS:
        return config.FACTOR_DECAY_MONTHS[dt.month]
    if state.get("mvrv_factor_override", False):
        return config.MVRV_EXIT_BASE_FACTOR
    return config.BASE_FACTOR


def calc_supertrend(df: pd.DataFrame, factors: np.ndarray) -> pd.DataFrame:
    """Υπολογίζει Supertrend με δυναμικό factor array."""
    atr = wilder_atr(df["high"], df["low"], df["close"], config.ATR_PERIOD).values
    hl2 = ((df["high"] + df["low"]) / 2).values
    close = df["close"].values
    n = len(df)

    raw_upper = hl2 + factors * atr
    raw_lower = hl2 - factors * atr
    upper_band = np.full(n, np.nan)
    lower_band = np.full(n, np.nan)
    supertrend = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)

    for i in range(1, n):
        if np.isnan(atr[i]):
            continue
        pl = lower_band[i-1] if not np.isnan(lower_band[i-1]) else raw_lower[i]
        pu = upper_band[i-1] if not np.isnan(upper_band[i-1]) else raw_upper[i]
        pc = close[i-1]

        lower_band[i] = raw_lower[i] if raw_lower[i] > pl or pc < pl else pl
        upper_band[i] = raw_upper[i] if raw_upper[i] < pu or pc > pu else pu

        if np.isnan(atr[i-1]):
            direction[i] = 1
        elif supertrend[i-1] == upper_band[i-1]:
            direction[i] = -1 if close[i] > upper_band[i] else 1
        else:
            direction[i] = 1 if close[i] < lower_band[i] else -1

        supertrend[i] = lower_band[i] if direction[i] == -1 else upper_band[i]

    out = df.copy()
    out["factor"] = factors
    out["st"] = supertrend
    out["dir"] = direction
    out["long_signal"] = (out["dir"] < 0) & (out["dir"].shift(1) > 0)
    out["short_signal"] = (out["dir"] > 0) & (out["dir"].shift(1) < 0)
    return out


# ══════════════════════════════════════════════════════════════════════
# 3. TRADING LOGIC
# ══════════════════════════════════════════════════════════════════════

def can_open_short(state: dict) -> bool:
    """Short ΜΟΝΟ αν είμαστε εντός long με ≥1 partial sell."""
    return state.get("position") == 1 and state.get("current_long_has_partials", False)


def should_partial_sell(state: dict, current_date: datetime, rsi: float) -> tuple[bool, float]:
    if state.get("position") != 1:
        return False, 0.0
    month = current_date.month
    if month not in config.PARTIAL_SELL_MONTHS:
        return False, 0.0
    if rsi <= config.RSI_OVERBOUGHT:
        return False, 0.0
    if current_date.year not in config.TOP_CYCLE_YEARS:
        return False, 0.0
    last_sell = state.get("last_partial_sell_date")
    if last_sell is not None:
        if isinstance(last_sell, str):
            last_sell = datetime.fromisoformat(last_sell)
        if (current_date - last_sell).days < config.PARTIAL_SELL_COOLDOWN_DAYS:
            return False, 0.0
    pct = config.PARTIAL_SELL_PCT_BY_MONTH.get(month, 0.15)
    return True, pct


def execute_open_long(state: dict, price: float, date: datetime) -> str:
    capital = state["capital"]
    if capital <= 0:
        return "INSUFFICIENT_CAPITAL"

    lev = config.LEVERAGE_NEXT_LONG if state.get("leverage_next_long") else config.DEFAULT_LEVERAGE
    if lev > 1:
        entry_value = min(
            max(capital * config.RISK_FRACTION * lev, config.MIN_TRADE_NOTIONAL),
            capital * lev * 2,
        )
    else:
        entry_value = min(
            max(capital * config.RISK_FRACTION, config.MIN_TRADE_NOTIONAL),
            capital,
        )
    entry_fee = entry_value * config.COMMISSION_PCT
    qty = entry_value / price

    state["position"] = 1
    state["entry_price"] = price
    state["entry_date"] = date
    state["entry_value"] = entry_value
    state["entry_qty"] = qty
    state["qty"] = qty
    state["entry_fee"] = entry_fee
    state["entry_balance"] = capital
    state["stop_price"] = price * (1 - config.STOP_LOSS_PCT)
    state["leverage"] = lev
    state["current_long_has_partials"] = False
    state["last_partial_sell_date"] = None
    state["partial_sells"] = []
    state["capital"] -= entry_fee

    if state.get("mvrv_factor_override"):
        state["mvrv_factor_override"] = False
    state["leverage_next_long"] = False

    lev_str = f" [⚡{lev:.0f}x LEVERAGE]" if lev > 1 else ""
    return f"LONG OPENED @ ${price:,.0f}{lev_str}"


def execute_close_position(state: dict, price: float, date: datetime, reason: str) -> tuple[str, float]:
    """Κλείνει την τρέχουσα θέση. Επιστρέφει (message, pnl)."""
    pos = state["position"]
    qty = state["qty"]
    entry = state["entry_price"]

    if pos == 1:
        gross_pnl = qty * (price - entry)
    else:
        gross_pnl = qty * (entry - price)

    exit_fee = qty * price * config.COMMISSION_PCT
    net_pnl = gross_pnl - exit_fee
    new_capital = state["capital"] + net_pnl

    if reason == "MVRV+RSI" and pos == -1:
        state["mvrv_factor_override"] = True
        state["mvrv_override_start_date"] = date
        state["leverage_next_long"] = True

    prev_pos = state["position"]
    state["capital"] = new_capital
    state["position"] = 0
    state["entry_price"] = 0.0
    state["entry_date"] = None
    state["entry_value"] = 0.0
    state["entry_qty"] = 0.0
    state["qty"] = 0.0
    state["entry_fee"] = 0.0
    state["entry_balance"] = 0.0
    state["stop_price"] = 0.0
    state["leverage"] = config.DEFAULT_LEVERAGE
    state["last_partial_sell_date"] = None
    state["partial_sells"] = []
    if prev_pos == -1:
        state["current_long_has_partials"] = False
    state["total_trades"] += 1
    state["total_pnl"] += net_pnl

    pnl_str = f"+${net_pnl:,.0f}" if net_pnl >= 0 else f"-${abs(net_pnl):,.0f}"
    return f"CLOSED [{reason}] @ ${price:,.0f} | PnL: {pnl_str}", net_pnl


def execute_partial_sell(state: dict, price: float, date: datetime, pct: float) -> str:
    qty = state["qty"]
    entry = state["entry_price"]
    sell_qty = qty * pct
    gross = sell_qty * (price - entry)
    fee = sell_qty * price * config.COMMISSION_PCT
    pnl = gross - fee

    state["capital"] += pnl
    state["qty"] -= sell_qty
    state["current_long_has_partials"] = True
    state["last_partial_sell_date"] = date

    ps_record = {
        "date": date.isoformat(),
        "price": price,
        "qty_sold": sell_qty,
        "pct_sold": pct * 100,
        "pnl": pnl,
    }
    state.setdefault("partial_sells", []).append(ps_record)

    return (f"PARTIAL SELL {pct*100:.0f}% @ ${price:,.0f} | "
            f"PnL: ${pnl:,.0f} | SL→breakeven @ ${entry:,.0f}")


def execute_stop_loss(state: dict, price: float, date: datetime) -> tuple[str, float]:
    return execute_close_position(state, price, date, "Stop Loss")


# ══════════════════════════════════════════════════════════════════════
# 4. MAIN (χωρίς idempotency)
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    today = datetime.now(timezone.utc)
    logger.info("=" * 65)
    logger.info(f"  BTC BOT — Daily Strategy Run — {today.strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info("=" * 65)

    # ── Load state ────────────────────────────────────────────────────
    state = state_module.load()
    logger.info(f"State: position={state['position']} capital=${state['capital']:,.2f}")

    # ── IDEMPOTENCY REMOVED ──────────────────────────────────────────
    # Το bot θα τρέχει κάθε φορά, είτε cron είτε manual
    # Δεν ελέγχουμε αν έχει ήδη τρέξει σήμερα

    # ── Fetch BTC data ────────────────────────────────────────────────
    logger.info("Fetching BTC daily candles...")
    btc_df = fetch_btc_with_fallback()

    if btc_df.empty:
        msg = "❌ Cannot fetch BTC data — strategy skipped"
        logger.error(msg)
        tg.alert(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID, "⚠️ BTC Data Error", msg)
        return 2

    # ── Validate BTC candle ───────────────────────────────────────────
    ok, reason = validator.check_btc_candle(btc_df, today)
    if not ok:
        logger.error(f"BTC candle validation failed: {reason}")
        tg.info(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID,
                "⚠️ BTC Candle Missing", f"Strategy skipped.\n{reason}")
        return 1

    # ── Validate MVRV ─────────────────────────────────────────────────
    ok, reason = validator.check_mvrv(str(config.MVRV_FILE), today)
    if not ok:
        logger.error(f"MVRV validation failed: {reason}")
        tg.info(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID,
                "⚠️ MVRV Data Missing", f"Strategy skipped.\n{reason}")
        return 1

    # ── Merge MVRV into BTC df ────────────────────────────────────────
    mvrv_df = pd.read_csv(config.MVRV_FILE)
    mvrv_df["date"] = pd.to_datetime(mvrv_df["date"])
    btc_df = btc_df.merge(mvrv_df, on="date", how="left")
    btc_df["mvrv_zscore"] = btc_df["mvrv_zscore"].ffill()

    # ── Calculate Indicators ──────────────────────────────────────────
    btc_df = btc_df[btc_df["date"] >= pd.Timestamp(config.WARMUP_DATE)].reset_index(drop=True)
    btc_df["rsi"] = calc_rsi(btc_df["close"], config.RSI_LENGTH)

    factors = np.array([get_factor(row["date"].to_pydatetime(), state)
                        for _, row in btc_df.iterrows()])
    btc_df = calc_supertrend(btc_df, factors)

    signal_df = btc_df[btc_df["date"] >= pd.Timestamp(config.START_DATE)].reset_index(drop=True)
    if len(signal_df) < 2:
        logger.error("Not enough data after START_DATE")
        return 2

    prev_bar = signal_df.iloc[-2]
    curr_bar = signal_df.iloc[-1]

    long_signal = bool(prev_bar["long_signal"])
    short_signal = bool(prev_bar["short_signal"])
    fill_price = float(curr_bar["open"])
    current_price = float(curr_bar["close"])
    current_date = curr_bar["date"].to_pydatetime()
    current_rsi = float(curr_bar["rsi"])
    current_mvrv = float(curr_bar["mvrv_zscore"]) if pd.notna(curr_bar.get("mvrv_zscore")) else float("nan")
    current_factor = float(curr_bar["factor"])
    current_atr = float(wilder_atr(btc_df["high"], btc_df["low"], btc_df["close"], config.ATR_PERIOD).iloc[-1])

    logger.info(f"Signal bar: {prev_bar['date'].date()} → long={long_signal} short={short_signal}")
    mvrv_label = "N/A" if math.isnan(current_mvrv) else f"{current_mvrv:.3f}"
    logger.info(f"Fill price: ${fill_price:,.0f} | RSI={current_rsi:.1f} | MVRV={mvrv_label} | Factor={current_factor:.1f}")

    ok, errors = validator.full_check(
        str(config.MVRV_FILE), btc_df,
        current_rsi, current_atr, current_factor, today
    )
    if not ok:
        err_str = "\n".join(errors)
        logger.error(f"Indicator validation failed:\n{err_str}")
        tg.alert(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID,
                 "⚠️ Data Integrity Error", f"Strategy skipped:\n{err_str}")
        return 2

    actions_taken = []
    position = state.get("position", 0)

    # LONG SIGNAL
    if long_signal and position != 1:
        if position == -1:
            msg, pnl = execute_close_position(state, fill_price, current_date, "Signal→Long")
            actions_taken.append(("CLOSE_SHORT", msg))
            state_module.log_action(state, "CLOSE_SHORT", msg)
            logger.info(f"🔴→🟢 {msg}")
        msg = execute_open_long(state, fill_price, current_date)
        actions_taken.append(("OPEN_LONG", msg))
        state_module.log_action(state, "OPEN_LONG", msg)
        logger.info(f"🟢 {msg}")
        position = 1

    # SHORT SIGNAL
    elif short_signal and position != -1:
        if can_open_short(state):
            if position == 1:
                msg, pnl = execute_close_position(state, fill_price, current_date, "Signal→Short")
                actions_taken.append(("CLOSE_LONG", msg))
                state_module.log_action(state, "CLOSE_LONG", msg)
                logger.info(f"🟢→🔴 {msg}")
            msg = "SHORT OPENED @ ${:,.0f}".format(fill_price)
            state["position"] = -1
            state["entry_price"] = fill_price
            state["entry_date"] = current_date
            qty_ = state["capital"] / fill_price
            state["entry_qty"] = qty_
            state["qty"] = qty_
            state["entry_fee"] = state["capital"] * config.COMMISSION_PCT
            state["entry_balance"] = state["capital"]
            state["stop_price"] = fill_price * (1 + config.STOP_LOSS_PCT)
            state["capital"] -= state["entry_fee"]
            actions_taken.append(("OPEN_SHORT", msg))
            state_module.log_action(state, "OPEN_SHORT", msg)
            logger.info(f"🔴 {msg}")
            position = -1

        elif position == 1 and not state.get("current_long_has_partials"):
            msg, pnl = execute_close_position(state, fill_price, current_date, "Exit Only")
            actions_taken.append(("EXIT_ONLY", msg))
            state_module.log_action(state, "EXIT_ONLY", msg)
            logger.info(f"🟠 {msg}")
            position = 0
        else:
            logger.info(f"⚪ Short signal skipped (flat position)")
            state_module.log_action(state, "SKIP_SHORT", f"flat @ ${fill_price:,.0f}")

    # STOP LOSS
    stop_price = state.get("stop_price", 0)
    if position == 1 and float(curr_bar["low"]) <= stop_price and stop_price > 0:
        msg, pnl = execute_stop_loss(state, stop_price, current_date)
        actions_taken.append(("STOP_LOSS", msg))
        state_module.log_action(state, "STOP_LOSS", msg)
        logger.info(f"⚠️ {msg}")
        position = 0

    elif position == -1 and float(curr_bar["high"]) >= stop_price and stop_price > 0:
        msg, pnl = execute_stop_loss(state, stop_price, current_date)
        actions_taken.append(("STOP_LOSS", msg))
        state_module.log_action(state, "STOP_LOSS", msg)
        logger.info(f"⚠️ {msg}")
        position = 0

    # MVRV + RSI SHORT EXIT
    if position == -1 and not math.isnan(current_mvrv):
        if current_mvrv <= config.MVRV_SHORT_EXIT_LEVEL and current_rsi < config.RSI_SHORT_EXIT_LEVEL:
            msg, pnl = execute_close_position(state, fill_price, current_date, "MVRV+RSI")
            actions_taken.append(("MVRV_EXIT", msg))
            state_module.log_action(state, "MVRV_EXIT", msg)
            logger.info(f"🔮 {msg}")
            position = 0

    # PARTIAL SELL
    if position == 1 and current_date.year in config.TOP_CYCLE_YEARS:
        ok_sell, sell_pct = should_partial_sell(state, current_date, current_rsi)
        if ok_sell:
            msg = execute_partial_sell(state, fill_price, current_date, sell_pct)
            actions_taken.append(("PARTIAL_SELL", msg))
            state_module.log_action(state, "PARTIAL_SELL", msg)
            logger.info(f"⭐ {msg}")

    if not actions_taken:
        logger.info("NO ACTION today")
        state_module.log_action(state, "NO_ACTION", f"BTC=${current_price:,.0f}")

    state["last_run_date"] = datetime.utcnow()

    if not config.DRY_RUN:
        if not state_module.save(state):
            logger.error("CRITICAL: Failed to save state!")
            tg.alert(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID,
                     "🚨 CRITICAL: State Save Failed",
                     "Bot executed actions but state could not be saved!\nManual intervention required.")
    else:
        logger.info("[DRY RUN] State NOT saved")

    pos_map = {0: "FLAT", 1: "LONG", -1: "SHORT"}
    pos_str = pos_map.get(state.get("position", 0), "?")

    for action_type, action_msg in actions_taken:
        emoji_map = {
            "OPEN_LONG":    "🟢",
            "CLOSE_LONG":   "🟢➡️⬛",
            "OPEN_SHORT":   "🔴",
            "CLOSE_SHORT":  "🔴➡️⬛",
            "STOP_LOSS":    "⚠️",
            "MVRV_EXIT":    "🔮",
            "PARTIAL_SELL": "⭐",
            "EXIT_ONLY":    "🟠",
        }
        emoji = emoji_map.get(action_type, "ℹ️")
        dry_tag = " [DRY RUN]" if config.DRY_RUN else ""
        tg.alert(
            config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID,
            f"{emoji} {action_type.replace('_', ' ')}{dry_tag}",
            f"{action_msg}\n\n"
            f"Capital: <b>${state['capital']:,.2f}</b>\n"
            f"Position: <b>{pos_str}</b>\n"
            f"BTC: <b>${current_price:,.0f}</b>",
        )

    action_summary = ", ".join([a[0] for a in actions_taken]) if actions_taken else "NO ACTION"
    run_id = os.getenv("BOT_RUN_ID", datetime.utcnow().strftime("%H%M%S"))
 
    tg.heartbeat(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID, {
        "position":  pos_str,
        "capital":   state["capital"],
        "btc_price": current_price,
        "mvrv":      f"{current_mvrv:.3f}" if not math.isnan(current_mvrv) else "N/A",
        "rsi":       f"{current_rsi:.1f}",
        "factor":    f"{current_factor:.1f}",
        "action":    action_summary,
        "run_id":    run_id,     # ← ΝΕΟ: unique per run
    })

    logger.info("=" * 65)
    logger.info(f"  Run complete — actions: {action_summary}")
    logger.info(f"  Capital: ${state['capital']:,.2f} | Position: {pos_str}")
    logger.info("=" * 65)
    return 0


if __name__ == "__main__":
    sys.exit(main())
