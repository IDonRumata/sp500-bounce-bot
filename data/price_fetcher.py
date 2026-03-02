import yfinance as yf
import pandas as pd
import numpy as np
from config import logger


def fetch_batch_history(symbols: list[str], period: str = "6mo", interval: str = "1d") -> dict[str, pd.DataFrame]:
    """Download OHLCV history for a batch of symbols using yfinance multi-download."""
    logger.info(f"Downloading price history for {len(symbols)} tickers ({period})...")
    try:
        data = yf.download(
            tickers=symbols,
            period=period,
            interval=interval,
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )

        result = {}
        if len(symbols) == 1:
            sym = symbols[0]
            if not data.empty:
                result[sym] = data
        else:
            for sym in symbols:
                try:
                    df = data[sym].dropna(how="all")
                    if not df.empty and len(df) >= 20:
                        result[sym] = df
                except (KeyError, AttributeError):
                    continue

        logger.info(f"Got price data for {len(result)}/{len(symbols)} tickers")
        return result

    except Exception as e:
        logger.error(f"Batch download failed: {e}")
        return {}


def fetch_single_history(symbol: str, period: str = "6mo") -> pd.DataFrame | None:
    """Download OHLCV for a single ticker."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, auto_adjust=True)
        if df.empty or len(df) < 20:
            return None
        return df
    except Exception as e:
        logger.error(f"Failed to fetch {symbol}: {e}")
        return None


def fetch_fundamentals(symbol: str) -> dict:
    """Fetch fundamental data for a single stock using yfinance."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        fundamentals = {
            "symbol": symbol,
            "name": info.get("longName") or info.get("shortName", symbol),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "market_cap": info.get("marketCap"),
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),

            # Valuation
            "pe_trailing": info.get("trailingPE"),
            "pe_forward": info.get("forwardPE"),
            "peg_ratio": info.get("pegRatio"),
            "price_to_book": info.get("priceToBook"),

            # Profitability
            "profit_margin": info.get("profitMargins"),
            "operating_margin": info.get("operatingMargins"),
            "gross_margin": info.get("grossMargins"),
            "roe": info.get("returnOnEquity"),
            "roa": info.get("returnOnAssets"),

            # Growth
            "earnings_growth": info.get("earningsGrowth"),
            "revenue_growth": info.get("revenueGrowth"),
            "eps_trailing": info.get("trailingEps"),
            "eps_forward": info.get("forwardEps"),

            # Financial health
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "quick_ratio": info.get("quickRatio"),
            "free_cash_flow": info.get("freeCashflow"),
            "total_cash": info.get("totalCash"),
            "total_debt": info.get("totalDebt"),

            # Dividend
            "dividend_yield": info.get("dividendYield"),

            # Analyst
            "target_mean_price": info.get("targetMeanPrice"),
            "target_low_price": info.get("targetLowPrice"),
            "target_high_price": info.get("targetHighPrice"),
            "recommendation_key": info.get("recommendationKey"),
            "number_of_analysts": info.get("numberOfAnalystOpinions"),
        }

        return fundamentals

    except Exception as e:
        logger.error(f"Failed to fetch fundamentals for {symbol}: {e}")
        return {"symbol": symbol, "error": str(e)}


def fetch_batch_fundamentals(symbols: list[str]) -> list[dict]:
    """Fetch fundamentals for a list of symbols (sequential to avoid rate limits)."""
    results = []
    for i, sym in enumerate(symbols):
        logger.debug(f"Fetching fundamentals {i+1}/{len(symbols)}: {sym}")
        data = fetch_fundamentals(sym)
        results.append(data)
    return results


def quick_prefilter(symbols_with_info: list[dict], max_price: float, min_drawdown: float, max_rsi: float) -> list[dict]:
    """
    Quick pre-filter: download all prices, compute basic RSI and drawdown,
    return only candidates meeting thresholds.
    """
    all_symbols = [s["symbol"] for s in symbols_with_info]
    sym_to_info = {s["symbol"]: s for s in symbols_with_info}

    # Batch download
    history = fetch_batch_history(all_symbols)

    candidates = []
    for sym, df in history.items():
        try:
            closes = df["Close"].dropna()
            if len(closes) < 50:
                continue

            current_price = float(closes.iloc[-1])

            # Price filter
            if current_price > max_price or current_price < 1.0:
                continue

            # Drawdown
            high_6m = float(closes.max())
            drawdown = ((current_price - high_6m) / high_6m) * 100
            if drawdown > min_drawdown:  # min_drawdown is negative like -15
                continue

            # Quick RSI(14) — EWM-based to match deep analysis
            delta = closes.diff()
            gain = delta.where(delta > 0, 0.0).ewm(alpha=1/14, min_periods=14).mean()
            loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/14, min_periods=14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))
            current_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50

            if current_rsi > max_rsi:
                continue

            info = sym_to_info.get(sym, {})
            candidates.append({
                "symbol": sym,
                "name": info.get("name", sym),
                "sector": info.get("sector", "Unknown"),
                "current_price": round(current_price, 2),
                "drawdown": round(drawdown, 2),
                "rsi_quick": round(current_rsi, 2),
                "df": df,  # pass dataframe for deep analysis
            })

        except Exception as e:
            logger.debug(f"Pre-filter skip {sym}: {e}")
            continue

    logger.info(f"Pre-filter: {len(candidates)}/{len(history)} candidates passed (price<={max_price}, drawdown<={min_drawdown}%, RSI<={max_rsi})")
    return candidates
