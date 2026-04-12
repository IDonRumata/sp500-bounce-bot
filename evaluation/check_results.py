"""
Check pending recommendations against current prices.
Run daily after market close to evaluate recommendation accuracy.
"""
import yfinance as yf
from datetime import datetime, timedelta
from config import SUCCESS_THRESHOLD_PCT, FAILURE_THRESHOLD_PCT, logger
from storage.database import (
    get_pending_recommendations, update_recommendation_result,
    get_pending_30d_recommendations, update_recommendation_30d_result,
)


def check_pending_results() -> list[dict]:
    """
    Check all pending recommendations whose check_date has passed.
    Returns list of checked results for reporting.
    """
    pending = get_pending_recommendations()
    if not pending:
        logger.info("No pending recommendations to check")
        return []

    logger.info(f"Checking {len(pending)} pending recommendations...")
    results = []

    # Group by ticker to minimize API calls
    tickers = list(set(r["ticker"] for r in pending))
    prices = _fetch_current_prices(tickers)

    for rec in pending:
        ticker = rec["ticker"]
        price_at_signal = rec["price_at_signal"]
        signal_date = rec["signal_date"]

        current_price = prices.get(ticker)
        if current_price is None:
            logger.warning(f"Could not fetch price for {ticker}, skipping")
            continue

        # Calculate result
        result_pct = round(((current_price - price_at_signal) / price_at_signal) * 100, 2)

        # Get max/min in the period for extended analysis
        max_price, min_price = _get_period_range(ticker, signal_date, rec["check_date"])

        # Determine status
        if result_pct >= SUCCESS_THRESHOLD_PCT:
            status = "success"
        elif result_pct <= FAILURE_THRESHOLD_PCT:
            status = "failure"
        else:
            status = "neutral"

        # Update database
        update_recommendation_result(
            rec_id=rec["id"],
            price_at_check=current_price,
            result_pct=result_pct,
            max_price=max_price,
            min_price=min_price,
            status=status,
        )

        results.append({
            "ticker": ticker,
            "price_at_signal": price_at_signal,
            "price_at_check": current_price,
            "result_pct": result_pct,
            "max_price": max_price,
            "min_price": min_price,
            "status": status,
            "signal_date": signal_date,
            "composite_score": rec["composite_score"],
        })

        logger.info(f"  {ticker}: {price_at_signal:.2f} → {current_price:.2f} ({result_pct:+.2f}%) = {status}")

    logger.info(f"Checked {len(results)}/{len(pending)} recommendations")
    return results


def check_pending_30d_results() -> list[dict]:
    """
    Check all pending 30-day recommendations whose check_date_30d has passed.
    Returns list of checked results for reporting.
    """
    pending = get_pending_30d_recommendations()
    if not pending:
        logger.info("No pending 30d recommendations to check")
        return []

    logger.info(f"Checking {len(pending)} pending 30d recommendations...")
    results = []

    tickers = list(set(r["ticker"] for r in pending))
    prices = _fetch_current_prices(tickers)

    for rec in pending:
        ticker = rec["ticker"]
        price_at_signal = rec["price_at_signal"]
        signal_date = rec["signal_date"]

        current_price = prices.get(ticker)
        if current_price is None:
            logger.warning(f"Could not fetch price for {ticker} (30d), skipping")
            continue

        result_pct = round(((current_price - price_at_signal) / price_at_signal) * 100, 2)

        if result_pct >= SUCCESS_THRESHOLD_PCT:
            status_30d = "success_30d"
        elif result_pct <= FAILURE_THRESHOLD_PCT:
            status_30d = "failure_30d"
        else:
            status_30d = "neutral_30d"

        update_recommendation_30d_result(
            rec_id=rec["id"],
            price_at_check=current_price,
            result_pct=result_pct,
            status_30d=status_30d,
        )

        results.append({
            "ticker": ticker,
            "price_at_signal": price_at_signal,
            "price_at_check": current_price,
            "result_pct": result_pct,
            "status": status_30d,
            "signal_date": signal_date,
            "composite_score": rec["composite_score"],
            "period": "30d",
        })

        logger.info(f"  {ticker} (30d): {price_at_signal:.2f} → {current_price:.2f} ({result_pct:+.2f}%) = {status_30d}")

    logger.info(f"Checked 30d: {len(results)}/{len(pending)} recommendations")
    return results


def snapshot_all_recommendations() -> dict:
    """
    Dynamic tracking: fetch current prices for ALL active recommendations
    (not expired, signal_date within last 60 days).
    Returns a digest dict with aggregate stats + notable movers.
    Called 3x/week (Tue/Thu/Sat).
    """
    from storage.database import get_active_recommendations_for_snapshot

    recs = get_active_recommendations_for_snapshot()
    if not recs:
        logger.info("Snapshot: no active recommendations to track")
        return {}

    logger.info(f"Snapshot: tracking {len(recs)} active recommendations")

    tickers = list({r["ticker"] for r in recs})
    prices = _fetch_current_prices(tickers)

    if not prices:
        logger.error("Snapshot: failed to fetch prices")
        return {}

    today = datetime.now().strftime("%Y-%m-%d")
    enriched = []

    for rec in recs:
        ticker = rec["ticker"]
        current_price = prices.get(ticker)
        if current_price is None:
            continue

        entry_price = rec["price_at_signal"]
        pnl_pct = round((current_price / entry_price - 1) * 100, 2)
        age_days = (datetime.now() - datetime.strptime(rec["signal_date"], "%Y-%m-%d")).days

        # Status label
        if pnl_pct >= 3.0:
            status = "win"
        elif pnl_pct <= -3.0:
            status = "loss"
        else:
            status = "flat"

        enriched.append({
            "ticker": ticker,
            "signal_date": rec["signal_date"],
            "entry": entry_price,
            "current": current_price,
            "pnl_pct": pnl_pct,
            "status": status,
            "age_days": age_days,
            "score": rec["composite_score"],
        })

    if not enriched:
        return {}

    # Aggregate stats (over all recs, before deduplication)
    returns = [e["pnl_pct"] for e in enriched]
    wins = [r for r in returns if r > 0]
    avg_return = round(sum(returns) / len(returns), 2)
    win_rate = round(len(wins) / len(returns) * 100, 1)

    # Deduplicate by ticker for top/bottom display: keep best P&L per unique ticker
    best_per_ticker: dict[str, dict] = {}
    for e in enriched:
        t = e["ticker"]
        if t not in best_per_ticker or e["pnl_pct"] > best_per_ticker[t]["pnl_pct"]:
            best_per_ticker[t] = e
    deduped = list(best_per_ticker.values())

    # Sort by P&L for top/bottom
    sorted_by_pnl = sorted(deduped, key=lambda x: x["pnl_pct"], reverse=True)
    top5 = sorted_by_pnl[:5]
    bottom5 = sorted_by_pnl[-5:][::-1]  # worst first

    # Age buckets
    fresh = [e for e in enriched if e["age_days"] <= 14]
    older = [e for e in enriched if e["age_days"] > 14]

    result = {
        "date": today,
        "total": len(enriched),
        "avg_return": avg_return,
        "win_rate": win_rate,
        "wins": len([e for e in enriched if e["status"] == "win"]),
        "losses": len([e for e in enriched if e["status"] == "loss"]),
        "flat": len([e for e in enriched if e["status"] == "flat"]),
        "top5": top5,
        "bottom5": bottom5,
        "fresh_avg": round(sum(e["pnl_pct"] for e in fresh) / len(fresh), 2) if fresh else None,
        "older_avg": round(sum(e["pnl_pct"] for e in older) / len(older), 2) if older else None,
        "fresh_count": len(fresh),
        "older_count": len(older),
    }

    logger.info(f"Snapshot done: {len(enriched)} recs, avg {avg_return:+.2f}%, WR {win_rate}%")
    return result


def _fetch_current_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch current prices for a list of tickers."""
    prices = {}
    try:
        if len(tickers) == 1:
            data = yf.download(tickers[0], period="1d", progress=False)
            if not data.empty:
                prices[tickers[0]] = float(data["Close"].iloc[-1])
        else:
            data = yf.download(tickers, period="1d", group_by="ticker", progress=False)
            for t in tickers:
                try:
                    close = data[t]["Close"].dropna()
                    if not close.empty:
                        prices[t] = float(close.iloc[-1])
                except (KeyError, AttributeError):
                    continue
    except Exception as e:
        logger.error(f"Failed to fetch prices: {e}")
    return prices


def _get_period_range(ticker: str, start_date: str, end_date: str) -> tuple[float | None, float | None]:
    """Get max and min price during the evaluation period."""
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
        )
        if df.empty:
            return None, None
        return round(float(df["High"].max()), 2), round(float(df["Low"].min()), 2)
    except Exception:
        return None, None
