"""
Check pending recommendations against current prices.
Run daily after market close to evaluate recommendation accuracy.
"""
import yfinance as yf
from datetime import datetime, timedelta
from config import SUCCESS_THRESHOLD_PCT, FAILURE_THRESHOLD_PCT, logger
from storage.database import get_pending_recommendations, update_recommendation_result


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
