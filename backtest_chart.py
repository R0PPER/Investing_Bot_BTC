"""
Προσωρινό backtest + chart — για οπτική επαλήθευση πριν το cloud deploy.
Τρέξε:  python backtest_chart.py
Βγάζει:  backtest_chart.html  +  backtest_trades_verify.csv
Μπορείς να το σβήσεις μετά την επαλήθευση.
"""
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import config
import daily_strategy as ds
import state as state_module
from backtest import _build_supertrend_df


def _record_close(
    state: dict,
    exit_price: float,
    exit_date: datetime,
    exit_i: int,
    exit_reason: str,
    df: pd.DataFrame,
    partial_sells_this_trade: list,
    current_leverage: float,
    is_open: bool = False,
) -> dict:
    """Καταγράφει trade όπως στο leverage_bot03.py (για chart + CSV)."""
    pos = state.get("_closing_pos", state.get("position", 0))
    if pos == 0:
        return {}

    entry_price = state.get("_closing_entry_price", 0.0)
    entry_date = state.get("_closing_entry_date")
    entry_i = state.get("_closing_entry_i", 0)
    entry_balance = state.get("_closing_entry_balance", 0.0)
    entry_qty = state.get("_closing_entry_qty", 0.0)
    entry_value = state.get("_closing_entry_value", 0.0)
    entry_fee = state.get("_closing_entry_fee", 0.0)
    qty = state.get("_closing_qty", 0.0)

    if pos == 1:
        gross_pnl = qty * (exit_price - entry_price)
        held = df.iloc[entry_i: exit_i + 1]
        favorable = (held["high"].max() - entry_price) * qty
        adverse = (held["low"].min() - entry_price) * qty
        trade_type = "Long"
        leverage_used = current_leverage
    else:
        gross_pnl = qty * (entry_price - exit_price)
        held = df.iloc[entry_i: exit_i + 1]
        favorable = (entry_price - held["low"].min()) * qty
        adverse = (entry_price - held["high"].max()) * qty
        trade_type = "Short"
        leverage_used = 1.0

    exit_fee = qty * exit_price * config.COMMISSION_PCT
    new_capital = state["capital"]
    net_pnl = new_capital - entry_balance

    return {
        "type": trade_type,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "qty": entry_qty,
        "remaining_qty": qty,
        "entry_value": entry_value,
        "entry_balance": entry_balance,
        "pnl": net_pnl,
        "gross_pnl": gross_pnl,
        "entry_fee": entry_fee,
        "exit_fee": exit_fee,
        "capital_after": new_capital,
        "favorable": favorable,
        "adverse": adverse,
        "exit_reason": exit_reason,
        "leverage": leverage_used,
        "partial_sells": partial_sells_this_trade.copy(),
        "had_partial_sells": bool(partial_sells_this_trade) if trade_type == "Long" else None,
        **({"open": True} if is_open else {}),
    }


def _snapshot_before_close(state: dict, entry_i: int):
    state["_closing_pos"] = state["position"]
    state["_closing_entry_price"] = state["entry_price"]
    state["_closing_entry_date"] = state["entry_date"]
    state["_closing_entry_i"] = entry_i
    state["_closing_entry_balance"] = state["entry_balance"]
    state["_closing_entry_qty"] = state["entry_qty"]
    state["_closing_entry_value"] = state.get("entry_value", 0.0)
    state["_closing_entry_fee"] = state.get("entry_fee", 0.0)
    state["_closing_qty"] = state["qty"]


def run_backtest_with_chart(
    start_date: str = None,
    capital: float = None,
    html_out: str = "backtest_chart.html",
    csv_out: str = "backtest_trades_verify.csv",
):
    start_date = start_date or config.START_DATE
    capital = config.INITIAL_CAPITAL if capital is None else capital

    print("Fetching data & indicators...")
    df = _build_supertrend_df()
    if df.empty:
        print("No BTC data fetched")
        return None

    trade_df = df[df["date"] >= pd.Timestamp(start_date)]
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

    trades = []
    exit_only_events = []
    stop_loss_events = []
    mvrv_exit_events = []
    leverage_events = []
    all_partial_sells = []
    partial_sells_this_trade = []
    entry_i = None
    current_leverage = config.DEFAULT_LEVERAGE
    shorts_opened = 0
    exit_only_count = 0

    print("Running backtest...")
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
                _snapshot_before_close(state, entry_i)
                ds.execute_close_position(state, fill_price, current_date, "Signal (->Long)")
                trades.append(_record_close(
                    state, fill_price, current_date, i, "Signal (->Long)",
                    df, partial_sells_this_trade, current_leverage,
                ))
                partial_sells_this_trade = []

            lev_before = config.LEVERAGE_NEXT_LONG if state.get("leverage_next_long") else config.DEFAULT_LEVERAGE
            ds.execute_open_long(state, fill_price, current_date)
            current_leverage = state.get("leverage", config.DEFAULT_LEVERAGE)
            entry_i = i
            partial_sells_this_trade = []

            if current_leverage == config.LEVERAGE_NEXT_LONG:
                leverage_events.append({
                    "date": current_date, "price": fill_price, "leverage": current_leverage,
                })
            position = state["position"]

        # 2. SHORT SIGNAL
        elif short_signal and position != -1:
            if ds.can_open_short(state):
                shorts_opened += 1
                if position == 1:
                    _snapshot_before_close(state, entry_i)
                    ds.execute_close_position(state, fill_price, current_date, "Signal (->Short)")
                    trades.append(_record_close(
                        state, fill_price, current_date, i, "Signal (->Short)",
                        df, partial_sells_this_trade, current_leverage,
                    ))
                    partial_sells_this_trade = []
                    current_leverage = config.DEFAULT_LEVERAGE

                state["position"] = -1
                state["entry_price"] = fill_price
                state["entry_date"] = current_date
                qty_ = state["capital"] / fill_price
                state["entry_qty"] = qty_
                state["qty"] = qty_
                state["entry_fee"] = state["capital"] * config.COMMISSION_PCT
                state["entry_balance"] = state["capital"]
                state["entry_value"] = state["capital"]
                state["stop_price"] = fill_price * (1 + config.STOP_LOSS_PCT)
                state["capital"] -= state["entry_fee"]
                entry_i = i
                position = -1

            elif position == 1 and not state.get("current_long_has_partials"):
                exit_only_count += 1
                exit_only_events.append({"date": current_date, "price": fill_price})
                _snapshot_before_close(state, entry_i)
                ds.execute_close_position(state, fill_price, current_date, "Exit Only (no partials)")
                trades.append(_record_close(
                    state, fill_price, current_date, i, "Exit Only (no partials)",
                    df, partial_sells_this_trade, current_leverage,
                ))
                partial_sells_this_trade = []
                current_leverage = config.DEFAULT_LEVERAGE
                position = 0

        # 3. STOP LOSS
        position = state.get("position", 0)
        stop_price = state.get("stop_price", 0)
        if position == 1 and float(row["low"]) <= stop_price and stop_price > 0:
            lp = (stop_price - state["entry_price"]) / state["entry_price"] * 100
            stop_loss_events.append({
                "date": current_date, "price": stop_price,
                "loss_percent": lp, "trade_type": "Long",
            })
            _snapshot_before_close(state, entry_i)
            ds.execute_stop_loss(state, stop_price, current_date)
            trades.append(_record_close(
                state, stop_price, current_date, i, "Stop Loss",
                df, partial_sells_this_trade, current_leverage,
            ))
            partial_sells_this_trade = []
            current_leverage = config.DEFAULT_LEVERAGE

        elif position == -1 and float(row["high"]) >= stop_price and stop_price > 0:
            lp = (state["entry_price"] - stop_price) / state["entry_price"] * 100
            stop_loss_events.append({
                "date": current_date, "price": stop_price,
                "loss_percent": lp, "trade_type": "Short",
            })
            _snapshot_before_close(state, entry_i)
            ds.execute_stop_loss(state, stop_price, current_date)
            trades.append(_record_close(
                state, stop_price, current_date, i, "Stop Loss",
                df, partial_sells_this_trade, current_leverage,
            ))
            partial_sells_this_trade = []
            current_leverage = config.DEFAULT_LEVERAGE

        # 4. MVRV + RSI SHORT EXIT
        position = state.get("position", 0)
        if position == -1 and not math.isnan(current_mvrv):
            if (
                current_mvrv <= config.MVRV_SHORT_EXIT_LEVEL
                and current_rsi < config.RSI_SHORT_EXIT_LEVEL
            ):
                mvrv_exit_events.append({"date": current_date, "price": fill_price})
                _snapshot_before_close(state, entry_i)
                ds.execute_close_position(state, fill_price, current_date, "MVRV+RSI")
                trades.append(_record_close(
                    state, fill_price, current_date, i, "MVRV+RSI Exit",
                    df, partial_sells_this_trade, current_leverage,
                ))
                partial_sells_this_trade = []
                current_leverage = config.DEFAULT_LEVERAGE

        # 5. PARTIAL SELLS
        position = state.get("position", 0)
        if position == 1 and current_date.year in config.TOP_CYCLE_YEARS:
            ok_sell, sell_pct = ds.should_partial_sell(state, current_date, current_rsi)
            if ok_sell:
                sell_qty = state["qty"] * sell_pct
                entry_price = state["entry_price"]
                ds.execute_partial_sell(state, fill_price, current_date, sell_pct)
                gross = sell_qty * (fill_price - entry_price)
                fee = sell_qty * fill_price * config.COMMISSION_PCT
                record = {
                    "date": current_date,
                    "price": fill_price,
                    "qty_sold": sell_qty,
                    "remaining_qty": state["qty"],
                    "realized_pnl": gross - fee,
                    "entry_price": entry_price,
                    "trade_entry_date": state["entry_date"],
                    "pct_sold": sell_pct * 100,
                }
                partial_sells_this_trade.append(record)
                all_partial_sells.append(record)

    # Open position mark (ίδιο με leverage_bot03.py)
    if state.get("position", 0) != 0:
        last_close = float(df["close"].iloc[-1])
        last_date = df["date"].iloc[-1].to_pydatetime()
        _snapshot_before_close(state, entry_i)
        pos = state["position"]
        qty = state["qty"]
        entry = state["entry_price"]
        gross = qty * (last_close - entry) if pos == 1 else qty * (entry - last_close)
        exit_fee = qty * last_close * config.COMMISSION_PCT
        capital_before = state["capital"]
        capital_after = capital_before + gross - exit_fee
        trade = _record_close(
            state, last_close, last_date, len(df) - 1, "Open",
            df, partial_sells_this_trade, current_leverage, is_open=True,
        )
        trade["capital_after"] = capital_after
        trade["pnl"] = capital_after - trade["entry_balance"]
        trade["gross_pnl"] = gross
        trade["exit_fee"] = exit_fee
        trades.append(trade)

    trades_df = pd.DataFrame(trades)
    closed_mask = ~trades_df["open"].fillna(False).astype(bool) if "open" in trades_df.columns else pd.Series(True, index=trades_df.index)
    closed_trades_df = trades_df[closed_mask]
    final_capital = trades[-1]["capital_after"] if trades and trades[-1].get("open") else state["capital"]
    total_return = (final_capital - capital) / capital * 100
    wins = closed_trades_df[closed_trades_df["pnl"] > 0] if not closed_trades_df.empty else closed_trades_df
    losses = closed_trades_df[closed_trades_df["pnl"] <= 0] if not closed_trades_df.empty else closed_trades_df
    win_rate = len(wins) / len(closed_trades_df) * 100 if len(closed_trades_df) else 0.0
    leverage_trades = trades_df[trades_df.get("leverage", 1.0) > 1.0] if "leverage" in trades_df.columns else pd.DataFrame()

    print("\n" + "=" * 70)
    print("  BACKTEST RESULTS")
    print("=" * 70)
    print(f"  Initial Capital : ${capital:>12,.2f}")
    print(f"  Final Capital   : ${final_capital:>12,.2f}")
    print(f"  Total Return    : {total_return:>11.1f}%")
    print(f"  Closed Trades   : {len(closed_trades_df):>12}")
    print(f"  Win Rate        : {win_rate:>11.1f}%")
    print(f"  Shorts Opened   : {shorts_opened:>12}")
    print(f"  Exit Only       : {exit_only_count:>12}")
    print(f"  Stop Losses     : {len(stop_loss_events):>12}")
    print(f"  MVRV Exits      : {len(mvrv_exit_events):>12}")
    print(f"  Leverage Trades : {len(leverage_trades):>12}")
    print("=" * 70)

    for t in trades:
        pnl_s = f"+${t['pnl']:,.0f}" if t["pnl"] >= 0 else f"-${abs(t['pnl']):,.0f}"
        tag = " [OPEN]" if t.get("open") else ""
        lev = f" [{t.get('leverage', 1):.0f}x]" if t.get("leverage", 1) > 1 else ""
        ps_n = len(t.get("partial_sells", []))
        ps_s = f" | {ps_n} partials" if ps_n else ""
        print(
            f"  {t['type']:5s} | {str(t['entry_date'].date()):10s}->{str(t['exit_date'].date()):10s} | "
            f"${t['entry_price']:>8,.0f}->${t['exit_price']:>8,.0f} | "
            f"{t['exit_reason'][:25]:25s} | {pnl_s}{tag}{lev}{ps_s}"
        )

    # ── CHART ─────────────────────────────────────────────────────────
    print("\nBuilding chart...")
    chart_df = df[df["date"] >= pd.Timestamp(start_date)]

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.55, 0.20, 0.25], vertical_spacing=0.03,
        subplot_titles=("BTC/USD — Dynamic Supertrend", "Factor Value", "Equity Curve"),
    )

    fig.add_trace(go.Candlestick(
        x=chart_df["date"], open=chart_df["open"], high=chart_df["high"],
        low=chart_df["low"], close=chart_df["close"], name="BTC/USD",
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350", showlegend=False,
    ), row=1, col=1)

    bull = chart_df[chart_df["dir"] == -1]
    bear = chart_df[chart_df["dir"] == 1]
    fig.add_trace(go.Scatter(
        x=bull["date"], y=bull["st"], mode="lines",
        line=dict(color="#26a69a", width=2), name="ST Bull",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=bear["date"], y=bear["st"], mode="lines",
        line=dict(color="#ef5350", width=2), name="ST Bear",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=chart_df["date"], y=chart_df["factor"], mode="lines",
        line=dict(color="#f0a500", width=2), name="Factor",
        fill="tozeroy", fillcolor="rgba(240,165,0,0.1)",
        hovertemplate="Date: %{x|%Y-%m-%d}<br>Factor: %{y:.1f}<extra></extra>",
    ), row=2, col=1)
    fig.add_hline(
        y=config.BASE_FACTOR, line_dash="dash", line_color="gray",
        annotation_text=f"Base {config.BASE_FACTOR}", row=2, col=1,
    )
    fig.add_hline(
        y=config.MVRV_EXIT_BASE_FACTOR, line_dash="dash", line_color="#ff00ff",
        annotation_text=f"MVRV Mode {config.MVRV_EXIT_BASE_FACTOR}", row=2, col=1,
    )

    longs_chart = chart_df[chart_df["long_signal"]]
    lev_dates = {e["date"].date() for e in leverage_events}
    longs_1x = longs_chart[~longs_chart["date"].dt.date.isin(lev_dates)]
    if not longs_1x.empty:
        fig.add_trace(go.Scatter(
            x=longs_1x["date"], y=longs_1x["low"] * 0.97, mode="markers",
            marker=dict(symbol="triangle-up", size=12, color="#00ff00"),
            name="Long (1x)",
            hovertext=[
                f"LONG (1x)<br>{d.date()}<br>${p:,.0f}"
                for d, p in zip(longs_1x["date"], longs_1x["close"])
            ],
            hoverinfo="text",
        ), row=1, col=1)

    if leverage_events:
        fig.add_trace(go.Scatter(
            x=[e["date"] for e in leverage_events],
            y=[e["price"] * 0.94 for e in leverage_events], mode="markers",
            marker=dict(symbol="triangle-up", size=16, color="#ff6600",
                        line=dict(width=2, color="#ffffff")),
            name="Leverage Long (3x)",
            hovertext=[
                f"LEVERAGE LONG ({e['leverage']:.0f}x)<br>{e['date'].date()}<br>${e['price']:,.0f}"
                for e in leverage_events
            ],
            hoverinfo="text",
        ), row=1, col=1)

    if not trades_df.empty:
        sh = trades_df[trades_df["type"] == "Short"]
        if not sh.empty:
            fig.add_trace(go.Scatter(
                x=sh["entry_date"], y=sh["entry_price"] * 1.02, mode="markers",
                marker=dict(symbol="triangle-down", size=12, color="#ff0000"),
                name="Short",
                hovertext=[
                    f"SHORT<br>{d.date()}<br>${p:,.0f}"
                    for d, p in zip(sh["entry_date"], sh["entry_price"])
                ],
                hoverinfo="text",
            ), row=1, col=1)

    if exit_only_events:
        fig.add_trace(go.Scatter(
            x=[e["date"] for e in exit_only_events],
            y=[e["price"] * 1.05 for e in exit_only_events], mode="markers",
            marker=dict(symbol="circle", size=11, color="#FF9800",
                        line=dict(width=1.5, color="#000")),
            name="Exit Only",
            hovertext=[
                f"EXIT ONLY<br>{e['date'].date()}<br>${e['price']:,.0f}"
                for e in exit_only_events
            ],
            hoverinfo="text",
        ), row=1, col=1)

    if stop_loss_events:
        fig.add_trace(go.Scatter(
            x=[e["date"] for e in stop_loss_events],
            y=[e["price"] for e in stop_loss_events], mode="markers",
            marker=dict(symbol="x", size=12, color="#ff4444", line=dict(width=2)),
            name="Stop Loss",
            hovertext=[
                f"STOP LOSS<br>{e['date'].date()}<br>${e['price']:,.0f}<br>-{e['loss_percent']:.1f}%"
                for e in stop_loss_events
            ],
            hoverinfo="text",
        ), row=1, col=1)

    if mvrv_exit_events:
        fig.add_trace(go.Scatter(
            x=[e["date"] for e in mvrv_exit_events],
            y=[e["price"] for e in mvrv_exit_events], mode="markers",
            marker=dict(symbol="diamond", size=12, color="#ff00ff",
                        line=dict(width=1, color="#ffffff")),
            name="MVRV+RSI Exit (triggers 3x next long)",
            hovertext=[
                f"MVRV+RSI EXIT<br>{e['date'].date()}<br>${e['price']:,.0f}<br>-> Next long = 3x leverage"
                for e in mvrv_exit_events
            ],
            hoverinfo="text",
        ), row=1, col=1)

    if all_partial_sells:
        fig.add_trace(go.Scatter(
            x=[p["date"] for p in all_partial_sells],
            y=[p["price"] * 0.99 for p in all_partial_sells], mode="markers",
            marker=dict(symbol="star", size=10, color="#FFD700"),
            name="Partial Sell",
            hovertext=[
                f"PARTIAL SELL {p['pct_sold']:.0f}%<br>{p['date'].date()}<br>"
                f"${p['price']:,.0f}<br>PnL: ${p['realized_pnl']:,.0f}"
                for p in all_partial_sells
            ],
            hoverinfo="text",
        ), row=1, col=1)

    eq_dates = [trade_df["date"].iloc[0]] + [t["exit_date"] for t in trades]
    eq_values = [capital] + [t["capital_after"] for t in trades]
    fig.add_trace(go.Scatter(
        x=eq_dates, y=eq_values, mode="lines",
        line=dict(color="#f0a500", width=2), name="Equity",
        hovertemplate="Date: %{x|%Y-%m-%d}<br>Equity: $%{y:,.0f}<extra></extra>",
    ), row=3, col=1)
    fig.add_hline(
        y=capital, line_dash="dash", line_color="gray",
        annotation_text=f"Start ${capital:,.0f}", row=3, col=1,
    )

    decay = config.FACTOR_DECAY_MONTHS
    factor_info = (
        f"Sep={decay[9]} -> Oct={decay[10]} -> Nov={decay[11]} -> Dec={decay[12]}"
    )
    stats_text = (
        f"Return: {total_return:.1f}%  |  Final: ${final_capital:,.0f}  |  "
        f"Win Rate: {win_rate:.1f}%  |  Shorts: {shorts_opened}  |  "
        f"Leverage Trades: {len(leverage_trades)}"
    )

    fig.update_layout(
        title=dict(
            text=(
                f"<b>Long_Short_Bot — Dynamic Supertrend (verification chart)</b><br>"
                f"<span style='font-size:11px'>Base={config.BASE_FACTOR} -> "
                f"MVRV Mode={config.MVRV_EXIT_BASE_FACTOR} | Monthly Decay: {factor_info}</span><br>"
                f"<span style='font-size:10px'>Orange triangles = 3x leverage longs | "
                f"Green triangles = 1x longs</span><br>"
                f"<span style='font-size:10px'>{stats_text}</span>"
            ),
            x=0.5, y=0.94, xanchor="center", yanchor="top",
        ),
        xaxis_rangeslider_visible=False, template="plotly_dark",
        height=1050, margin=dict(t=150, l=50, r=50, b=50), hovermode="closest",
        legend=dict(
            orientation="h", yanchor="top", y=1.14, xanchor="center", x=0.5,
            font=dict(size=9), bgcolor="rgba(0,0,0,0.7)", bordercolor="gray", borderwidth=1,
        ),
    )
    fig.update_yaxes(type="log", row=1, col=1, title="Price (log)")
    fig.update_yaxes(title="Factor", row=2, col=1, range=[0, config.BASE_FACTOR + 1])
    fig.update_yaxes(title="Equity ($)", row=3, col=1)

    out_html = Path(html_out)
    fig.write_html(str(out_html), include_plotlyjs="cdn")
    print(f"Chart saved -> {out_html.resolve()}")

    if not trades_df.empty:
        trades_df.to_csv(csv_out, index=False)
        print(f"Trades CSV  -> {Path(csv_out).resolve()}")

    print("\nDone! Open backtest_chart.html in browser to compare with Scalping_Bot chart.")
    return {"trades": trades, "final_capital": final_capital, "html": str(out_html)}


if __name__ == "__main__":
    run_backtest_with_chart()
