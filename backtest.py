"""
Backtest — ίδια λογική με Scalping_Bot/leverage_bot03.py
"""
import math
from pathlib import Path

import numpy as np
import pandas as pd

import config
import daily_strategy as ds
import state as state_module


def _build_supertrend_df() -> pd.DataFrame:
    """Φέρνει δεδομένα από WARMUP_DATE και υπολογίζει indicators (static first pass)."""
    df = ds.fetch_btc_yfinance()
    if df.empty:
        df = ds.fetch_btc_with_fallback()
    if df.empty:
        return pd.DataFrame()

    mvrv_path = getattr(config, "MVRV_FILE", None)
    if mvrv_path and Path(mvrv_path).exists():
        mvrv_df = pd.read_csv(mvrv_path, usecols=["date", "mvrv_zscore"])
        mvrv_df["date"] = pd.to_datetime(mvrv_df["date"])
        df = df.merge(mvrv_df, on="date", how="left")
        print(f"Loaded MVRV history: {len(mvrv_df)} rows from {mvrv_path}")
    else:
        print("MVRV history not found — backtest will run without MVRV data.")
        df["mvrv_zscore"] = np.nan

    df = df[df["date"] >= pd.Timestamp(config.WARMUP_DATE)].reset_index(drop=True)
    df["rsi"] = ds.calc_rsi(df["close"], config.RSI_LENGTH)
    df["mvrv_zscore"] = df["mvrv_zscore"].ffill()

    # Static supertrend (ίδιο με leverage_bot03.py first pass — χωρίς MVRV override)
    static_state = {"mvrv_factor_override": False}
    factors = np.array([
        ds.get_factor(row["date"].to_pydatetime(), static_state)
        for _, row in df.iterrows()
    ])
    return ds.calc_supertrend(df, factors)


def run_backtest(
    start_date: str = None,
    end_date: str = None,
    capital: float = None,
    export_csv: bool = True,
):
    """Backtest με την ίδια σειρά ενεργειών και παραμέτρους με leverage_bot03.py."""
    start_date = start_date or config.START_DATE
    capital = config.INITIAL_CAPITAL if capital is None else capital

    df = _build_supertrend_df()
    if df.empty:
        print("No BTC data fetched")
        return None

    if end_date is not None:
        df = df[df["date"] <= pd.Timestamp(end_date)].reset_index(drop=True)

    trade_df = df[df["date"] >= pd.Timestamp(start_date)]
    if trade_df.empty:
        print(f"No data after START_DATE={start_date}")
        return None

    trade_start_idx = int(trade_df.index[0])

    state = state_module.load()
    state["capital"] = capital
    state["position"] = 0
    state["entry_price"] = 0.0
    state["entry_qty"] = 0.0
    state["qty"] = 0.0
    state["stop_price"] = 0.0
    state["current_long_has_partials"] = False
    state["last_partial_sell_date"] = None
    state["partial_sells"] = []
    state["mvrv_factor_override"] = False
    state["leverage_next_long"] = False
    state["total_trades"] = 0
    state["total_pnl"] = 0.0
    state["action_log"] = []

    trade_rows = []

    for i in range(trade_start_idx + 1, len(df)):
        prev = df.iloc[i - 1]
        row = df.iloc[i]

        fill_price = float(row["open"])
        current_date = row["date"].to_pydatetime()
        current_rsi = float(row["rsi"])
        current_mvrv = (
            float(row["mvrv_zscore"])
            if pd.notna(row.get("mvrv_zscore"))
            else math.nan
        )

        long_signal = bool(prev["long_signal"])
        short_signal = bool(prev["short_signal"])
        position = state.get("position", 0)

        # 1. LONG SIGNAL
        if long_signal and position != 1:
            if position == -1:
                msg, pnl = ds.execute_close_position(state, fill_price, current_date, "Signal→Long")
                trade_rows.append({
                    "date": current_date.date(), "type": "CLOSE_SHORT",
                    "price": fill_price, "pnl": pnl,
                    "capital": state["capital"], "position": state["position"],
                })
            msg = ds.execute_open_long(state, fill_price, current_date)
            trade_rows.append({
                "date": current_date.date(), "type": "OPEN_LONG",
                "price": fill_price, "pnl": 0.0,
                "capital": state["capital"], "position": state["position"],
            })
            position = state["position"]

        # 2. SHORT SIGNAL
        elif short_signal and position != -1:
            if ds.can_open_short(state):
                if position == 1:
                    msg, pnl = ds.execute_close_position(state, fill_price, current_date, "Signal→Short")
                    trade_rows.append({
                        "date": current_date.date(), "type": "CLOSE_LONG",
                        "price": fill_price, "pnl": pnl,
                        "capital": state["capital"], "position": state["position"],
                    })
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
                trade_rows.append({
                    "date": current_date.date(), "type": "OPEN_SHORT",
                    "price": fill_price, "pnl": 0.0,
                    "capital": state["capital"], "position": state["position"],
                })
                position = state["position"]
            elif position == 1 and not state.get("current_long_has_partials"):
                msg, pnl = ds.execute_close_position(state, fill_price, current_date, "Exit Only")
                trade_rows.append({
                    "date": current_date.date(), "type": "EXIT_ONLY",
                    "price": fill_price, "pnl": pnl,
                    "capital": state["capital"], "position": state["position"],
                })
                position = state["position"]

        # 3. STOP LOSS
        position = state.get("position", 0)
        stop_price = state.get("stop_price", 0)
        if position == 1 and float(row["low"]) <= stop_price and stop_price > 0:
            msg, pnl = ds.execute_stop_loss(state, stop_price, current_date)
            trade_rows.append({
                "date": current_date.date(), "type": "STOP_LOSS",
                "price": stop_price, "pnl": pnl,
                "capital": state["capital"], "position": state["position"],
            })
        elif position == -1 and float(row["high"]) >= stop_price and stop_price > 0:
            msg, pnl = ds.execute_stop_loss(state, stop_price, current_date)
            trade_rows.append({
                "date": current_date.date(), "type": "STOP_LOSS",
                "price": stop_price, "pnl": pnl,
                "capital": state["capital"], "position": state["position"],
            })

        # 4. MVRV + RSI SHORT EXIT
        position = state.get("position", 0)
        if position == -1 and not math.isnan(current_mvrv):
            if (
                current_mvrv <= config.MVRV_SHORT_EXIT_LEVEL
                and current_rsi < config.RSI_SHORT_EXIT_LEVEL
            ):
                msg, pnl = ds.execute_close_position(state, fill_price, current_date, "MVRV+RSI")
                trade_rows.append({
                    "date": current_date.date(), "type": "MVRV_EXIT",
                    "price": fill_price, "pnl": pnl,
                    "capital": state["capital"], "position": state["position"],
                })

        # 5. PARTIAL SELLS
        position = state.get("position", 0)
        if position == 1 and current_date.year in config.TOP_CYCLE_YEARS:
            ok_sell, sell_pct = ds.should_partial_sell(state, current_date, current_rsi)
            if ok_sell:
                msg = ds.execute_partial_sell(state, fill_price, current_date, sell_pct)
                trade_rows.append({
                    "date": current_date.date(), "type": "PARTIAL_SELL",
                    "price": fill_price, "pnl": 0.0,
                    "capital": state["capital"], "position": state["position"],
                })

    final_value = state["capital"]
    if state.get("position", 0) != 0:
        final_value += state["qty"] * df.iloc[-1]["close"]

    if export_csv:
        out_path = Path("backtest_trade_log.csv")
        pd.DataFrame(trade_rows).to_csv(out_path, index=False)
        print(f"Saved trade log: {out_path}")

    print(
        f"Backtest capital={capital:.2f} final_value={final_value:.2f} "
        f"events={len(trade_rows)}"
    )
    return {"final_value": final_value, "trades": trade_rows, "state": state}


if __name__ == "__main__":
    run_backtest()
