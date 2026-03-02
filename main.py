"""
S&P 500 Bounce Probability Analyzer Bot
Entry point: Telegram bot + APScheduler
"""
import asyncio
import json
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import (
    SCHEDULE_DAYS, SCHEDULE_HOUR, SCHEDULE_MINUTE,
    MAX_PRICE, MIN_DRAWDOWN, PRE_FILTER_RSI,
    MIN_COMPOSITE_SCORE, TOP_PICKS_COUNT, logger,
)
from storage.database import init_db, save_report
from data.sp500_list import fetch_sp500_tickers
from data.price_fetcher import quick_prefilter, fetch_fundamentals, fetch_single_history
from data.market_context import fetch_market_context
from analysis.technical import full_technical_analysis
from analysis.fundamental import analyze_fundamentals
from analysis.sentiment import analyze_sentiment
from scoring.scorer import compute_composite_score
from llm.analyst import generate_analysis, generate_single_stock_analysis
from bot.telegram_bot import (
    create_bot_application, set_analysis_callbacks,
    send_scheduled_report,
)


def run_full_analysis() -> dict | None:
    """
    Full pipeline: fetch data → pre-filter → deep analysis → score → LLM → save.
    Returns dict with market_context, stocks, llm_response.
    """
    logger.info("=" * 60)
    logger.info("STARTING FULL S&P 500 ANALYSIS")
    logger.info("=" * 60)

    # Step 1: Market context
    logger.info("Step 1: Fetching market context...")
    market_ctx = fetch_market_context()
    logger.info(f"Market regime: {market_ctx.get('regime', '?')}")

    # Step 2: Get S&P 500 list and pre-filter
    logger.info("Step 2: Fetching S&P 500 list and pre-filtering...")
    tickers = fetch_sp500_tickers()
    if not tickers:
        logger.error("No tickers fetched")
        return None

    candidates = quick_prefilter(tickers, MAX_PRICE, MIN_DRAWDOWN, PRE_FILTER_RSI)
    if not candidates:
        logger.warning("No candidates passed pre-filter")
        return {"market_context": market_ctx, "stocks": [], "llm_response": "Нет кандидатов."}

    logger.info(f"Step 2 complete: {len(candidates)} candidates")

    # Step 3: Deep analysis for each candidate
    logger.info(f"Step 3: Deep analysis for {len(candidates)} candidates...")
    analyzed_stocks = []

    for i, cand in enumerate(candidates):
        symbol = cand["symbol"]
        logger.info(f"  Analyzing {i+1}/{len(candidates)}: {symbol}")

        try:
            # Technical analysis (already have price data)
            df = cand.get("df")
            if df is None or df.empty:
                continue
            tech = full_technical_analysis(df, symbol)
            if tech.get("error"):
                continue

            # Fundamental analysis
            fund_raw = fetch_fundamentals(symbol)
            fund = analyze_fundamentals(fund_raw)

            # Sentiment analysis (Finnhub — rate limited)
            sent = analyze_sentiment(symbol)

            # Composite score
            scores = compute_composite_score(tech, fund, sent, market_ctx)

            analyzed_stocks.append({
                "technical": tech,
                "fundamental": fund,
                "sentiment": sent,
                "scores": scores,
            })

        except Exception as e:
            logger.error(f"  Error analyzing {symbol}: {e}")
            continue

    if not analyzed_stocks:
        logger.warning("No stocks survived deep analysis")
        return {"market_context": market_ctx, "stocks": [], "llm_response": "Ни одна акция не прошла глубокий анализ."}

    # Step 4: Filter by composite score and rank
    logger.info("Step 4: Filtering and ranking...")
    analyzed_stocks = [
        s for s in analyzed_stocks
        if s["scores"]["composite_score"] >= MIN_COMPOSITE_SCORE
    ]
    analyzed_stocks.sort(key=lambda x: x["scores"]["composite_score"], reverse=True)
    top_stocks = analyzed_stocks[:TOP_PICKS_COUNT]

    logger.info(f"Step 4 complete: {len(top_stocks)} top picks (from {len(analyzed_stocks)} passing threshold)")

    if not top_stocks:
        return {
            "market_context": market_ctx,
            "stocks": [],
            "llm_response": f"Нет акций с score >= {MIN_COMPOSITE_SCORE}.",
        }

    # Step 5: GPT-4o analysis
    logger.info("Step 5: Generating GPT-4o analysis...")
    llm_response = generate_analysis(top_stocks, market_ctx)

    # Step 6: Save to database
    logger.info("Step 6: Saving report...")
    today = datetime.now().strftime("%Y-%m-%d")
    # Clean stock data for storage (remove DataFrame objects)
    stocks_for_storage = []
    for s in top_stocks:
        clean = {
            "technical": {k: v for k, v in s["technical"].items() if k != "df"},
            "fundamental": s["fundamental"],
            "sentiment": s["sentiment"],
            "scores": s["scores"],
        }
        stocks_for_storage.append(clean)

    save_report(
        date=today,
        market_regime=market_ctx.get("regime", "unknown"),
        stocks=stocks_for_storage,
        llm_response=llm_response,
        scores=[s["scores"] for s in top_stocks],
    )

    logger.info("=" * 60)
    logger.info(f"ANALYSIS COMPLETE: {len(top_stocks)} stocks found")
    logger.info("=" * 60)

    return {
        "market_context": market_ctx,
        "stocks": top_stocks,
        "llm_response": llm_response,
    }


def run_single_analysis(ticker: str) -> dict | None:
    """Analyze a single stock (/analyze command)."""
    logger.info(f"Single stock analysis: {ticker}")

    try:
        # Market context
        market_ctx = fetch_market_context()

        # Price data
        df = fetch_single_history(ticker, period="6mo")
        if df is None:
            return {"error": f"Нет данных для {ticker}"}

        # Technical
        tech = full_technical_analysis(df, ticker)
        if tech.get("error"):
            return {"error": tech["error"]}

        # Fundamental
        fund_raw = fetch_fundamentals(ticker)
        fund = analyze_fundamentals(fund_raw)

        # Sentiment
        sent = analyze_sentiment(ticker)

        # Score
        scores = compute_composite_score(tech, fund, sent, market_ctx)

        stock_data = {
            "technical": tech,
            "fundamental": fund,
            "sentiment": sent,
            "scores": scores,
        }

        # LLM
        llm_response = generate_single_stock_analysis(stock_data, market_ctx)

        return {
            "stock_data": stock_data,
            "market_context": market_ctx,
            "llm_response": llm_response,
        }

    except Exception as e:
        logger.error(f"Single analysis failed for {ticker}: {e}", exc_info=True)
        return {"error": str(e)}


# Import here to avoid circular imports
from data.price_fetcher import fetch_single_history


async def main():
    """Start the bot with scheduler."""
    logger.info("Initializing S&P 500 Bounce Analyzer Bot...")

    # Init database
    init_db()

    # Set analysis callbacks for telegram handlers
    set_analysis_callbacks(run_full_analysis, run_single_analysis)

    # Create bot
    app = create_bot_application()

    # Setup scheduler
    scheduler = AsyncIOScheduler()
    days_map = {"mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu", "fri": "fri", "sat": "sat", "sun": "sun"}
    day_of_week = ",".join(days_map.get(d.strip().lower(), d.strip()) for d in SCHEDULE_DAYS.split(","))

    async def scheduled_job():
        bot = app.bot
        await send_scheduled_report(bot)

    scheduler.add_job(
        scheduled_job,
        CronTrigger(day_of_week=day_of_week, hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE),
        id="sp500_report",
        name="S&P 500 Auto Report",
    )
    scheduler.start()
    logger.info(f"Scheduler started: {day_of_week} at {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")

    # Start polling
    logger.info("Starting Telegram bot polling...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    logger.info("Bot is running. Press Ctrl+C to stop.")

    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    finally:
        scheduler.shutdown()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
