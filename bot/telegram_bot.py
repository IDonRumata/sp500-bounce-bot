import re
import asyncio
from datetime import datetime
from telegram import Update, BotCommand, MenuButtonCommands
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, AUTHORIZED_CHAT_IDS,
    CMD_COOLDOWN_SEC, logger,
)
from bot.formatters import (
    format_market_overview, format_stocks_table, format_ai_analysis,
    format_single_stock, format_watchlist, format_help, format_status,
    format_stats, format_check_results, format_settings, format_admin_users,
    format_alerts,
)
from storage.database import (
    get_last_report, get_watchlist,
    add_to_watchlist, remove_from_watchlist,
    get_stats_summary,
    register_user, get_user, get_all_active_users,
    get_subscribed_users, update_user_setting,
)
from evaluation.check_results import check_pending_results

# Will be set by main.py
run_full_analysis = None
run_single_analysis = None
bot_start_time = datetime.now()

# Rate limiting for /run
_last_run_time: dict[str, datetime] = {}

# Ticker validation pattern (1-5 uppercase letters, optionally with dash)
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def set_analysis_callbacks(full_fn, single_fn):
    global run_full_analysis, run_single_analysis
    run_full_analysis = full_fn
    run_single_analysis = single_fn


def _is_authorized(update: Update) -> bool:
    """Check if user is authorized. Auto-registers on first contact."""
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    # Whitelist gate: if AUTHORIZED_CHAT_IDS is set, only those can register
    if AUTHORIZED_CHAT_IDS and chat_id not in AUTHORIZED_CHAT_IDS:
        return False

    # Auto-register / update activity
    register_user(
        chat_id,
        username=user.username if user else None,
        first_name=user.first_name if user else None,
    )

    # Check active flag in DB
    db_user = get_user(chat_id)
    if db_user and not db_user["is_active"]:
        return False

    return True


def _is_admin(update: Update) -> bool:
    """Check if user has admin privileges."""
    chat_id = str(update.effective_chat.id)
    db_user = get_user(chat_id)
    return bool(db_user and db_user["is_admin"])


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
    if not _is_authorized(update):
        return
    await _safe_send(context, update.effective_chat.id, format_help())


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    uptime = str(datetime.now() - bot_start_time).split(".")[0]
    report = get_last_report()
    last_run = report["date"] if report else None
    await _safe_send(context, update.effective_chat.id, format_status(last_run, None, uptime))


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    chat_id = update.effective_chat.id
    report = get_last_report()
    if not report:
        await _safe_send(context, chat_id, "No reports yet. Run `/run`.")
        return

    msg = f"*Last report* — {report['date']}\n"
    msg += f"Market regime: *{report.get('market_regime', '?')}*\n\n"

    stocks = report.get("stocks", [])
    if stocks:
        for i, s in enumerate(stocks[:7], 1):
            sym = s.get("technical", {}).get("symbol", "?")
            score = s.get("scores", {}).get("composite_score", "?")
            price = s.get("technical", {}).get("current_price", "?")
            msg += f"{i}. *{sym}* — ${price} | Score: {score}\n"
    msg += "\n_Not financial advice_"

    await _safe_send(context, chat_id, msg)

    # Also send the AI analysis if available
    llm = report.get("llm_response")
    if llm:
        await _safe_send(context, chat_id, format_ai_analysis(llm))


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    chat_id = update.effective_chat.id

    # Rate limiting
    now = datetime.now()
    last = _last_run_time.get(str(chat_id))
    if last and (now - last).total_seconds() < CMD_COOLDOWN_SEC:
        remaining = int(CMD_COOLDOWN_SEC - (now - last).total_seconds())
        await _safe_send(context, chat_id, f"⏳ Подождите {remaining} сек. перед повторным запуском.")
        return
    _last_run_time[str(chat_id)] = now

    await _safe_send(context, chat_id, "🔄 *Запуск полного анализа S&P 500*\n\nЭто займёт 5-10 минут...")

    if not run_full_analysis:
        await _safe_send(context, chat_id, "Analysis not configured")
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
        await _safe_send(context, chat_id, "❌ *Ошибка анализа.* Повторите позже.")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        await _safe_send(context, chat_id, "Укажите тикер: `/analyze AAPL`")
        return

    ticker = args[0].upper()

    # Validate ticker format (security: prevent injection)
    if not _TICKER_RE.match(ticker):
        await _safe_send(context, chat_id, "❌ Неверный формат тикера. Пример: `/analyze AAPL`")
        return

    await _safe_send(context, chat_id, f"🔍 *Анализирую {ticker}...*\n\nЗаймёт 1-2 минуты.")

    if not run_single_analysis:
        await _safe_send(context, chat_id, "Analysis not configured")
        return

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, run_single_analysis, ticker)

        if not result or result.get("error"):
            await _safe_send(context, chat_id, f"❌ Не удалось проанализировать {ticker}")
            return

        messages = format_single_stock(result["stock_data"], result["llm_response"])
        for msg in messages:
            await _safe_send(context, chat_id, msg)
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"Analyze command failed for {ticker}: {e}", exc_info=True)
        await _safe_send(context, chat_id, f"❌ *Ошибка анализа {ticker}.* Повторите позже.")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    chat_id = update.effective_chat.id
    user_id = str(chat_id)
    args = context.args

    if not args:
        symbols = get_watchlist(user_id)
        await _safe_send(context, chat_id, format_watchlist(symbols))
        return

    action = args[0].lower()
    if action == "add" and len(args) > 1:
        ticker = args[1].upper()
        if not _TICKER_RE.match(ticker):
            await _safe_send(context, chat_id, "❌ Неверный формат тикера")
            return
        if add_to_watchlist(ticker, user_id):
            await _safe_send(context, chat_id, f"✅ *{ticker}* добавлен в watchlist")
        else:
            await _safe_send(context, chat_id, f"ℹ️ *{ticker}* уже в watchlist")
    elif action == "remove" and len(args) > 1:
        ticker = args[1].upper()
        if not _TICKER_RE.match(ticker):
            await _safe_send(context, chat_id, "❌ Неверный формат тикера")
            return
        if remove_from_watchlist(ticker, user_id):
            await _safe_send(context, chat_id, f"🗑 *{ticker}* удалён из watchlist")
        else:
            await _safe_send(context, chat_id, f"ℹ️ *{ticker}* не найден в watchlist")
    else:
        await _safe_send(context, chat_id, "Используйте:\n`/watchlist` — показать\n`/watchlist add AAPL`\n`/watchlist remove AAPL`")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    chat_id = update.effective_chat.id
    try:
        stats = get_stats_summary()
        await _safe_send(context, chat_id, format_stats(stats))
    except Exception as e:
        logger.error(f"Stats command failed: {e}", exc_info=True)
        await _safe_send(context, chat_id, "❌ Ошибка получения статистики.")


# ---- Subscribe / Settings / Admin ----

async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    chat_id = str(update.effective_chat.id)
    update_user_setting(chat_id, "subscribed_reports", 1)
    await _safe_send(context, update.effective_chat.id, "✅ Вы подписаны на авто-отчёты.")


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    chat_id = str(update.effective_chat.id)
    update_user_setting(chat_id, "subscribed_reports", 0)
    await _safe_send(context, update.effective_chat.id, "🔕 Вы отписаны от авто-отчётов. Команды по-прежнему доступны.")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    chat_id = str(update.effective_chat.id)
    user = get_user(chat_id)
    if not user:
        await _safe_send(context, update.effective_chat.id, "Пользователь не найден.")
        return
    await _safe_send(context, update.effective_chat.id, format_settings(user))


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    if not _is_admin(update):
        await _safe_send(context, update.effective_chat.id, "⛔ Нет прав администратора.")
        return

    chat_id = update.effective_chat.id
    args = context.args

    if not args:
        await _safe_send(context, chat_id,
            "*Команды администратора:*\n"
            "`/admin users` — список пользователей\n"
            "`/admin broadcast MSG` — рассылка всем\n"
            "`/admin ban CHAT_ID` — заблокировать\n"
            "`/admin unban CHAT_ID` — разблокировать\n"
            "`/admin promote CHAT_ID` — сделать админом"
        )
        return

    action = args[0].lower()

    if action == "users":
        users = get_all_active_users()
        await _safe_send(context, chat_id, format_admin_users(users))

    elif action == "broadcast" and len(args) > 1:
        message = " ".join(args[1:])
        subscribers = get_subscribed_users()
        sent = 0
        for cid in subscribers:
            try:
                await _safe_send(context.bot, cid, f"📢 *Объявление:*\n\n{message}")
                sent += 1
            except Exception as e:
                logger.warning(f"Broadcast failed for {cid}: {e}")
        await _safe_send(context, chat_id, f"✅ Отправлено {sent}/{len(subscribers)} пользователям.")

    elif action == "ban" and len(args) > 1:
        target_id = args[1]
        if update_user_setting(target_id, "is_active", 0):
            await _safe_send(context, chat_id, f"🚫 Пользователь {target_id} заблокирован.")
        else:
            await _safe_send(context, chat_id, f"Пользователь {target_id} не найден.")

    elif action == "unban" and len(args) > 1:
        target_id = args[1]
        if update_user_setting(target_id, "is_active", 1):
            await _safe_send(context, chat_id, f"✅ Пользователь {target_id} разблокирован.")
        else:
            await _safe_send(context, chat_id, f"Пользователь {target_id} не найден.")

    elif action == "promote" and len(args) > 1:
        target_id = args[1]
        if update_user_setting(target_id, "is_admin", 1):
            await _safe_send(context, chat_id, f"👑 Пользователь {target_id} стал админом.")
        else:
            await _safe_send(context, chat_id, f"Пользователь {target_id} не найден.")

    else:
        await _safe_send(context, chat_id, "Неизвестная команда. `/admin` для справки.")


# ---- Scheduled Report ----

async def scheduled_report_job(context: ContextTypes.DEFAULT_TYPE):
    """Job callback for python-telegram-bot's job_queue (replaces APScheduler)."""
    logger.info("Scheduled report triggered by job_queue")
    await send_scheduled_report(context.bot)


async def send_scheduled_report(bot):
    """Send automated report to all subscribed users."""
    subscribers = get_subscribed_users()
    # Fallback to TELEGRAM_CHAT_ID if no subscribers yet
    if not subscribers and TELEGRAM_CHAT_ID:
        subscribers = [TELEGRAM_CHAT_ID]
    if not subscribers:
        logger.error("No subscribers and TELEGRAM_CHAT_ID not set")
        return

    if not run_full_analysis:
        for cid in subscribers:
            await _safe_send(bot, cid, "Analysis not configured")
        return

    # Notify subscribers that analysis started
    for cid in subscribers:
        await _safe_send(bot, cid, "🔄 *Автоматический анализ S&P 500...*")

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_full_analysis)

        if not result or not result.get("stocks"):
            for cid in subscribers:
                await _safe_send(bot, cid, "⚠️ По текущим критериям нет подходящих кандидатов.")
            return

        # Pre-format messages once, send to all
        msg_market = format_market_overview(result["market_context"])
        msg_stocks = format_stocks_table(result["stocks"])
        msg_ai = format_ai_analysis(result["llm_response"])

        for cid in subscribers:
            try:
                await _safe_send(bot, cid, msg_market)
                await asyncio.sleep(0.5)
                await _safe_send(bot, cid, msg_stocks)
                await asyncio.sleep(0.5)
                await _safe_send(bot, cid, msg_ai)
            except Exception as e:
                logger.warning(f"Failed to send report to {cid}: {e}")

    except Exception as e:
        logger.error(f"Scheduled report failed: {e}", exc_info=True)
        for cid in subscribers:
            await _safe_send(bot, cid, "❌ *Ошибка авто-отчёта.* Следующая попытка по расписанию.")


async def check_results_job(context: ContextTypes.DEFAULT_TYPE):
    """Job callback: check pending recommendation results after market close."""
    logger.info("Check results job triggered")

    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, check_pending_results)
        if results:
            msg = format_check_results(results)
            subscribers = get_subscribed_users()
            if not subscribers and TELEGRAM_CHAT_ID:
                subscribers = [TELEGRAM_CHAT_ID]
            for cid in subscribers:
                try:
                    await _safe_send(context.bot, cid, msg)
                except Exception as e:
                    logger.warning(f"Failed to send check results to {cid}: {e}")
        else:
            logger.info("No pending results to report")
    except Exception as e:
        logger.error(f"Check results job failed: {e}", exc_info=True)


async def weekly_stats_job(context: ContextTypes.DEFAULT_TYPE):
    """Job callback: send weekly performance stats."""
    logger.info("Weekly stats job triggered")

    try:
        stats = get_stats_summary()
        msg = "📅 *Еженедельная статистика*\n\n" + format_stats(stats)
        subscribers = get_subscribed_users()
        if not subscribers and TELEGRAM_CHAT_ID:
            subscribers = [TELEGRAM_CHAT_ID]
        for cid in subscribers:
            try:
                await _safe_send(context.bot, cid, msg)
            except Exception as e:
                logger.warning(f"Failed to send weekly stats to {cid}: {e}")
    except Exception as e:
        logger.error(f"Weekly stats job failed: {e}", exc_info=True)


async def watchlist_alert_job(context: ContextTypes.DEFAULT_TYPE):
    """Job callback: check watchlist tickers and send alerts."""
    from alerts.watchlist_monitor import check_watchlist_alerts

    try:
        loop = asyncio.get_event_loop()
        alerts_by_user = await loop.run_in_executor(None, check_watchlist_alerts)
        if not alerts_by_user:
            return
        for uid, alerts in alerts_by_user.items():
            try:
                msg = format_alerts(alerts)
                if msg:
                    await _safe_send(context.bot, uid, msg)
            except Exception as e:
                logger.warning(f"Failed to send alerts to {uid}: {e}")
        logger.info(f"Sent alerts to {len(alerts_by_user)} users")
    except Exception as e:
        logger.error(f"Watchlist alert job failed: {e}", exc_info=True)


# ---- Bot Setup ----

def create_bot_application() -> Application:
    """Create and configure the Telegram bot application."""
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("admin", cmd_admin))

    logger.info("Telegram bot application created")
    return app
