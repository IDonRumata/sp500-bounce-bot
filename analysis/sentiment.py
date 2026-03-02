from data.finnhub_client import get_full_sentiment_data
from config import logger


def analyze_sentiment(symbol: str) -> dict:
    """
    Full sentiment analysis for a stock using Finnhub data.
    Returns structured dict with news, analyst, earnings, insider data.
    """
    try:
        data = get_full_sentiment_data(symbol)
    except Exception as e:
        logger.error(f"Sentiment analysis failed for {symbol}: {e}")
        return _empty_sentiment(symbol)

    result = {"symbol": symbol}

    # News sentiment
    news = data.get("news", {})
    result["news_total"] = news.get("total_articles", 0)
    result["news_positive"] = news.get("positive", 0)
    result["news_negative"] = news.get("negative", 0)
    result["news_sentiment"] = news.get("sentiment", "no_data")

    # Analyst recommendations
    analyst = data.get("analyst", {})
    result["analyst_consensus"] = analyst.get("consensus", "no_data")
    result["analyst_buy"] = analyst.get("strong_buy", 0) + analyst.get("buy", 0)
    result["analyst_hold"] = analyst.get("hold", 0)
    result["analyst_sell"] = analyst.get("sell", 0) + analyst.get("strong_sell", 0)
    result["analyst_total"] = analyst.get("total", 0)

    # Earnings calendar
    earnings = data.get("earnings", {})
    result["earnings_next_date"] = earnings.get("next_date")
    result["earnings_days_until"] = earnings.get("days_until")
    result["earnings_within_14d"] = earnings.get("within_14_days", False)
    result["earnings_within_7d"] = earnings.get("within_7_days", False)

    # Insider transactions
    insider = data.get("insider", {})
    result["insider_buy_count"] = insider.get("buy_count", 0)
    result["insider_sell_count"] = insider.get("sell_count", 0)
    result["insider_net_value"] = insider.get("net_value", 0)
    result["insider_sentiment"] = insider.get("sentiment", "neutral")

    return result


def _empty_sentiment(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "news_total": 0, "news_positive": 0, "news_negative": 0, "news_sentiment": "no_data",
        "analyst_consensus": "no_data", "analyst_buy": 0, "analyst_hold": 0, "analyst_sell": 0, "analyst_total": 0,
        "earnings_next_date": None, "earnings_days_until": None, "earnings_within_14d": False, "earnings_within_7d": False,
        "insider_buy_count": 0, "insider_sell_count": 0, "insider_net_value": 0, "insider_sentiment": "neutral",
    }
