from config import WEIGHT_TECHNICAL, WEIGHT_FUNDAMENTAL, WEIGHT_SENTIMENT, WEIGHT_MARKET, logger


def compute_composite_score(technical: dict, fundamental: dict, sentiment: dict, market_context: dict) -> dict:
    """
    Compute weighted composite bounce probability score (0-100).
    Returns dict with sub-scores and final composite.
    """
    tech_score = _technical_score(technical)
    fund_score = _fundamental_score(fundamental)
    sent_score = _sentiment_score(sentiment)
    market_score = _market_score(market_context, technical.get("symbol", ""))

    composite = (
        tech_score * WEIGHT_TECHNICAL +
        fund_score * WEIGHT_FUNDAMENTAL +
        sent_score * WEIGHT_SENTIMENT +
        market_score * WEIGHT_MARKET
    )

    # Clamp to 0-100
    composite = max(0, min(100, composite))

    # Bounce probability label
    if composite >= 75:
        probability = "high"
    elif composite >= 60:
        probability = "medium_high"
    elif composite >= 45:
        probability = "medium"
    elif composite >= 30:
        probability = "low"
    else:
        probability = "very_low"

    return {
        "symbol": technical.get("symbol", ""),
        "technical_score": round(tech_score, 1),
        "fundamental_score": round(fund_score, 1),
        "sentiment_score": round(sent_score, 1),
        "market_score": round(market_score, 1),
        "composite_score": round(composite, 1),
        "bounce_probability": probability,
    }


def _technical_score(t: dict) -> float:
    """Technical sub-score: 0-100 based on oversold + reversal signals."""
    score = 50.0  # start neutral

    # --- RSI ---
    rsi = t.get("rsi")
    if rsi is not None:
        if rsi < 25:
            score += 20
        elif rsi < 30:
            score += 15
        elif rsi < 35:
            score += 8
        elif rsi < 40:
            score += 3
        elif rsi > 65:
            score -= 10

    # --- Bullish RSI Divergence ---
    if t.get("rsi_bullish_divergence"):
        score += 12

    # --- MACD ---
    if t.get("macd_cross_bullish"):
        score += 10
    elif t.get("macd_histogram_rising"):
        score += 6
    elif t.get("macd_histogram") is not None and t["macd_histogram"] < 0 and not t.get("macd_histogram_rising"):
        score -= 5

    # --- Bollinger Bands ---
    if t.get("bb_below_lower"):
        score += 8
    bb_pct_b = t.get("bb_pct_b")
    if bb_pct_b is not None and bb_pct_b < 0.1:
        score += 5
    if t.get("bb_squeeze"):
        score += 3

    # --- StochRSI ---
    stoch = t.get("stoch_rsi")
    if stoch is not None:
        if stoch < 0.1:
            score += 8
        elif stoch < 0.2:
            score += 5
        elif stoch < 0.3:
            score += 2

    # --- Momentum deceleration ---
    roc5 = t.get("roc5")
    roc10 = t.get("roc10")
    if roc5 is not None and roc10 is not None:
        if roc5 > roc10 and roc5 > -5:
            score += 6  # falling is slowing
        elif roc5 < -10:
            score -= 8  # free fall

    # --- Volume ---
    vol_ratio = t.get("volume_ratio")
    if vol_ratio is not None:
        if vol_ratio > 1.5 and t.get("drawdown_pct", 0) < -15:
            score += 8  # capitulation volume
        elif vol_ratio > 1.2:
            score += 3
        elif vol_ratio < 0.5:
            score -= 5  # dead stock

    if t.get("obv_trend") == "up":
        score += 3
    elif t.get("obv_trend") == "down":
        score -= 2

    # --- SMA200 position ---
    if t.get("above_sma200") is True:
        score += 4
    elif t.get("pct_from_sma200") is not None:
        if t["pct_from_sma200"] < -20:
            score -= 5
        elif t["pct_from_sma200"] < -10:
            score -= 2

    # --- Death Cross penalty ---
    if t.get("death_cross"):
        score -= 6

    # --- Support level proximity ---
    price = t.get("current_price")
    s1 = t.get("support1")
    if price and s1 and s1 > 0:
        dist_pct = ((price - s1) / s1) * 100
        if 0 <= dist_pct <= 2:
            score += 4  # near support

    # --- Fibonacci level proximity ---
    fib_dist = t.get("fib_distance_pct")
    fib_level = t.get("fib_nearest_level")
    if fib_dist is not None and fib_level in ("50.0%", "61.8%"):
        if abs(fib_dist) < 3:
            score += 5  # near key Fib level

    # --- Extreme volatility penalty ---
    atr_pct = t.get("atr_pct")
    if atr_pct is not None and atr_pct > 8:
        score -= 4

    return max(0, min(100, score))


def _fundamental_score(f: dict) -> float:
    """Fundamental sub-score: 0-100 based on company quality."""
    if f.get("error"):
        return 40.0  # neutral if no data

    score = 50.0

    # --- P/E vs sector ---
    pe_vs = f.get("pe_vs_sector", "")
    if pe_vs == "undervalued":
        score += 12
    elif pe_vs == "fair":
        score += 5
    elif pe_vs == "overvalued":
        score -= 5
    elif pe_vs == "negative_earnings":
        score -= 10

    # --- Earnings growth ---
    eg = f.get("earnings_growth")
    if eg is not None:
        if eg > 20:
            score += 12
        elif eg > 0:
            score += 6
        elif eg < -30:
            score -= 10
        elif eg < 0:
            score -= 5

    # --- Revenue growth ---
    rg = f.get("revenue_growth")
    if rg is not None:
        if rg > 10:
            score += 8
        elif rg > 0:
            score += 4
        elif rg < -10:
            score -= 6
        elif rg < 0:
            score -= 3

    # --- Profit margin ---
    pm = f.get("profit_margin")
    if pm is not None:
        if pm > 20:
            score += 8
        elif pm > 10:
            score += 4
        elif pm < 0:
            score -= 8

    # --- Debt/Equity ---
    de = f.get("debt_to_equity")
    if de is not None:
        if de < 50:
            score += 6
        elif de < 100:
            score += 3
        elif de > 200:
            score -= 6
        elif de > 150:
            score -= 3

    # --- Free Cash Flow ---
    if f.get("fcf_positive"):
        score += 6
    elif f.get("free_cash_flow") is not None:
        score -= 6

    # --- ROE ---
    roe = f.get("roe")
    if roe is not None:
        if roe > 20:
            score += 5
        elif roe > 10:
            score += 2
        elif roe < 0:
            score -= 5

    # --- Current ratio ---
    cr = f.get("current_ratio")
    if cr is not None:
        if cr > 2.0:
            score += 3
        elif cr > 1.5:
            score += 1
        elif cr < 1.0:
            score -= 4

    # --- Dividend ---
    dy = f.get("dividend_yield")
    if dy is not None and dy > 0:
        score += 2

    # --- Analyst target upside ---
    upside = f.get("upside_to_target")
    if upside is not None:
        if upside > 30:
            score += 5
        elif upside > 15:
            score += 3

    return max(0, min(100, score))


def _sentiment_score(s: dict) -> float:
    """Sentiment sub-score: 0-100 based on news, analysts, insiders, earnings."""
    score = 50.0

    # --- Analyst consensus ---
    consensus = s.get("analyst_consensus", "")
    if consensus == "buy":
        score += 15
    elif consensus == "hold":
        score += 3
    elif consensus == "sell":
        score -= 12

    # --- Insider activity ---
    insider = s.get("insider_sentiment", "")
    if insider in ("strong_buy", "bullish"):
        score += 15
    elif insider in ("strong_sell", "bearish"):
        score -= 10

    # --- News sentiment ---
    news = s.get("news_sentiment", "")
    if news == "positive":
        score += 8
    elif news == "negative":
        score -= 6
    # no_data → keep neutral

    # --- Earnings proximity risk ---
    if s.get("earnings_within_7d"):
        score -= 12  # high uncertainty
    elif s.get("earnings_within_14d"):
        score -= 5
    else:
        score += 5  # no imminent earnings = less risk

    return max(0, min(100, score))


def _market_score(ctx: dict, symbol: str = "") -> float:
    """Market context sub-score: 0-100 based on SPY, VIX, sector strength."""
    score = 50.0

    regime = ctx.get("regime", "neutral")
    if regime == "bullish":
        score += 20
    elif regime == "neutral":
        score += 5
    elif regime == "weak":
        score -= 5
    elif regime == "bearish":
        score -= 12
    elif regime == "panic":
        score -= 18

    # VIX
    vix = ctx.get("vix", {})
    vix_level = vix.get("level")
    if vix_level == 3:  # elevated = opportunity for contrarian
        score += 10
    elif vix_level == 4:  # high fear
        score += 5
    elif vix_level == 5:  # panic → too risky
        score -= 8
    elif vix_level == 1:  # complacent
        score += 2

    # Sector strength (if available)
    sectors = ctx.get("sectors", [])
    for sec in sectors:
        if sec.get("relative_strength") == "strong":
            score += 5
            break
        elif sec.get("relative_strength") == "weak":
            score -= 3
            break

    return max(0, min(100, score))
