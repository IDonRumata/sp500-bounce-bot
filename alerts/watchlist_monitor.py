"""
Watchlist alert monitor — checks watchlist tickers for price/RSI triggers,
entry signals (bounce opportunities), and exit signals for open positions.
Runs periodically via job_queue during market hours.
"""
from datetime import datetime, timedelta, timezone

import yfinance as yf
import pandas as pd

from analysis.technical import calc_rsi, full_technical_analysis
from scoring.scorer import _technical_score
from config import (
    ALERT_PRICE_PCT, ALERT_RSI_OVERSOLD, ALERT_RSI_OVERBOUGHT,
    ALERT_COOLDOWN_HOURS, MIN_COMPOSITE_SCORE,
    STOP_LOSS_PCT, TAKE_PROFIT_PCT, logger,
)
from storage.database import (
    get_watchlist, get_all_active_users,
    save_alert, get_last_alert_time,
    get_open_positions,
)

# Entry signal thresholds
ENTRY_SIGNAL_RSI = 40          # RSI must be below this
ENTRY_SIGNAL_DRAWDOWN = -15    # Drawdown must be deeper than this (%)
ENTRY_SIGNAL_TECH_SCORE = 65   # Technical score must be above this
ENTRY_SIGNAL_COOLDOWN_H = 24   # Don't repeat entry signal for 24h

# Exit signal thresholds — derived from config SL/TP
EXIT_TAKE_PROFIT_PCT = TAKE_PROFIT_PCT   # from config (+15%)
EXIT_STOP_LOSS_PCT = STOP_LOSS_PCT       # from config (-8%)
EXIT_RSI_OVERBOUGHT = 70                 # RSI overbought → consider exit
EXIT_SIGNAL_COOLDOWN_H = 8              # Don't repeat exit signal for 8h


def _is_market_open() -> bool:
    """Check if NYSE is open (Mon-Fri 9:30-16:00 ET = 14:30-21:00 UTC)."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    market_open = now.replace(hour=14, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=21, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def _check_cooldown(user_id: str, ticker: str, alert_type: str) -> bool:
    """Returns True if enough time has passed since last alert."""
    last = get_last_alert_time(user_id, ticker, alert_type)
    if not last:
        return True
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
        return (datetime.utcnow() - last_dt) > timedelta(hours=ALERT_COOLDOWN_HOURS)
    except Exception:
        return True


def check_watchlist_alerts() -> dict[str, list[dict]]:
    """
    Check all users' watchlists for alert conditions.
    Returns {user_id: [alert_dict, ...]} for users with triggered alerts.
    Only runs during market hours.
    """
    if not _is_market_open():
        logger.debug("Market closed, skipping watchlist alerts")
        return {}

    # Gather all unique tickers across all users
    users = get_all_active_users()
    user_watchlists: dict[str, list[str]] = {}
    all_tickers: set[str] = set()

    for u in users:
        uid = u["chat_id"]
        if not u.get("alert_enabled"):
            continue
        wl = get_watchlist(uid)
        if wl:
            user_watchlists[uid] = wl
            all_tickers.update(wl)

    if not all_tickers:
        return {}

    # Fetch price data for all unique tickers in one batch
    ticker_data = _fetch_alert_data(list(all_tickers))

    # Check each user's watchlist against the data
    alerts_by_user: dict[str, list[dict]] = {}

    for uid, tickers in user_watchlists.items():
        user_alerts = []
        for ticker in tickers:
            data = ticker_data.get(ticker)
            if not data:
                continue

            # Price change alert
            change_pct = data["change_pct"]
            if abs(change_pct) >= ALERT_PRICE_PCT:
                alert_type = "price_surge" if change_pct > 0 else "price_drop"
                if _check_cooldown(uid, ticker, alert_type):
                    alert = {
                        "ticker": ticker,
                        "alert_type": alert_type,
                        "price": data["price"],
                        "change_pct": change_pct,
                        "rsi": data["rsi"],
                    }
                    user_alerts.append(alert)
                    save_alert(uid, ticker, alert_type, data["price"], change_pct, data["rsi"])

            # RSI oversold alert
            if data["rsi"] is not None and data["rsi"] < ALERT_RSI_OVERSOLD:
                if _check_cooldown(uid, ticker, "rsi_oversold"):
                    alert = {
                        "ticker": ticker,
                        "alert_type": "rsi_oversold",
                        "price": data["price"],
                        "change_pct": change_pct,
                        "rsi": data["rsi"],
                    }
                    user_alerts.append(alert)
                    save_alert(uid, ticker, "rsi_oversold", data["price"], change_pct, data["rsi"])

            # RSI overbought alert
            if data["rsi"] is not None and data["rsi"] > ALERT_RSI_OVERBOUGHT:
                if _check_cooldown(uid, ticker, "rsi_overbought"):
                    alert = {
                        "ticker": ticker,
                        "alert_type": "rsi_overbought",
                        "price": data["price"],
                        "change_pct": change_pct,
                        "rsi": data["rsi"],
                    }
                    user_alerts.append(alert)
                    save_alert(uid, ticker, "rsi_overbought", data["price"], change_pct, data["rsi"])

        if user_alerts:
            alerts_by_user[uid] = user_alerts

    return alerts_by_user


def _fetch_alert_data(tickers: list[str]) -> dict[str, dict]:
    """Fetch current price + RSI for a list of tickers. Returns {ticker: {price, change_pct, rsi}}."""
    result = {}

    try:
        data = yf.download(
            tickers=tickers,
            period="1mo",
            interval="1d",
            auto_adjust=True,
            threads=True,
            progress=False,
        )

        if data.empty:
            return result

        for sym in tickers:
            try:
                if len(tickers) == 1:
                    closes = data["Close"].dropna()
                else:
                    closes = data[sym]["Close"].dropna()

                if len(closes) < 14:
                    continue

                current_price = float(closes.iloc[-1])

                # Intraday change: compare to previous close
                prev_close = float(closes.iloc[-2]) if len(closes) >= 2 else current_price
                change_pct = round(((current_price - prev_close) / prev_close) * 100, 2)

                # RSI
                rsi = round(calc_rsi(closes, 14), 2)

                result[sym] = {
                    "price": round(current_price, 2),
                    "change_pct": change_pct,
                    "rsi": rsi,
                }

            except Exception as e:
                logger.debug(f"Alert data skip {sym}: {e}")
                continue

    except Exception as e:
        logger.error(f"Alert batch download failed: {e}")

    return result


def check_entry_signals() -> dict[str, list[dict]]:
    """
    Check watchlist tickers for entry signals (bounce opportunities).
    Runs full technical analysis and scores each ticker.
    Returns {user_id: [signal_dict, ...]}.
    """
    if not _is_market_open():
        return {}

    users = get_all_active_users()
    user_watchlists: dict[str, list[str]] = {}
    all_tickers: set[str] = set()

    for u in users:
        uid = u["chat_id"]
        wl = get_watchlist(uid)
        if wl:
            user_watchlists[uid] = wl
            all_tickers.update(wl)

    if not all_tickers:
        return {}

    # Download 6 months of data for full technical analysis
    tickers_list = list(all_tickers)
    try:
        data = yf.download(
            tickers=tickers_list,
            period="6mo",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception as e:
        logger.error(f"Entry signal download failed: {e}")
        return {}

    if data.empty:
        return {}

    # Analyze each ticker
    ticker_signals = {}
    for sym in tickers_list:
        try:
            if len(tickers_list) == 1:
                df = data
            else:
                df = data[sym]

            df = df.dropna(how="all")
            if len(df) < 50:
                continue

            closes = df["Close"].dropna()
            current_price = float(closes.iloc[-1])
            high_6m = float(closes.max())
            drawdown = ((current_price - high_6m) / high_6m) * 100

            rsi = calc_rsi(closes, 14)

            # Quick pre-filter
            if rsi > ENTRY_SIGNAL_RSI:
                continue
            if drawdown > ENTRY_SIGNAL_DRAWDOWN:
                continue

            # Full technical analysis
            # Build a proper DataFrame with OHLCV
            if len(tickers_list) == 1:
                sym_df = data
            else:
                sym_df = pd.DataFrame({
                    "Open": data[sym]["Open"],
                    "High": data[sym]["High"],
                    "Low": data[sym]["Low"],
                    "Close": data[sym]["Close"],
                    "Volume": data[sym]["Volume"],
                }).dropna(how="all")

            tech = full_technical_analysis(sym_df, sym)
            if tech.get("error"):
                continue

            tech_score = _technical_score(tech)

            if tech_score < ENTRY_SIGNAL_TECH_SCORE:
                continue

            # Good entry signal!
            ticker_signals[sym] = {
                "ticker": sym,
                "alert_type": "entry_signal",
                "price": round(current_price, 2),
                "rsi": round(rsi, 2),
                "drawdown": round(drawdown, 2),
                "tech_score": round(tech_score, 1),
                "macd_bullish": tech.get("macd_histogram_rising", False),
                "bb_below": tech.get("bb_pct_b", 1.0) < 0.2,
                "divergence": tech.get("rsi_bullish_divergence", False),
            }

        except Exception as e:
            logger.debug(f"Entry signal skip {sym}: {e}")
            continue

    if not ticker_signals:
        return {}

    # Map signals to users
    signals_by_user: dict[str, list[dict]] = {}
    for uid, tickers in user_watchlists.items():
        user_signals = []
        for t in tickers:
            if t in ticker_signals:
                if _check_cooldown(uid, t, "entry_signal"):
                    sig = ticker_signals[t].copy()
                    user_signals.append(sig)
                    save_alert(uid, t, "entry_signal", sig["price"], 0, sig["rsi"])
        if user_signals:
            signals_by_user[uid] = user_signals

    return signals_by_user


def check_exit_signals() -> dict[str, list[dict]]:
    """
    Check open portfolio positions for exit signals.
    Returns {user_id: [signal_dict, ...]}.
    """
    if not _is_market_open():
        return {}

    users = get_all_active_users()
    all_positions = []
    user_positions: dict[str, list[dict]] = {}

    for u in users:
        uid = u["chat_id"]
        positions = get_open_positions(uid)
        if positions:
            user_positions[uid] = [dict(p) for p in positions]
            all_positions.extend(user_positions[uid])

    if not all_positions:
        return {}

    # Get unique tickers
    tickers = list({p["ticker"] for p in all_positions})

    # Download data for analysis
    try:
        data = yf.download(
            tickers=tickers,
            period="1mo",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception as e:
        logger.error(f"Exit signal download failed: {e}")
        return {}

    if data.empty:
        return {}

    # Get current prices and RSI
    ticker_info = {}
    for sym in tickers:
        try:
            if len(tickers) == 1:
                closes = data["Close"].dropna()
            else:
                closes = data[sym]["Close"].dropna()

            if len(closes) < 14:
                continue

            current_price = float(closes.iloc[-1])
            rsi = round(calc_rsi(closes, 14), 2)
            ticker_info[sym] = {"price": current_price, "rsi": rsi}

        except Exception:
            continue

    # Check each user's positions
    signals_by_user: dict[str, list[dict]] = {}

    for uid, positions in user_positions.items():
        user_signals = []
        for pos in positions:
            ticker = pos["ticker"]
            info = ticker_info.get(ticker)
            if not info:
                continue

            current_price = info["price"]
            buy_price = pos["buy_price"]
            pnl_pct = round((current_price / buy_price - 1) * 100, 2)
            rsi = info["rsi"]

            signal = None

            # Take profit signal
            if pnl_pct >= EXIT_TAKE_PROFIT_PCT:
                signal_type = "exit_take_profit"
                reason = f"Прибыль {pnl_pct:+.1f}% — рассмотрите фиксацию"

            # Stop loss warning
            elif pnl_pct <= EXIT_STOP_LOSS_PCT:
                signal_type = "exit_stop_loss"
                reason = f"Убыток {pnl_pct:+.1f}% — рассмотрите стоп-лосс"

            # RSI overbought while in profit
            elif rsi >= EXIT_RSI_OVERBOUGHT and pnl_pct > 0:
                signal_type = "exit_overbought"
                reason = f"RSI {rsi} (перекуплен) при {pnl_pct:+.1f}% прибыли"

            else:
                continue

            if _check_cooldown(uid, ticker, signal_type):
                signal = {
                    "ticker": ticker,
                    "alert_type": signal_type,
                    "price": round(current_price, 2),
                    "buy_price": buy_price,
                    "pnl_pct": pnl_pct,
                    "rsi": rsi,
                    "reason": reason,
                    "shares": pos["shares"],
                }
                user_signals.append(signal)
                save_alert(uid, ticker, signal_type, current_price, pnl_pct, rsi)

        if user_signals:
            signals_by_user[uid] = user_signals

    return signals_by_user
