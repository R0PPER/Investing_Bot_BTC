"""
data_validator.py
Integrity checks για BTC candles και MVRV data.
Επιστρέφει (ok: bool, reason: str).
"""

import pandas as pd
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger("btc_bot.validator")


def check_mvrv(mvrv_path: str, today: datetime) -> tuple[bool, str]:
    """
    Ελέγχει ότι το MVRV CSV έχει record για σήμερα.
    today: naive ή aware datetime — θεωρείται UTC.
    """
    try:
        df = pd.read_csv(mvrv_path, usecols=["date", "mvrv_zscore"])
        df["date"] = pd.to_datetime(df["date"])
    except FileNotFoundError:
        return False, f"MVRV file not found: {mvrv_path}"
    except Exception as e:
        return False, f"MVRV file read error: {e}"

    today_date = pd.Timestamp(today.date())
    if today_date not in df["date"].values:
        latest = df["date"].max()
        latest_val = float(df.loc[df["date"] == latest, "mvrv_zscore"].iloc[0])
        logger.warning(f"MVRV for {today.date()} not yet available; using latest known value from {latest.date()}: {latest_val:.4f}")
        return True, f"MVRV latest available={latest.date()} value={latest_val:.4f}"

    row = df[df["date"] == today_date].iloc[0]
    val = row["mvrv_zscore"]
    if pd.isna(val):
        return False, f"MVRV is NaN for {today.date()}"

    logger.info(f"MVRV OK for {today.date()}: {val:.4f}")
    return True, f"MVRV={val:.4f}"


def check_btc_candle(df: pd.DataFrame, today: datetime) -> tuple[bool, str]:
    """
    Ελέγχει ότι το BTC DataFrame έχει κλειστό candle για σήμερα.
    Το daily candle κλείνει 00:00 UTC — άρα ελέγχουμε για today-1.
    Για trading στρατηγική, θέλουμε το κλειστό candle χθες (confirmed).
    """
    if df is None or df.empty:
        return False, "BTC DataFrame is empty"

    # Το τελευταίο confirmed candle είναι χθες
    expected = pd.Timestamp((today - timedelta(days=1)).date())
    df_dates = pd.to_datetime(df["date"] if "date" in df.columns else df.index)
    latest   = df_dates.max()

    if latest < expected:
        return False, f"BTC candle missing — latest={latest.date()}, expected>={expected.date()}"

    # Ελέγχουμε OHLC integrity για το τελευταίο candle
    last = df.iloc[-1]
    for col in ["open", "high", "low", "close"]:
        col_name = col
        if col_name not in df.columns:
            return False, f"Missing column: {col_name}"
        val = float(last[col_name])
        if pd.isna(val) or val <= 0:
            return False, f"Invalid {col_name}={val} in last candle"

    close_price = float(last["close"])
    logger.info(f"BTC candle OK — latest={latest.date()}, close=${close_price:,.0f}")
    return True, f"close=${close_price:,.0f}"


def check_indicators(rsi: float, atr: float, factor: float) -> tuple[bool, str]:
    """Ελέγχει ότι τα indicators δεν είναι NaN/invalid."""
    import math
    checks = {"RSI": rsi, "ATR": atr, "Factor": factor}
    for name, val in checks.items():
        if val is None or math.isnan(val) or val <= 0:
            return False, f"{name} is invalid: {val}"
    logger.info(f"Indicators OK — RSI={rsi:.1f} ATR={atr:.0f} Factor={factor:.1f}")
    return True, "OK"


def full_check(mvrv_path: str, btc_df: pd.DataFrame,
               rsi: float, atr: float, factor: float,
               today: datetime) -> tuple[bool, list[str]]:
    """
    Τρέχει όλα τα checks. Επιστρέφει (all_ok, list_of_errors).
    """
    errors = []

    ok, msg = check_mvrv(mvrv_path, today)
    if not ok:
        errors.append(f"MVRV: {msg}")

    ok, msg = check_btc_candle(btc_df, today)
    if not ok:
        errors.append(f"BTC: {msg}")

    ok, msg = check_indicators(rsi, atr, factor)
    if not ok:
        errors.append(f"Indicators: {msg}")

    return len(errors) == 0, errors