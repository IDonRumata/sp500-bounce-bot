"""
S&P 500 Bounce Probability Analyzer Bot
Entry point: Telegram bot + built-in job_queue scheduler
"""
import asyncio
import json
from datetime import datetime, time as dtime

from config import (
    SCHEDULE_DAYS, SCHEDULE_HOUR, SCHEDULE_MINUTE,
    MAX_PRICE, MIN_DRAWDOWN, PRE_FILTER_RSI,
    MIN_COMPOSITE_SCORE, TOP_PICKS_COUNT,
    WEEKLY_REPORT_DAY, WEEKLY_REPORT_HOUR, logger,
)
from storage.database import init_db, save_report, save_recommendations, save_market_snapshot, set_first_admin, register_user
from data.sp500_list import fetch_sp500_tickers
from data.price_fetcher import quick_prefilter, fetch_fundamentals, fetch_single_history
from data.market_context import fetch_market_context
from analysis.technical import full_technical_analysis
from analysis.fundamental import analyze_fundamentals
from analysis.sentiment import analyze_sentiment
from scoring.scorer import compute_composite_score
from llm.analyst import generate_analysis, generate_single_stock_analysis
from telegram import BotCommand, MenuButtonCommands
from bot.telegram_bot import (
    create_bot_application, set_analysis_callbacks,
    send_scheduled_report, scheduled_report_job,
    check_results_job, weekly_stats_job,
    watchlist_alert_job,
)
from config import TELEGRAM_CHAT_ID as _CHAT_ID, ALERT_ENABLED, ALERT_INTERVAL_MIN


# --- Market regime multiplier ---
REGIME_MULTIPLIER = {
    "bullish": 1.0,
    "neutral": 0.95,
    "weak": 0.88,
    "bearish": 0.80,
    "panic": 0.70,
}


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
    regime = market_ctx.get("regime", "neutral")
    logger.info(f"Market regime: {regime}")

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

            # Apply market regime multiplier
            multiplier = REGIME_MULTIPLIER.get(regime, 0.95)
            if multiplier < 1.0:
                original = scores["composite_score"]
                scores["composite_score"] = round(original * multiplier, 1)
                # Recalculate probability label
                cs = scores["composite_score"]
                if cs >= 75:
                    scores["bounce_probability"] = "high"
                elif cs >= 60:
                    scores["bounce_probability"] = "medium_high"
                elif cs >= 45:
                    scores["bounce_probability"] = "medium"
                elif cs >= 30:
                    scores["bounce_probability"] = "low"
                else:
                    scores["bounce_probability"] = "very_low"

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
        and not (s["fundamental"].get("quality_grade") == "F"
                 and s["scores"]["bounce_probability"] in ("low", "very_low"))
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

    # Step 5: LLM analysis
    logger.info("Step 5: Generating AI analysis...")
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

    # Step 7: Save individual recommendations for tracking
    logger.info("Step 7: Saving recommendations for tracking...")
    save_recommendations(stocks_for_storage, market_ctx, today)
    save_market_snapshot(market_ctx, today, len(candidates), len(top_stocks))

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


def _parse_schedule_days(days_str: str) -> tuple[int, ...]:
    """Convert 'mon,wed,fri' → (1, 3, 5) for job_queue.run_daily.
    python-telegram-bot uses 0=Sunday, 1=Monday, ..., 6=Saturday."""
    day_map = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
    days = []
    for d in days_str.split(","):
        d = d.strip().lower()
        if d in day_map:
            days.append(day_map[d])
    return tuple(sorted(days)) if days else (1, 3, 5)


async def main():
    """Start the bot with built-in job_queue scheduler."""
    logger.info("Initializing S&P 500 Bounce Analyzer Bot...")

    # Init database
    init_db()

    # Ensure the owner (TELEGRAM_CHAT_ID) is registered as admin
    if _CHAT_ID:
        register_user(_CHAT_ID)
        set_first_admin(_CHAT_ID)

    # Set analysis callbacks for telegram handlers
    set_analysis_callbacks(run_full_analysis, run_single_analysis)

    # Create bot (post_init sets menu button + commands)
    app = create_bot_application()

    # Start application
    await app.initialize()
    await app.start()

    # Setup menu button and commands (post_init only works with run_polling)
    try:
        await app.bot.set_my_commands([
            BotCommand("run", "Полный анализ S&P 500"),
            BotCommand("report", "Последний отчёт"),
            BotCommand("analyze", "Анализ одной акции"),
            BotCommand("stats", "Статистика рекомендаций"),
            BotCommand("watchlist", "Watchlist"),
            BotCommand("take", "Купить акции (напр. /take AAPL 5)"),
            BotCommand("sell", "Продать позицию"),
            BotCommand("portfolio", "Портфель с P&L"),
            BotCommand("subscribe", "Подписаться на отчёты"),
            BotCommand("unsubscribe", "Отписаться от отчётов"),
            BotCommand("settings", "Мои настройки"),
            BotCommand("status", "Статус бота"),
            BotCommand("help", "Справка"),
        ])
        if _CHAT_ID:
            await app.bot.set_chat_menu_button(
                chat_id=int(_CHAT_ID), menu_button=MenuButtonCommands(),
            )
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Menu button and commands configured")
    except Exception as e:
        logger.error(f"Failed to set menu/commands: {e}")

    # Setup scheduled reports using telegram's built-in job_queue
    schedule_days = _parse_schedule_days(SCHEDULE_DAYS)
    schedule_time = dtime(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, second=0)

    app.job_queue.run_daily(
        scheduled_report_job,
        time=schedule_time,
        days=schedule_days,
        name="sp500_auto_report",
    )

    day_names = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}
    schedule_str = ",".join(day_names.get(d, "?") for d in schedule_days)
    logger.info(f"Scheduler: {schedule_str} at {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} UTC")

    # Setup daily result checking (Mon-Fri 18:00 UTC, after market close)
    app.job_queue.run_daily(
        check_results_job,
        time=dtime(hour=18, minute=0, second=0),
        days=(1, 2, 3, 4, 5),
        name="check_results",
    )
    logger.info("Result checker: Mon-Fri at 18:00 UTC")

    # Setup weekly stats report
    weekly_days = _parse_schedule_days(WEEKLY_REPORT_DAY)
    app.job_queue.run_daily(
        weekly_stats_job,
        time=dtime(hour=WEEKLY_REPORT_HOUR, minute=0, second=0),
        days=weekly_days,
        name="weekly_stats",
    )
    weekly_str = ",".join(day_names.get(d, "?") for d in weekly_days)
    logger.info(f"Weekly stats: {weekly_str} at {WEEKLY_REPORT_HOUR:02d}:00 UTC")

    # Setup watchlist alerts (repeating job)
    if ALERT_ENABLED:
        app.job_queue.run_repeating(
            watchlist_alert_job,
            interval=ALERT_INTERVAL_MIN * 60,
            first=60,
            name="watchlist_alerts",
        )
        logger.info(f"Watchlist alerts: every {ALERT_INTERVAL_MIN} min (market hours only)")
    else:
        logger.info("Watchlist alerts: DISABLED")

    # Start polling (explicitly request all update types to override stale Telegram state)
    await app.updater.start_polling(allowed_updates=[])
    logger.info("Bot is running. Press Ctrl+C to stop.")

    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
