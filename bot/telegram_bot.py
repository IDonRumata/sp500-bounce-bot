import asyncio
import json
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, logger
from bot.formatters import (
    format_market_overview, format_stocks_table, format_ai_analysis,
    format_single_stock, format_watchlist, format_help, format_status,
)
from storage.database import (
    get_last_report, save_report, get_watchlist,
    add_to_watchlist, remove_from_watchlist,
)

# Will be set by main.py
run_full_analysis = None
run_single_analysis = None
bot_start_time = datetime.now()


def set_analysis_callbacks(full_fn, single_fn):
    global run_full_analysis, run_single_analysis
    run_full_analysis = full_fn
    run_single_analysis = single_fn


async def _safe_send(context_or_bot, chat_id: str, text: str, parse_mode=ParseMode.MARKDOWN):
    """Send message, splitting if > 4096 chars."""
    max_len = 4000  # leave margin

    # Try to get bot from context or use directly
    bot = getattr(context_or_bot, 'bot', context_or_bot)

    if len(text) <= max_len:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, disable_web_page_preview=True)
        except Exception as e:
            # Fallback: send without Markdown if formatting fails
            logger.warning(f"Markdown send failed, retrying plain: {e}")
            await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
        return

    # Split by double newlines
    parts = text.split("\n\n")
    chunk = ""
    for part in parts:
        if len(chunk) + len(part) + 2 > max_len:
            if chunk:
                try:
                    await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode, disable_web_page_preview=True)
                except Exception:
                    await bot.send_message(chat_id=chat_id, text=chunk, disable_web_page_preview=True)
                await asyncio.sleep(0.5)
            chunk = part
        else:
            chunk = chunk + "\n\n" + part if chunk else part
    if chunk:
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode, disable_web_page_preview=True)
        except Exception:
            await bot.send_message(chat_id=chat_id, text=chunk, disable_web_page_preview=True)


# ---- Command Handlers ----

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _safe_send(context, update.effective_chat.id, format_help())


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = str(datetime.now() - bot_start_time).split(".")[0]
    report = get_last_report()
    last_run = report["date"] if report else None
    await _safe_send(context, update.effective_chat.id, format_status(last_run, None, uptime))


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    report = get_last_report()
    if not report:
        await _safe_send(context, chat_id, "📭 Отчётов пока нет. Запустите `/run`.")
        return

    msg = f"📊 *Последний отчёт* — {report['date']}\n"
    msg += f"🌍 Режим рынка: *{report.get('market_regime', '?')}*\n\n"

    stocks = report.get("stocks", [])
    if stocks:
        for i, s in enumerate(stocks[:7], 1):
            sym = s.get("technical", {}).get("symbol", "?")
            score = s.get("scores", {}).get("composite_score", "?")
            price = s.get("technical", {}).get("current_price", "?")
            msg += f"{i}. *{sym}* — ${price} | Score: {score}\n"
    msg += f"\n🧠 AI-анализ сохранён.\n"
    msg += "⚠️ _Не является инвестиционной рекомендацией_"

    await _safe_send(context, chat_id, msg)

    # Also send the AI analysis if available
    llm = report.get("llm_response")
    if llm:
        await _safe_send(context, chat_id, format_ai_analysis(llm))


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await _safe_send(context, chat_id, "🔄 *Запуск полного анализа S&P 500*\n\nЭто займёт 5-10 минут...")

    if not run_full_analysis:
        await _safe_send(context, chat_id, "❌ Анализ не настроен")
        return

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, run_full_analysis)

        if not result or not result.get("stocks"):
            await _safe_send(context, chat_id, "⚠️ *Акции не найдены*\n\nПо текущим критериям нет подходящих кандидатов.")
            return

        # Send 3 messages: market overview, stocks table, AI analysis
        await _safe_send(context, chat_id, format_market_overview(result["market_context"]))
        await asyncio.sleep(1)
        await _safe_send(context, chat_id, format_stocks_table(result["stocks"]))
        await asyncio.sleep(1)
        await _safe_send(context, chat_id, format_ai_analysis(result["llm_response"]))

    except Exception as e:
        logger.error(f"Run command failed: {e}", exc_info=True)
        await _safe_send(context, chat_id, f"❌ *Ошибка:* {str(e)[:200]}")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        await _safe_send(context, chat_id, "❌ Укажите тикер: `/analyze AAPL`")
        return

    ticker = args[0].upper()
    await _safe_send(context, chat_id, f"🔍 *Анализирую {ticker}...*\n\nЗаймёт 1-2 минуты.")

    if not run_single_analysis:
        await _safe_send(context, chat_id, "❌ Анализ не настроен")
        return

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, run_single_analysis, ticker)

        if not result or result.get("error"):
            await _safe_send(context, chat_id, f"❌ Не удалось проанализировать {ticker}: {result.get('error', 'Unknown error')}")
            return

        messages = format_single_stock(result["stock_data"], result["llm_response"])
        for msg in messages:
            await _safe_send(context, chat_id, msg)
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"Analyze command failed for {ticker}: {e}", exc_info=True)
        await _safe_send(context, chat_id, f"❌ *Ошибка анализа {ticker}:* {str(e)[:200]}")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args

    if not args:
        symbols = get_watchlist()
        await _safe_send(context, chat_id, format_watchlist(symbols))
        return

    action = args[0].lower()
    if action == "add" and len(args) > 1:
        ticker = args[1].upper()
        if add_to_watchlist(ticker):
            await _safe_send(context, chat_id, f"✅ *{ticker}* добавлен в watchlist")
        else:
            await _safe_send(context, chat_id, f"ℹ️ *{ticker}* уже в watchlist")
    elif action == "remove" and len(args) > 1:
        ticker = args[1].upper()
        if remove_from_watchlist(ticker):
            await _safe_send(context, chat_id, f"🗑 *{ticker}* удалён из watchlist")
        else:
            await _safe_send(context, chat_id, f"ℹ️ *{ticker}* не найден в watchlist")
    else:
        await _safe_send(context, chat_id, "Используйте:\n`/watchlist` — показать\n`/watchlist add AAPL`\n`/watchlist remove AAPL`")


async def send_scheduled_report(bot):
    """Called by scheduler to send auto-report."""
    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        logger.error("TELEGRAM_CHAT_ID not set, cannot send scheduled report")
        return

    await _safe_send(bot, chat_id, "🔄 *Автоматический анализ S&P 500...*")

    if not run_full_analysis:
        await _safe_send(bot, chat_id, "❌ Анализ не настроен")
        return

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_full_analysis)

        if not result or not result.get("stocks"):
            await _safe_send(bot, chat_id, "⚠️ По текущим критериям нет подходящих кандидатов.")
            return

        await _safe_send(bot, chat_id, format_market_overview(result["market_context"]))
        await asyncio.sleep(1)
        await _safe_send(bot, chat_id, format_stocks_table(result["stocks"]))
        await asyncio.sleep(1)
        await _safe_send(bot, chat_id, format_ai_analysis(result["llm_response"]))

    except Exception as e:
        logger.error(f"Scheduled report failed: {e}", exc_info=True)
        await _safe_send(bot, chat_id, f"❌ *Ошибка авто-отчёта:* {str(e)[:200]}")


def create_bot_application() -> Application:
    """Create and configure the Telegram bot application."""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))

    logger.info("Telegram bot application created")
    return app
