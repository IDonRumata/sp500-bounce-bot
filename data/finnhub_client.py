import time
import requests
from datetime import datetime, timedelta
from config import FINNHUB_API_KEY, logger

BASE_URL = "https://finnhub.io/api/v1"
RATE_LIMIT_DELAY = 1.1  # Finnhub free: 60 req/min


def _get(endpoint: str, params: dict = None) -> dict | list | None:
    params = params or {}
    params["token"] = FINNHUB_API_KEY
    try:
        resp = requests.get(f"{BASE_URL}{endpoint}", params=params, timeout=15)
        if resp.status_code == 429:
            logger.warning("Finnhub rate limit hit, waiting 60s...")
            time.sleep(60)
            resp = requests.get(f"{BASE_URL}{endpoint}", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Finnhub {endpoint} failed: {e}")
        return None


def get_company_news(symbol: str, days: int = 7) -> list[dict]:
    """Get recent news for a symbol."""
    if not FINNHUB_API_KEY:
        return []
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    data = _get("/company-news", {"symbol": symbol, "from": from_date, "to": to_date})
    time.sleep(RATE_LIMIT_DELAY)
    if not data or not isinstance(data, list):
        return []
    return data[:20]  # limit to 20 most recent


def get_analyst_recommendations(symbol: str) -> list[dict]:
    """Get analyst recommendations (buy/hold/sell)."""
    if not FINNHUB_API_KEY:
        return []
    data = _get("/stock/recommendation", {"symbol": symbol})
    time.sleep(RATE_LIMIT_DELAY)
    if not data or not isinstance(data, list):
        return []
    return data[:3]  # last 3 periods


def get_earnings_calendar(symbol: str) -> dict | None:
    """Get upcoming earnings date."""
    if not FINNHUB_API_KEY:
        return None
    from_date = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
    data = _get("/calendar/earnings", {"symbol": symbol, "from": from_date, "to": to_date})
    time.sleep(RATE_LIMIT_DELAY)
    if not data or "earningsCalendar" not in data:
        return None
    calendars = data["earningsCalendar"]
    if not calendars:
        return None
    # Return the nearest future earnings
    return calendars[0]


def get_insider_transactions(symbol: str) -> list[dict]:
    """Get insider transactions for a symbol."""
    if not FINNHUB_API_KEY:
        return []
    data = _get("/stock/insider-transactions", {"symbol": symbol})
    time.sleep(RATE_LIMIT_DELAY)
    if not data or "data" not in data:
        return []
    return data["data"][:30]  # last 30 transactions


def analyze_insider_sentiment(transactions: list[dict]) -> dict:
    """Analyze insider transactions: net buying vs selling over last 90 days."""
    cutoff = datetime.now() - timedelta(days=90)
    buy_value = 0.0
    sell_value = 0.0
    buy_count = 0
    sell_count = 0

    for tx in transactions:
        try:
            tx_date = datetime.strptime(tx.get("transactionDate", ""), "%Y-%m-%d")
            if tx_date < cutoff:
                continue
        except (ValueError, TypeError):
            continue

        change = tx.get("change", 0) or 0
        price = tx.get("transactionPrice", 0) or 0
        value = abs(change * price)

        code = (tx.get("transactionCode") or "").upper()
        if code in ("P", "A") or change > 0:  # Purchase or Award
            buy_value += value
            buy_count += 1
        elif code == "S" or change < 0:  # Sale
            sell_value += value
            sell_count += 1

    net_value = buy_value - sell_value
    sentiment = "neutral"
    if buy_count > 0 and sell_count == 0:
        sentiment = "strong_buy"
    elif buy_value > sell_value * 1.5:
        sentiment = "bullish"
    elif sell_value > buy_value * 2:
        sentiment = "bearish"
    elif sell_count > 0 and buy_count == 0:
        sentiment = "strong_sell"

    return {
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_value": round(buy_value, 2),
        "sell_value": round(sell_value, 2),
        "net_value": round(net_value, 2),
        "sentiment": sentiment,
    }


def analyze_news_sentiment(news: list[dict]) -> dict:
    """Simple keyword-based news sentiment analysis."""
    positive_words = {
        "beat", "beats", "exceeded", "upgrade", "upgrades", "raised", "raises",
        "growth", "strong", "profit", "surge", "surges", "record", "bullish",
        "outperform", "buy", "positive", "recovery", "rebound", "upside",
        "dividend", "buyback", "innovation", "expansion", "deal",
    }
    negative_words = {
        "miss", "misses", "missed", "downgrade", "downgrades", "cut", "cuts",
        "loss", "weak", "decline", "declines", "fell", "falls", "bearish",
        "underperform", "sell", "negative", "warning", "risk", "lawsuit",
        "layoff", "layoffs", "debt", "investigation", "recall", "bankruptcy",
        "fraud", "probe", "fine", "penalty",
    }

    pos_count = 0
    neg_count = 0
    total = len(news)

    for article in news:
        headline = (article.get("headline") or "").lower()
        summary = (article.get("summary") or "").lower()
        text = headline + " " + summary

        words = set(text.split())
        p = len(words & positive_words)
        n = len(words & negative_words)

        if p > n:
            pos_count += 1
        elif n > p:
            neg_count += 1

    if total == 0:
        sentiment = "no_data"
    elif pos_count > neg_count * 1.5:
        sentiment = "positive"
    elif neg_count > pos_count * 1.5:
        sentiment = "negative"
    else:
        sentiment = "mixed"

    return {
        "total_articles": total,
        "positive": pos_count,
        "negative": neg_count,
        "neutral": total - pos_count - neg_count,
        "sentiment": sentiment,
    }


def get_full_sentiment_data(symbol: str) -> dict:
    """Collect all sentiment data for a symbol from Finnhub."""
    news = get_company_news(symbol)
    news_sentiment = analyze_news_sentiment(news)

    recs = get_analyst_recommendations(symbol)
    rec_summary = _summarize_recommendations(recs)

    earnings = get_earnings_calendar(symbol)
    earnings_info = _parse_earnings(earnings)

    insider_txs = get_insider_transactions(symbol)
    insider_sentiment = analyze_insider_sentiment(insider_txs)

    return {
        "news": news_sentiment,
        "analyst": rec_summary,
        "earnings": earnings_info,
        "insider": insider_sentiment,
    }


def _summarize_recommendations(recs: list[dict]) -> dict:
    if not recs:
        return {"consensus": "no_data", "buy": 0, "hold": 0, "sell": 0, "strong_buy": 0, "strong_sell": 0}

    latest = recs[0]
    sb = latest.get("strongBuy", 0)
    b = latest.get("buy", 0)
    h = latest.get("hold", 0)
    s = latest.get("sell", 0)
    ss = latest.get("strongSell", 0)
    total = sb + b + h + s + ss

    if total == 0:
        consensus = "no_data"
    elif (sb + b) / total > 0.6:
        consensus = "buy"
    elif (s + ss) / total > 0.4:
        consensus = "sell"
    else:
        consensus = "hold"

    return {
        "consensus": consensus,
        "strong_buy": sb,
        "buy": b,
        "hold": h,
        "sell": s,
        "strong_sell": ss,
        "total": total,
        "period": latest.get("period", ""),
    }


def _parse_earnings(earnings: dict | None) -> dict:
    if not earnings:
        return {"next_date": None, "days_until": None, "within_14_days": False}

    date_str = earnings.get("date")
    if not date_str:
        return {"next_date": None, "days_until": None, "within_14_days": False}

    try:
        earn_date = datetime.strptime(date_str, "%Y-%m-%d")
        days_until = (earn_date - datetime.now()).days
        return {
            "next_date": date_str,
            "days_until": days_until,
            "within_14_days": 0 <= days_until <= 14,
            "within_7_days": 0 <= days_until <= 7,
        }
    except ValueError:
        return {"next_date": date_str, "days_until": None, "within_14_days": False}
