import json
import requests
from bs4 import BeautifulSoup
from config import logger
from storage.database import cache_get, cache_set

CACHE_KEY = "sp500_tickers"
CACHE_TTL = 86400 * 7  # 7 days

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def fetch_sp500_tickers() -> list[dict]:
    """Fetch S&P 500 tickers with sector info. Returns list of dicts with keys: symbol, name, sector."""
    cached = cache_get(CACHE_KEY)
    if cached:
        logger.info("S&P 500 list loaded from cache")
        return json.loads(cached)

    logger.info("Fetching S&P 500 list from Wikipedia...")
    try:
        headers = {"User-Agent": "sp500-bounce-bot/1.0 (compatible; Python/requests)"}
        resp = requests.get(WIKI_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", {"id": "constituents"})
        if not table:
            raise ValueError("Could not find constituents table")

        rows = table.find_all("tr")[1:]  # skip header
        tickers = []
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 4:
                continue
            symbol = cols[0].text.strip().replace(".", "-")  # BRK.B -> BRK-B for yfinance
            name = cols[1].text.strip()
            sector = cols[2].text.strip()
            tickers.append({"symbol": symbol, "name": name, "sector": sector})

        if len(tickers) < 400:
            logger.warning(f"Only {len(tickers)} tickers found, expected ~500")

        cache_set(CACHE_KEY, json.dumps(tickers, ensure_ascii=False), CACHE_TTL)
        logger.info(f"Fetched {len(tickers)} S&P 500 tickers")
        return tickers

    except Exception as e:
        logger.error(f"Failed to fetch S&P 500 list: {e}")
        return _fallback_top100()


def _fallback_top100() -> list[dict]:
    """Fallback: top-100 S&P 500 stocks by weight."""
    symbols = [
        "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "BRK-B", "TSLA", "UNH", "XOM",
        "JNJ", "JPM", "V", "PG", "MA", "HD", "CVX", "MRK", "ABBV", "LLY",
        "PEP", "KO", "COST", "AVGO", "TMO", "WMT", "MCD", "CSCO", "ACN", "ABT",
        "CRM", "DHR", "NKE", "CMCSA", "VZ", "ADBE", "TXN", "NEE", "PM", "BMY",
        "RTX", "HON", "UNP", "AMGN", "INTC", "IBM", "LOW", "QCOM", "CAT", "GS",
        "BA", "SBUX", "INTU", "BLK", "DE", "MDLZ", "AMD", "GILD", "GE", "ADI",
        "MMM", "SYK", "AXP", "ISRG", "TJX", "BKNG", "LRCX", "PLD", "REGN", "VRTX",
        "CB", "MMC", "ZTS", "PGR", "CI", "SCHW", "SO", "DUK", "BDX", "CME",
        "CL", "SLB", "MO", "FIS", "EQIX", "APD", "ITW", "NOC", "EOG", "HUM",
        "UBER", "MCK", "ETN", "PNC", "WM", "EMR", "GD", "F", "GM", "USB",
    ]
    return [{"symbol": s, "name": s, "sector": "Unknown"} for s in symbols]
