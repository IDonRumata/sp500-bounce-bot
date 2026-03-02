import yfinance as yf
import pandas as pd
import numpy as np
from config import SECTOR_ETFS, logger


def fetch_market_context() -> dict:
    """Fetch and analyze broad market conditions: SPY, VIX, sector ETFs."""
    context = {
        "spy": _analyze_spy(),
        "vix": _analyze_vix(),
        "sectors": _analyze_sectors(),
        "regime": "neutral",
    }

    # Determine overall market regime
    spy = context["spy"]
    vix = context["vix"]

    if spy["rsi"] < 35 and not spy["above_sma50"] and vix["level"] > 30:
        context["regime"] = "panic"
    elif spy["rsi"] < 42 and not spy["above_sma50"]:
        context["regime"] = "bearish"
    elif spy["rsi"] < 48:
        context["regime"] = "weak"
    elif spy["rsi"] > 55 and spy["above_sma50"] and spy["above_sma200"]:
        context["regime"] = "bullish"
    else:
        context["regime"] = "neutral"

    return context


def _analyze_spy() -> dict:
    """Analyze SPY for market direction."""
    try:
        df = yf.download("SPY", period="6mo", interval="1d", progress=False)
        if df.empty or len(df) < 50:
            return _default_spy()

        closes = df["Close"].dropna()
        current = float(closes.iloc[-1])

        # RSI(14)
        rsi = _calc_rsi(closes, 14)

        # SMAs
        sma50 = float(closes.rolling(50).mean().iloc[-1])
        sma200 = float(closes.rolling(min(200, len(closes))).mean().iloc[-1])

        # Drawdown from high
        high_6m = float(closes.max())
        drawdown = ((current - high_6m) / high_6m) * 100

        # Trend
        sma20 = float(closes.rolling(20).mean().iloc[-1])

        return {
            "price": round(current, 2),
            "rsi": round(rsi, 2),
            "sma50": round(sma50, 2),
            "sma200": round(sma200, 2),
            "sma20": round(sma20, 2),
            "above_sma50": current > sma50,
            "above_sma200": current > sma200,
            "drawdown": round(drawdown, 2),
            "trend": "up" if current > sma20 > sma50 else ("down" if current < sma20 < sma50 else "mixed"),
        }
    except Exception as e:
        logger.error(f"SPY analysis failed: {e}")
        return _default_spy()


def _analyze_vix() -> dict:
    """Analyze VIX for fear/greed."""
    try:
        df = yf.download("^VIX", period="3mo", interval="1d", progress=False)
        if df.empty:
            return {"value": None, "level": None, "label": "unknown"}

        current = float(df["Close"].iloc[-1])

        if current < 15:
            label = "complacent"
            level = 1
        elif current < 20:
            label = "normal"
            level = 2
        elif current < 25:
            label = "elevated"
            level = 3
        elif current < 30:
            label = "high_fear"
            level = 4
        else:
            label = "panic"
            level = 5

        return {"value": round(current, 2), "level": level, "label": label}
    except Exception as e:
        logger.error(f"VIX analysis failed: {e}")
        return {"value": None, "level": None, "label": "unknown"}


def _analyze_sectors() -> list[dict]:
    """Analyze sector ETFs for rotation."""
    try:
        etf_symbols = list(SECTOR_ETFS.values())
        data = yf.download(etf_symbols, period="3mo", interval="1d", group_by="ticker", progress=False)

        sectors = []
        for sector_name, etf_sym in SECTOR_ETFS.items():
            try:
                if len(etf_symbols) == 1:
                    df = data
                else:
                    df = data[etf_sym]
                closes = df["Close"].dropna()
                if len(closes) < 20:
                    continue

                current = float(closes.iloc[-1])
                high_3m = float(closes.max())
                rsi = _calc_rsi(closes, 14)
                pct_from_high = ((current - high_3m) / high_3m) * 100

                sectors.append({
                    "sector": sector_name,
                    "etf": etf_sym,
                    "price": round(current, 2),
                    "rsi": round(rsi, 2),
                    "pct_from_high": round(pct_from_high, 2),
                    "relative_strength": "strong" if rsi > 55 else ("weak" if rsi < 40 else "neutral"),
                })
            except (KeyError, AttributeError):
                continue

        # Sort by RSI descending (strongest first)
        sectors.sort(key=lambda x: x["rsi"], reverse=True)
        return sectors

    except Exception as e:
        logger.error(f"Sector analysis failed: {e}")
        return []


def get_sector_strength(sector_name: str, sectors: list[dict]) -> str:
    """Get relative strength for a specific sector."""
    for s in sectors:
        if s["sector"].lower() == sector_name.lower():
            return s["relative_strength"]
    return "neutral"


def _calc_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    last_loss = float(loss.iloc[-1])
    if last_loss == 0:
        return 100.0
    rs = float(gain.iloc[-1]) / last_loss
    return 100 - (100 / (1 + rs))


def _default_spy() -> dict:
    return {
        "price": None, "rsi": 50, "sma50": None, "sma200": None, "sma20": None,
        "above_sma50": True, "above_sma200": True, "drawdown": 0, "trend": "mixed",
    }
