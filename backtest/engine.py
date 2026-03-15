"""
Backtest engine: run the scoring pipeline on historical data
and measure how recommendations performed.

Key principle: only uses data available UP TO the as-of date.
No LLM (hindsight bias), no real-time sentiment.
Technical + fundamental scoring is deterministic and fair.
"""
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from config import (
    MAX_PRICE, MIN_DRAWDOWN, PRE_FILTER_RSI,
    MIN_COMPOSITE_SCORE, TOP_PICKS_COUNT,
    WEIGHT_TECHNICAL, WEIGHT_FUNDAMENTAL, WEIGHT_SENTIMENT, WEIGHT_MARKET,
    logger,
)
from analysis.technical import full_technical_analysis, calc_rsi
from scoring.scorer import _technical_score, _market_score


# Regime multiplier (same as main.py)
REGIME_MULTIPLIER = {
    "bullish": 1.0, "neutral": 0.95, "weak": 0.88,
    "bearish": 0.80, "panic": 0.70,
}

# Check windows: how many days after signal to measure result
CHECK_WINDOWS = [5, 10, 20]


def run_backtest(days_back: int = 365, check_days: int = 10) -> dict:
    """
    Run backtest over the last N days.
    Picks dates every ~20 trading days, runs scoring, checks actual results.

    Returns dict with signals list and aggregate stats.
    """
    logger.info(f"BACKTEST: starting {days_back}-day backtest")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_back + 250)  # extra data for indicators

    # Step 1: Fetch S&P 500 tickers
    from data.sp500_list import fetch_sp500_tickers
    tickers_info = fetch_sp500_tickers()
    if not tickers_info:
        return {"error": "Failed to fetch S&P 500 list"}

    symbols = [t["symbol"] for t in tickers_info]
    sym_to_info = {t["symbol"]: t for t in tickers_info}

    # Step 2: Download all history at once (efficient)
    logger.info(f"BACKTEST: downloading history for {len(symbols)} tickers...")
    try:
        all_data = yf.download(
            tickers=symbols,
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception as e:
        logger.error(f"BACKTEST: download failed: {e}")
        return {"error": f"Data download failed: {e}"}

    # Also download SPY and VIX for market context
    try:
        spy_data = yf.download("SPY", start=start_date.strftime("%Y-%m-%d"),
                               end=end_date.strftime("%Y-%m-%d"),
                               auto_adjust=True, progress=False)
        vix_data = yf.download("^VIX", start=start_date.strftime("%Y-%m-%d"),
                               end=end_date.strftime("%Y-%m-%d"),
                               auto_adjust=True, progress=False)
    except Exception:
        spy_data = pd.DataFrame()
        vix_data = pd.DataFrame()

    # Step 3: Generate test dates (every ~20 trading days)
    # Use the actual trading dates from SPY
    if spy_data.empty:
        return {"error": "No SPY data for market context"}

    trading_dates = spy_data.index
    backtest_start = end_date - timedelta(days=days_back)

    # Filter to dates within backtest window, leaving room for check_days
    valid_dates = [d for d in trading_dates
                   if d >= pd.Timestamp(backtest_start)
                   and d <= pd.Timestamp(end_date - timedelta(days=max(CHECK_WINDOWS) + 5))]

    if not valid_dates:
        return {"error": "Not enough data for backtest period"}

    # Sample every ~20 trading days
    step = max(1, len(valid_dates) // 18)
    test_dates = valid_dates[::step]

    logger.info(f"BACKTEST: testing {len(test_dates)} dates over {days_back} days")

    # Step 4: Run analysis for each test date
    all_signals = []

    for test_date in test_dates:
        signals = _analyze_date(
            test_date, all_data, spy_data, vix_data,
            symbols, sym_to_info, trading_dates,
        )
        all_signals.extend(signals)
        logger.info(f"BACKTEST: {test_date.strftime('%Y-%m-%d')} → {len(signals)} signals")

    if not all_signals:
        return {
            "days_back": days_back,
            "test_dates": len(test_dates),
            "total_signals": 0,
            "signals": [],
            "stats": {},
        }

    # Step 5: Calculate aggregate stats
    stats = _calculate_stats(all_signals)

    logger.info(f"BACKTEST COMPLETE: {len(all_signals)} signals, "
                f"win rate: {stats.get('win_rate_10d', 0):.1f}%")

    return {
        "days_back": days_back,
        "test_dates": len(test_dates),
        "total_signals": len(all_signals),
        "signals": all_signals,
        "stats": stats,
    }


def run_backtest_date(as_of_date: str, check_days: int = 10) -> dict:
    """
    Run backtest for a specific date.
    as_of_date: 'YYYY-MM-DD'
    """
    target = pd.Timestamp(as_of_date)
    end_fetch = target + timedelta(days=max(CHECK_WINDOWS) + 10)
    start_fetch = target - timedelta(days=250)

    from data.sp500_list import fetch_sp500_tickers
    tickers_info = fetch_sp500_tickers()
    if not tickers_info:
        return {"error": "Failed to fetch S&P 500 list"}

    symbols = [t["symbol"] for t in tickers_info]
    sym_to_info = {t["symbol"]: t for t in tickers_info}

    logger.info(f"BACKTEST DATE: {as_of_date}, downloading data...")
    try:
        all_data = yf.download(
            tickers=symbols,
            start=start_fetch.strftime("%Y-%m-%d"),
            end=min(end_fetch, datetime.now()).strftime("%Y-%m-%d"),
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
        spy_data = yf.download("SPY", start=start_fetch.strftime("%Y-%m-%d"),
                               end=min(end_fetch, datetime.now()).strftime("%Y-%m-%d"),
                               auto_adjust=True, progress=False)
        vix_data = yf.download("^VIX", start=start_fetch.strftime("%Y-%m-%d"),
                               end=min(end_fetch, datetime.now()).strftime("%Y-%m-%d"),
                               auto_adjust=True, progress=False)
    except Exception as e:
        return {"error": f"Data download failed: {e}"}

    trading_dates = spy_data.index if not spy_data.empty else pd.DatetimeIndex([])

    # Find closest trading date to target
    valid = [d for d in trading_dates if d <= target]
    if not valid:
        return {"error": f"No trading data for {as_of_date}"}
    actual_date = valid[-1]

    signals = _analyze_date(
        actual_date, all_data, spy_data, vix_data,
        symbols, sym_to_info, trading_dates,
    )

    stats = _calculate_stats(signals) if signals else {}

    return {
        "date": actual_date.strftime("%Y-%m-%d"),
        "total_signals": len(signals),
        "signals": signals,
        "stats": stats,
    }


def _analyze_date(test_date, all_data, spy_data, vix_data,
                  symbols, sym_to_info, trading_dates) -> list[dict]:
    """Run the scoring pipeline as if today were test_date."""

    # Build market context from historical data
    market_ctx = _historical_market_context(spy_data, vix_data, test_date)
    regime = market_ctx.get("regime", "neutral")
    multiplier = REGIME_MULTIPLIER.get(regime, 0.95)

    # Skip weak/panic regimes — data shows WR ~50% with negative avg returns
    if regime in ("weak", "panic"):
        return []

    signals = []

    for sym in symbols:
        try:
            # Get historical data up to test_date
            if len(symbols) == 1:
                df = all_data
            else:
                try:
                    df = all_data[sym]
                except (KeyError, TypeError):
                    continue

            df = df.loc[:test_date].dropna(how="all")
            if df.empty or len(df) < 50:
                continue

            closes = df["Close"].dropna()
            if len(closes) < 50:
                continue

            current_price = float(closes.iloc[-1])

            # Pre-filter (same as production)
            if current_price > MAX_PRICE or current_price < 1.0:
                continue

            high_6m = float(closes.max())
            drawdown = ((current_price - high_6m) / high_6m) * 100
            if drawdown > MIN_DRAWDOWN:
                continue

            rsi_val = calc_rsi(closes, 14)
            if rsi_val > PRE_FILTER_RSI:
                continue

            # Deep technical analysis
            tech = full_technical_analysis(df, sym)
            if tech.get("error"):
                continue

            # Technical score (fair — uses only historical data)
            tech_score = _technical_score(tech)

            # Neutral fundamental & sentiment (can't get historical)
            fund_score = 50.0
            sent_score = 50.0
            market_score = _market_score(market_ctx, sym)

            # Composite — backtest-adapted weights
            # Fund/sent are always 50 (neutral), so use tech-heavy weights
            BT_WEIGHT_TECH = 0.70
            BT_WEIGHT_MARKET = 0.30
            composite = (
                tech_score * BT_WEIGHT_TECH +
                market_score * BT_WEIGHT_MARKET
            )
            composite = max(0, min(100, composite))

            # Apply regime multiplier
            if multiplier < 1.0:
                composite = round(composite * multiplier, 1)

            if composite < MIN_COMPOSITE_SCORE:
                continue

            # Calculate actual results (what happened after)
            future_results = _get_future_returns(
                sym, all_data, test_date, current_price, trading_dates, len(symbols),
            )

            signals.append({
                "date": test_date.strftime("%Y-%m-%d"),
                "symbol": sym,
                "sector": sym_to_info.get(sym, {}).get("sector", "?"),
                "price": round(current_price, 2),
                "rsi": round(rsi_val, 2),
                "drawdown": round(drawdown, 2),
                "composite_score": round(composite, 1),
                "tech_score": round(tech_score, 1),
                "market_regime": regime,
                **future_results,
            })

        except Exception as e:
            continue

    # Sort by composite and take top picks
    signals.sort(key=lambda x: x["composite_score"], reverse=True)
    return signals[:TOP_PICKS_COUNT]


def _historical_market_context(spy_data, vix_data, as_of_date) -> dict:
    """Build market context from historical SPY/VIX data."""
    ctx = {"regime": "neutral", "vix": {"level": 2, "label": "normal"}, "sectors": []}

    if spy_data.empty:
        return ctx

    spy = spy_data.loc[:as_of_date]
    if len(spy) < 50:
        return ctx

    spy_closes = spy["Close"].dropna()
    spy_price = float(spy_closes.iloc[-1])
    spy_rsi = calc_rsi(spy_closes, 14)
    spy_sma50 = float(spy_closes.rolling(50).mean().iloc[-1]) if len(spy_closes) >= 50 else spy_price
    spy_sma200 = float(spy_closes.rolling(min(200, len(spy_closes))).mean().iloc[-1])
    above_sma50 = spy_price > spy_sma50
    above_sma200 = spy_price > spy_sma200

    # VIX
    vix_val = 20.0
    if not vix_data.empty:
        vix_slice = vix_data.loc[:as_of_date]
        if not vix_slice.empty:
            vix_val = float(vix_slice["Close"].dropna().iloc[-1])

    if vix_val < 15:
        vix_level, vix_label = 1, "complacent"
    elif vix_val < 20:
        vix_level, vix_label = 2, "normal"
    elif vix_val < 25:
        vix_level, vix_label = 3, "elevated"
    elif vix_val < 30:
        vix_level, vix_label = 4, "high_fear"
    else:
        vix_level, vix_label = 5, "panic"

    # Regime
    if spy_rsi < 35 and not above_sma50 and vix_val > 30:
        regime = "panic"
    elif spy_rsi < 42 and not above_sma50:
        regime = "bearish"
    elif spy_rsi < 48:
        regime = "weak"
    elif spy_rsi > 55 and above_sma50 and above_sma200:
        regime = "bullish"
    else:
        regime = "neutral"

    ctx["regime"] = regime
    ctx["spy"] = {
        "price": round(spy_price, 2),
        "rsi": round(spy_rsi, 2),
        "above_sma50": above_sma50,
        "above_sma200": above_sma200,
    }
    ctx["vix"] = {"value": round(vix_val, 2), "level": vix_level, "label": vix_label}

    return ctx


def _get_future_returns(symbol, all_data, signal_date, entry_price,
                        trading_dates, num_symbols) -> dict:
    """Get actual returns after signal_date for CHECK_WINDOWS."""
    result = {}

    try:
        if num_symbols == 1:
            df = all_data
        else:
            df = all_data[symbol]

        future = df.loc[signal_date:].dropna(how="all")
        if len(future) < 2:
            for w in CHECK_WINDOWS:
                result[f"return_{w}d"] = None
            result["max_gain"] = None
            result["max_loss"] = None
            return result

        # Skip the signal date itself
        future = future.iloc[1:]
        future_closes = future["Close"].dropna()

        for w in CHECK_WINDOWS:
            if len(future_closes) >= w:
                price_at_w = float(future_closes.iloc[w - 1])
                ret = ((price_at_w - entry_price) / entry_price) * 100
                result[f"return_{w}d"] = round(ret, 2)
            else:
                result[f"return_{w}d"] = None

        # Max gain/loss in the 20-day window
        window = future_closes.iloc[:max(CHECK_WINDOWS)] if len(future_closes) >= max(CHECK_WINDOWS) else future_closes
        if not window.empty:
            max_price = float(window.max())
            min_price = float(window.min())
            result["max_gain"] = round(((max_price - entry_price) / entry_price) * 100, 2)
            result["max_loss"] = round(((min_price - entry_price) / entry_price) * 100, 2)
        else:
            result["max_gain"] = None
            result["max_loss"] = None

    except Exception:
        for w in CHECK_WINDOWS:
            result[f"return_{w}d"] = None
        result["max_gain"] = None
        result["max_loss"] = None

    return result


def _calculate_stats(signals: list[dict]) -> dict:
    """Calculate aggregate backtest statistics."""
    stats = {}

    for w in CHECK_WINDOWS:
        key = f"return_{w}d"
        returns = [s[key] for s in signals if s.get(key) is not None]
        if returns:
            wins = [r for r in returns if r > 0]
            losses = [r for r in returns if r <= 0]
            stats[f"win_rate_{w}d"] = round(len(wins) / len(returns) * 100, 1)
            stats[f"avg_return_{w}d"] = round(sum(returns) / len(returns), 2)
            stats[f"median_return_{w}d"] = round(float(np.median(returns)), 2)
            stats[f"avg_win_{w}d"] = round(sum(wins) / len(wins), 2) if wins else 0
            stats[f"avg_loss_{w}d"] = round(sum(losses) / len(losses), 2) if losses else 0
            stats[f"best_{w}d"] = round(max(returns), 2)
            stats[f"worst_{w}d"] = round(min(returns), 2)
            stats[f"count_{w}d"] = len(returns)

    # Overall stats
    max_gains = [s["max_gain"] for s in signals if s.get("max_gain") is not None]
    max_losses = [s["max_loss"] for s in signals if s.get("max_loss") is not None]
    if max_gains:
        stats["avg_max_gain"] = round(sum(max_gains) / len(max_gains), 2)
    if max_losses:
        stats["avg_max_loss"] = round(sum(max_losses) / len(max_losses), 2)

    # Score distribution
    scores = [s["composite_score"] for s in signals]
    if scores:
        stats["avg_score"] = round(sum(scores) / len(scores), 1)
        stats["min_score"] = round(min(scores), 1)
        stats["max_score"] = round(max(scores), 1)

    # By regime
    regimes = {}
    for s in signals:
        r = s.get("market_regime", "?")
        if r not in regimes:
            regimes[r] = []
        ret = s.get("return_10d")
        if ret is not None:
            regimes[r].append(ret)
    stats["by_regime"] = {
        r: {"count": len(rets), "avg_return": round(sum(rets) / len(rets), 2),
            "win_rate": round(len([x for x in rets if x > 0]) / len(rets) * 100, 1)}
        for r, rets in regimes.items() if rets
    }

    # By score tier (10d returns)
    score_tiers = {"70+": [], "65-70": [], "60-65": [], "55-60": [], "<55": []}
    for s in signals:
        sc = s.get("composite_score", 0)
        ret = s.get("return_10d")
        if ret is None:
            continue
        if sc >= 70:
            score_tiers["70+"].append(ret)
        elif sc >= 65:
            score_tiers["65-70"].append(ret)
        elif sc >= 60:
            score_tiers["60-65"].append(ret)
        elif sc >= 55:
            score_tiers["55-60"].append(ret)
        else:
            score_tiers["<55"].append(ret)
    stats["by_score_tier"] = {
        tier: {"count": len(rets), "avg_return": round(sum(rets) / len(rets), 2),
               "win_rate": round(len([x for x in rets if x > 0]) / len(rets) * 100, 1)}
        for tier, rets in score_tiers.items() if rets
    }

    # By drawdown depth (10d returns)
    dd_tiers = {"<-30%": [], "-20..-30%": [], "-15..-20%": [], "-10..-15%": []}
    for s in signals:
        dd = s.get("drawdown", 0)
        ret = s.get("return_10d")
        if ret is None:
            continue
        if dd < -30:
            dd_tiers["<-30%"].append(ret)
        elif dd < -20:
            dd_tiers["-20..-30%"].append(ret)
        elif dd < -15:
            dd_tiers["-15..-20%"].append(ret)
        else:
            dd_tiers["-10..-15%"].append(ret)
    stats["by_drawdown"] = {
        tier: {"count": len(rets), "avg_return": round(sum(rets) / len(rets), 2),
               "win_rate": round(len([x for x in rets if x > 0]) / len(rets) * 100, 1)}
        for tier, rets in dd_tiers.items() if rets
    }

    # Profit factor (sum of wins / abs sum of losses)
    returns_10d = [s.get("return_10d") for s in signals if s.get("return_10d") is not None]
    if returns_10d:
        gross_profit = sum(r for r in returns_10d if r > 0)
        gross_loss = abs(sum(r for r in returns_10d if r < 0))
        stats["profit_factor"] = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0

    return stats
