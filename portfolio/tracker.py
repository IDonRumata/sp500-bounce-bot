"""Portfolio tracker — P&L tracking for user positions."""
import yfinance as yf
from config import logger
from storage.database import (
    add_portfolio_position, close_portfolio_position,
    get_open_positions, get_closed_positions,
)


def add_position(user_id: str, ticker: str, shares: float, price: float,
                 rec_id: int | None = None) -> bool:
    """Record a buy. Returns True on success."""
    row_id = add_portfolio_position(user_id, ticker, shares, price, rec_id)
    if row_id:
        logger.info(f"Portfolio: {user_id} bought {shares} x {ticker} @ ${price:.2f}")
        return True
    return False


def close_position(user_id: str, ticker: str, price: float) -> dict | None:
    """Close oldest open position for ticker at given price. Returns closed position dict."""
    result = close_portfolio_position(user_id, ticker, price)
    if result:
        logger.info(
            f"Portfolio: {user_id} sold {ticker} @ ${price:.2f}, "
            f"P&L: {result['pnl_pct']:+.2f}% (${result['pnl_abs']:+.2f})"
        )
    return result


def fetch_current_price(ticker: str) -> float | None:
    """Get latest price for a single ticker via yfinance."""
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        price = getattr(info, "last_price", None)
        if price is None:
            hist = t.history(period="1d")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
        return round(float(price), 2) if price else None
    except Exception as e:
        logger.warning(f"fetch_current_price({ticker}): {e}")
        return None


def refresh_prices(positions: list[dict]) -> list[dict]:
    """Enrich open positions with current prices and live P&L."""
    if not positions:
        return []

    tickers = list({p["ticker"] for p in positions})

    # Batch download for efficiency
    current_prices = {}
    try:
        data = yf.download(tickers, period="1d", progress=False, threads=True)
        if len(tickers) == 1:
            if not data.empty:
                current_prices[tickers[0]] = round(float(data["Close"].iloc[-1]), 2)
        else:
            for t in tickers:
                try:
                    price = data[t]["Close"].iloc[-1]
                    if price and price == price:  # not NaN
                        current_prices[t] = round(float(price), 2)
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"refresh_prices batch download failed: {e}")

    # Fallback for missing tickers
    for t in tickers:
        if t not in current_prices:
            p = fetch_current_price(t)
            if p:
                current_prices[t] = p

    enriched = []
    for pos in positions:
        pos = dict(pos)
        cur = current_prices.get(pos["ticker"])
        if cur:
            pos["current_price"] = cur
            pos["pnl_pct"] = round((cur / pos["buy_price"] - 1) * 100, 2) if pos["buy_price"] else 0
            pos["pnl_abs"] = round((cur - pos["buy_price"]) * pos["shares"], 2)
            pos["market_value"] = round(cur * pos["shares"], 2)
        else:
            pos["current_price"] = None
            pos["pnl_pct"] = None
            pos["pnl_abs"] = None
            pos["market_value"] = None
        enriched.append(pos)

    return enriched


def get_portfolio_summary(positions: list[dict]) -> dict:
    """Compute summary from enriched positions (after refresh_prices)."""
    if not positions:
        return {"total_invested": 0, "total_value": 0, "total_pnl": 0, "total_pnl_pct": 0, "count": 0}

    total_invested = sum(p["buy_price"] * p["shares"] for p in positions)
    total_value = sum(p.get("market_value") or (p["buy_price"] * p["shares"]) for p in positions)
    total_pnl = round(total_value - total_invested, 2)
    total_pnl_pct = round((total_value / total_invested - 1) * 100, 2) if total_invested else 0

    # Best / worst by pnl_pct
    with_pnl = [p for p in positions if p.get("pnl_pct") is not None]
    best = max(with_pnl, key=lambda p: p["pnl_pct"]) if with_pnl else None
    worst = min(with_pnl, key=lambda p: p["pnl_pct"]) if with_pnl else None

    return {
        "total_invested": round(total_invested, 2),
        "total_value": round(total_value, 2),
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "count": len(positions),
        "best": best,
        "worst": worst,
    }
