import pandas as pd
import numpy as np
from config import logger


def full_technical_analysis(df: pd.DataFrame, symbol: str = "") -> dict:
    """
    Comprehensive technical analysis for a single stock.
    df must have columns: Open, High, Low, Close, Volume
    Returns dict with all indicators + interpretation.
    """
    closes = df["Close"].dropna()
    highs = df["High"].dropna()
    lows = df["Low"].dropna()
    volumes = df["Volume"].dropna()

    if len(closes) < 50:
        return {"error": "Insufficient data", "symbol": symbol}

    current_price = float(closes.iloc[-1])
    high_6m = float(closes.max())
    low_6m = float(closes.min())
    drawdown = ((current_price - high_6m) / high_6m) * 100

    result = {
        "symbol": symbol,
        "current_price": round(current_price, 2),
        "high_6m": round(high_6m, 2),
        "low_6m": round(low_6m, 2),
        "drawdown_pct": round(drawdown, 2),
    }

    # --- RSI(14) ---
    rsi_val = _rsi(closes, 14)
    result["rsi"] = round(rsi_val, 2)
    result["rsi_signal"] = "oversold" if rsi_val < 30 else ("near_oversold" if rsi_val < 40 else "neutral")

    # --- RSI Divergence ---
    result["rsi_bullish_divergence"] = _detect_rsi_divergence(closes, 14)

    # --- MACD(12, 26, 9) ---
    macd_data = _macd(closes)
    result.update(macd_data)

    # --- Bollinger Bands ---
    bb_data = _bollinger_bands(closes, 20, 2)
    result.update(bb_data)

    # --- SMA 20/50/200 ---
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, min(200, len(closes) - 1))
    result["sma20"] = round(sma20, 2) if sma20 else None
    result["sma50"] = round(sma50, 2) if sma50 else None
    result["sma200"] = round(sma200, 2) if sma200 else None

    if sma200:
        result["pct_from_sma200"] = round(((current_price - sma200) / sma200) * 100, 2)
        result["above_sma200"] = current_price > sma200
    else:
        result["pct_from_sma200"] = None
        result["above_sma200"] = None

    # Golden Cross / Death Cross
    if sma50 and sma200:
        result["golden_cross"] = sma50 > sma200
        result["death_cross"] = sma50 < sma200
    else:
        result["golden_cross"] = None
        result["death_cross"] = None

    # --- ATR(14) ---
    atr_val = _atr(highs, lows, closes, 14)
    result["atr"] = round(atr_val, 2) if atr_val else None
    result["atr_pct"] = round((atr_val / current_price) * 100, 2) if atr_val and current_price > 0 else None

    # --- StochRSI ---
    stoch_rsi = _stoch_rsi(closes, 14, 14)
    result["stoch_rsi"] = round(stoch_rsi, 4) if stoch_rsi is not None else None

    # --- Volume Analysis ---
    vol_data = _volume_analysis(volumes, closes)
    result.update(vol_data)

    # --- Momentum ROC ---
    result["roc5"] = round(_roc(closes, 5), 2)
    result["roc10"] = round(_roc(closes, 10), 2)
    result["roc20"] = round(_roc(closes, 20), 2)
    result["momentum_deceleration"] = result["roc5"] > result["roc10"] if result["roc5"] is not None else None

    # --- Pivot Points (Classic) ---
    pivots = _pivot_points(highs, lows, closes)
    result.update(pivots)

    # --- Fibonacci Retracement ---
    fib = _fibonacci_retracement(high_6m, low_6m, current_price)
    result.update(fib)

    return result


# ---- Indicator implementations ----

def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1/period, min_periods=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/period, min_periods=period).mean()
    last_loss = float(loss.iloc[-1])
    if last_loss == 0:
        return 100.0
    rs = float(gain.iloc[-1]) / last_loss
    return 100 - (100 / (1 + rs))


def _detect_rsi_divergence(closes: pd.Series, period: int = 14) -> bool:
    """
    Detect bullish RSI divergence: price makes lower low but RSI makes higher low.
    Check last 20 bars for two swing lows.
    """
    if len(closes) < 30:
        return False

    try:
        # Compute RSI series
        delta = closes.diff()
        gain = delta.where(delta > 0, 0.0).ewm(alpha=1/period, min_periods=period).mean()
        loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/period, min_periods=period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi_series = 100 - (100 / (1 + rs))
        rsi_series = rsi_series.dropna()

        if len(rsi_series) < 20:
            return False

        # Look at last 20 bars, find two local lows in price
        price_window = closes.iloc[-20:].values
        rsi_window = rsi_series.iloc[-20:].values

        # Find local mins (simple: lower than neighbors)
        price_lows = []
        for i in range(1, len(price_window) - 1):
            if price_window[i] < price_window[i-1] and price_window[i] < price_window[i+1]:
                price_lows.append((i, price_window[i], rsi_window[min(i, len(rsi_window)-1)]))

        if len(price_lows) < 2:
            return False

        # Check last two lows: price lower low but RSI higher low
        prev_low = price_lows[-2]
        curr_low = price_lows[-1]

        price_lower = curr_low[1] < prev_low[1]
        rsi_higher = curr_low[2] > prev_low[2]

        return price_lower and rsi_higher

    except Exception:
        return False


def _macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    if len(closes) < slow + signal:
        return {"macd": None, "macd_signal": None, "macd_histogram": None,
                "macd_cross_bullish": None, "macd_histogram_rising": None}

    ema_fast = closes.ewm(span=fast).mean()
    ema_slow = closes.ewm(span=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal).mean()
    histogram = macd_line - signal_line

    macd_val = float(macd_line.iloc[-1])
    signal_val = float(signal_line.iloc[-1])
    hist_val = float(histogram.iloc[-1])

    # Bullish crossover: MACD crosses above signal
    prev_macd = float(macd_line.iloc[-2])
    prev_signal = float(signal_line.iloc[-2])
    cross_bullish = prev_macd < prev_signal and macd_val >= signal_val

    # Histogram rising (momentum improving)
    prev_hist = float(histogram.iloc[-2])
    hist_rising = hist_val > prev_hist

    return {
        "macd": round(macd_val, 4),
        "macd_signal": round(signal_val, 4),
        "macd_histogram": round(hist_val, 4),
        "macd_cross_bullish": cross_bullish,
        "macd_histogram_rising": hist_rising,
    }


def _bollinger_bands(closes: pd.Series, period: int = 20, num_std: float = 2) -> dict:
    if len(closes) < period:
        return {"bb_upper": None, "bb_lower": None, "bb_pct_b": None, "bb_squeeze": None, "bb_below_lower": None}

    sma = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    upper = sma + num_std * std
    lower = sma - num_std * std

    curr_price = float(closes.iloc[-1])
    curr_upper = float(upper.iloc[-1])
    curr_lower = float(lower.iloc[-1])
    curr_sma = float(sma.iloc[-1])

    # %B = (Price - Lower) / (Upper - Lower)
    band_width = curr_upper - curr_lower
    pct_b = (curr_price - curr_lower) / band_width if band_width > 0 else 0.5

    # Squeeze: bandwidth is narrowing (low volatility → potential breakout)
    bw_series = (upper - lower) / sma
    if len(bw_series.dropna()) >= 20:
        avg_bw = float(bw_series.rolling(20).mean().iloc[-1])
        curr_bw = float(bw_series.iloc[-1])
        squeeze = curr_bw < avg_bw * 0.8
    else:
        squeeze = False

    return {
        "bb_upper": round(curr_upper, 2),
        "bb_lower": round(curr_lower, 2),
        "bb_middle": round(curr_sma, 2),
        "bb_pct_b": round(pct_b, 4),
        "bb_squeeze": squeeze,
        "bb_below_lower": curr_price < curr_lower,
    }


def _sma(series: pd.Series, period: int) -> float | None:
    if len(series) < period:
        return None
    return float(series.rolling(period).mean().iloc[-1])


def _atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    high = highs.values
    low = lows.values
    close = closes.values
    tr_list = []
    for i in range(1, len(close)):
        tr = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
        tr_list.append(tr)
    tr_series = pd.Series(tr_list)
    return float(tr_series.rolling(period).mean().iloc[-1])


def _stoch_rsi(closes: pd.Series, rsi_period: int = 14, stoch_period: int = 14) -> float | None:
    if len(closes) < rsi_period + stoch_period + 5:
        return None

    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1/rsi_period, min_periods=rsi_period).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/rsi_period, min_periods=rsi_period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    rsi_series = rsi_series.dropna()

    if len(rsi_series) < stoch_period:
        return None

    rsi_min = rsi_series.rolling(stoch_period).min()
    rsi_max = rsi_series.rolling(stoch_period).max()
    denom = rsi_max - rsi_min
    stoch = (rsi_series - rsi_min) / denom.replace(0, np.nan)
    last = stoch.iloc[-1]
    return float(last) if not pd.isna(last) else None


def _volume_analysis(volumes: pd.Series, closes: pd.Series) -> dict:
    if len(volumes) < 20:
        return {"volume_ratio": None, "volume_spike": False, "obv_trend": "unknown"}

    avg20 = float(volumes.rolling(20).mean().iloc[-1])
    current_vol = float(volumes.iloc[-1])
    ratio = current_vol / avg20 if avg20 > 0 else 1.0

    # Volume spike: current > 1.5x average
    spike = ratio > 1.5

    # OBV trend (On-Balance Volume)
    obv = (np.sign(closes.diff()) * volumes).cumsum()
    if len(obv) >= 10:
        obv_sma10 = float(obv.rolling(10).mean().iloc[-1])
        obv_current = float(obv.iloc[-1])
        obv_trend = "up" if obv_current > obv_sma10 else "down"
    else:
        obv_trend = "unknown"

    return {
        "volume_ratio": round(ratio, 2),
        "volume_spike": spike,
        "avg_volume_20d": int(avg20),
        "current_volume": int(current_vol),
        "obv_trend": obv_trend,
    }


def _roc(closes: pd.Series, period: int) -> float:
    if len(closes) <= period:
        return 0.0
    prev = float(closes.iloc[-(period+1)])
    curr = float(closes.iloc[-1])
    return ((curr / prev) - 1) * 100 if prev != 0 else 0.0


def _pivot_points(highs: pd.Series, lows: pd.Series, closes: pd.Series) -> dict:
    """Classic pivot points from previous day."""
    if len(closes) < 2:
        return {"pivot": None, "support1": None, "support2": None, "resistance1": None, "resistance2": None}

    h = float(highs.iloc[-2])
    l = float(lows.iloc[-2])
    c = float(closes.iloc[-2])

    pivot = (h + l + c) / 3
    s1 = 2 * pivot - h
    s2 = pivot - (h - l)
    r1 = 2 * pivot - l
    r2 = pivot + (h - l)

    return {
        "pivot": round(pivot, 2),
        "support1": round(s1, 2),
        "support2": round(s2, 2),
        "resistance1": round(r1, 2),
        "resistance2": round(r2, 2),
    }


def _fibonacci_retracement(high: float, low: float, current_price: float) -> dict:
    """Fibonacci retracement levels from swing high to swing low."""
    diff = high - low
    if diff <= 0:
        return {"fib_236": None, "fib_382": None, "fib_500": None, "fib_618": None, "fib_nearest_level": None}

    fib_236 = high - 0.236 * diff
    fib_382 = high - 0.382 * diff
    fib_500 = high - 0.500 * diff
    fib_618 = high - 0.618 * diff

    # Find nearest Fibonacci level to current price
    levels = {"23.6%": fib_236, "38.2%": fib_382, "50.0%": fib_500, "61.8%": fib_618}
    nearest = min(levels.items(), key=lambda x: abs(x[1] - current_price))

    return {
        "fib_236": round(fib_236, 2),
        "fib_382": round(fib_382, 2),
        "fib_500": round(fib_500, 2),
        "fib_618": round(fib_618, 2),
        "fib_nearest_level": nearest[0],
        "fib_nearest_price": round(nearest[1], 2),
        "fib_distance_pct": round(((current_price - nearest[1]) / nearest[1]) * 100, 2),
    }
