"""
Watchlist alert monitor — checks watchlist tickers for price/RSI triggers.
Runs periodically via job_queue during market hours.
"""
from datetime import datetime, timedelta, timezone

import yfinance as yf
import pandas as pd

from analysis.technical import calc_rsi
from config import (
    ALERT_PRICE_PCT, ALERT_RSI_OVERSOLD, ALERT_RSI_OVERBOUGHT,
    ALERT_COOLDOWN_HOURS, logger,
)
from storage.database import (
    get_watchlist, get_all_active_users,
    save_alert, get_last_alert_time,
)


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
