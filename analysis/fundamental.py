from config import logger

# Sector median P/E ratios (approximate, updated periodically)
SECTOR_PE_MEDIANS = {
    "Technology": 28,
    "Information Technology": 28,
    "Healthcare": 22,
    "Health Care": 22,
    "Financials": 14,
    "Consumer Discretionary": 25,
    "Communication Services": 18,
    "Industrials": 21,
    "Consumer Staples": 23,
    "Energy": 12,
    "Utilities": 18,
    "Real Estate": 35,
    "Materials": 16,
}


def analyze_fundamentals(fund_data: dict) -> dict:
    """
    Analyze fundamental data and return structured metrics with grades.
    fund_data comes from price_fetcher.fetch_fundamentals().
    """
    symbol = fund_data.get("symbol", "?")

    if fund_data.get("error"):
        return {"symbol": symbol, "error": fund_data["error"], "grade": "N/A"}

    result = {"symbol": symbol}

    # --- Valuation ---
    pe_trailing = fund_data.get("pe_trailing")
    pe_forward = fund_data.get("pe_forward")
    sector = fund_data.get("sector", "Unknown")
    sector_median = SECTOR_PE_MEDIANS.get(sector, 20)

    result["pe_trailing"] = _round(pe_trailing)
    result["pe_forward"] = _round(pe_forward)
    result["sector_pe_median"] = sector_median
    result["pe_vs_sector"] = _pe_vs_sector(pe_forward or pe_trailing, sector_median)
    result["peg_ratio"] = _round(fund_data.get("peg_ratio"))
    result["price_to_book"] = _round(fund_data.get("price_to_book"))

    # --- Profitability ---
    result["profit_margin"] = _to_pct(fund_data.get("profit_margin"))
    result["operating_margin"] = _to_pct(fund_data.get("operating_margin"))
    result["gross_margin"] = _to_pct(fund_data.get("gross_margin"))
    result["roe"] = _to_pct(fund_data.get("roe"))
    result["roa"] = _to_pct(fund_data.get("roa"))

    # --- Growth ---
    result["earnings_growth"] = _to_pct(fund_data.get("earnings_growth"))
    result["revenue_growth"] = _to_pct(fund_data.get("revenue_growth"))
    result["eps_trailing"] = _round(fund_data.get("eps_trailing"))
    result["eps_forward"] = _round(fund_data.get("eps_forward"))
    eps_t = fund_data.get("eps_trailing")
    eps_f = fund_data.get("eps_forward")
    if eps_t and eps_f and eps_t > 0:
        result["eps_growth_expected"] = round(((eps_f - eps_t) / abs(eps_t)) * 100, 2)
    else:
        result["eps_growth_expected"] = None

    # --- Financial Health ---
    result["debt_to_equity"] = _round(fund_data.get("debt_to_equity"))
    result["current_ratio"] = _round(fund_data.get("current_ratio"))
    result["quick_ratio"] = _round(fund_data.get("quick_ratio"))
    fcf = fund_data.get("free_cash_flow")
    result["free_cash_flow"] = fcf
    result["fcf_positive"] = fcf is not None and fcf > 0

    # --- Dividend ---
    div_yield = fund_data.get("dividend_yield")
    result["dividend_yield"] = round(div_yield * 100, 2) if div_yield else None

    # --- Market Cap ---
    mc = fund_data.get("market_cap")
    result["market_cap"] = mc
    result["market_cap_label"] = _market_cap_label(mc)

    # --- Analyst Targets ---
    result["target_mean"] = fund_data.get("target_mean_price")
    result["target_low"] = fund_data.get("target_low_price")
    result["target_high"] = fund_data.get("target_high_price")
    result["recommendation"] = fund_data.get("recommendation_key")

    current = fund_data.get("current_price")
    target_mean = fund_data.get("target_mean_price")
    if current and target_mean and current > 0:
        result["upside_to_target"] = round(((target_mean - current) / current) * 100, 2)
    else:
        result["upside_to_target"] = None

    # --- Overall fundamental quality grade ---
    result["quality_grade"] = _quality_grade(result)

    return result


def _pe_vs_sector(pe: float | None, sector_median: float) -> str:
    if pe is None:
        return "unknown"
    if pe < 0:
        return "negative_earnings"
    if pe < sector_median * 0.7:
        return "undervalued"
    if pe < sector_median:
        return "fair"
    if pe < sector_median * 1.3:
        return "fair_high"
    return "overvalued"


def _quality_grade(data: dict) -> str:
    """Grade fundamental quality: A/B/C/D/F."""
    score = 0

    # Positive earnings growth
    eg = data.get("earnings_growth")
    if eg is not None and eg > 0:
        score += 2
    elif eg is not None and eg < -20:
        score -= 2

    # Revenue growth
    rg = data.get("revenue_growth")
    if rg is not None and rg > 5:
        score += 1
    elif rg is not None and rg < -5:
        score -= 1

    # Profit margin
    pm = data.get("profit_margin")
    if pm is not None and pm > 10:
        score += 1
    elif pm is not None and pm < 0:
        score -= 2

    # Debt/Equity
    de = data.get("debt_to_equity")
    if de is not None and de < 100:
        score += 1
    elif de is not None and de > 200:
        score -= 1

    # FCF
    if data.get("fcf_positive"):
        score += 1
    else:
        score -= 1

    # ROE
    roe = data.get("roe")
    if roe is not None and roe > 15:
        score += 1

    # P/E
    vs = data.get("pe_vs_sector", "")
    if vs == "undervalued":
        score += 1
    elif vs == "overvalued":
        score -= 1

    if score >= 5:
        return "A"
    if score >= 3:
        return "B"
    if score >= 1:
        return "C"
    if score >= -1:
        return "D"
    return "F"


def _round(val, digits=2):
    if val is None:
        return None
    try:
        return round(float(val), digits)
    except (ValueError, TypeError):
        return None


def _to_pct(val):
    if val is None:
        return None
    try:
        return round(float(val) * 100, 2)
    except (ValueError, TypeError):
        return None


def _market_cap_label(mc) -> str:
    if mc is None:
        return "unknown"
    if mc >= 200e9:
        return "mega_cap"
    if mc >= 10e9:
        return "large_cap"
    if mc >= 2e9:
        return "mid_cap"
    return "small_cap"
