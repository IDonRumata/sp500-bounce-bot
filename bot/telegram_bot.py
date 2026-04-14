import re
import asyncio
from datetime import datetime
import telegram
from telegram import (
    Update, BotCommand, MenuButtonCommands, Bot as TgBot,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, AUTHORIZED_CHAT_IDS,
    CMD_COOLDOWN_SEC, logger,
)
from bot.formatters import (
    format_market_overview, format_stocks_table, format_ai_analysis,
    format_single_stock, format_watchlist, format_help, format_status,
    format_stats, format_check_results, format_settings, format_admin_users,
    format_alerts, format_portfolio, format_portfolio_history,
    format_backtest, format_entry_signals, format_exit_signals,
    format_performance, format_snapshot_digest,
    format_paper_dashboard, format_paper_approval_message,
)
from storage.database import (
    get_last_report, get_watchlist,
    add_to_watchlist, remove_from_watchlist,
    get_stats_summary,
    register_user, get_user, get_all_active_users,
    get_subscribed_users, update_user_setting,
    get_setting, set_setting,
    get_paper_trades_stats, get_paper_trade_by_id,
    reject_paper_trade, update_paper_trade_open, update_paper_trade_error,
)
from evaluation.check_results import check_pending_results, check_pending_30d_results, snapshot_all_recommendations

# Will be set by main.py
run_full_analysis = None
run_single_analysis = None
bot_start_time = datetime.now()

# Rate limiting for /run
_last_run_time: dict[str, datetime] = {}

# Strong references to background tasks to prevent garbage collection
_bg_tasks = set()

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

    # Extract Bot object: context.bot works, but Bot.bot returns User — avoid that
    if isinstance(context_or_bot, TgBot):
        bot = context_or_bot
    else:
        bot = context_or_bot.bot

    if len(text) <= max_len:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, disable_web_page_preview=True)
        except telegram.error.Forbidden as e:
            # User blocked the bot — log and skip (don't retry)
            logger.warning(f"User {chat_id} blocked the bot: {e}")
            return
        except Exception as e:
            # Fallback: send without Markdown if formatting fails
            logger.warning(f"Markdown send failed, retrying plain: {e}")
            try:
                await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
            except telegram.error.Forbidden:
                logger.warning(f"User {chat_id} blocked the bot (plain fallback)")
                return
            except Exception as e2:
                logger.error(f"Failed to send message to {chat_id}: {e2}")
                return
        return

    # Split by double newlines
    parts = text.split("\n\n")
    chunk = ""
    for part in parts:
        if len(chunk) + len(part) + 2 > max_len:
            if chunk:
                try:
                    await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode, disable_web_page_preview=True)
                except telegram.error.Forbidden:
                    logger.warning(f"User {chat_id} blocked the bot (split send)")
                    return
                except Exception:
                    try:
                        await bot.send_message(chat_id=chat_id, text=chunk, disable_web_page_preview=True)
                    except telegram.error.Forbidden:
                        logger.warning(f"User {chat_id} blocked the bot (split plain)")
                        return
                    except Exception as e:
                        logger.error(f"Failed to send chunk to {chat_id}: {e}")
                await asyncio.sleep(0.5)
            chunk = part
        else:
            chunk = chunk + "\n\n" + part if chunk else part
    if chunk:
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode, disable_web_page_preview=True)
        except telegram.error.Forbidden:
            logger.warning(f"User {chat_id} blocked the bot (final chunk)")
            return
        except Exception:
            try:
                await bot.send_message(chat_id=chat_id, text=chunk, disable_web_page_preview=True)
            except telegram.error.Forbidden:
                logger.warning(f"User {chat_id} blocked the bot (final plain)")
                return
            except Exception as e:
                logger.error(f"Failed to send final chunk to {chat_id}: {e}")


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


async def _run_analysis_background(bot, chat_id):
    """Run full analysis in background and send results when done."""
    try:
        result = await asyncio.get_event_loop().run_in_executor(None, run_full_analysis)

        if not result or not result.get("stocks"):
            await _safe_send(bot, chat_id, "⚠️ *Акции не найдены*\n\nПо текущим критериям нет подходящих кандидатов.")
            return

        await _safe_send(bot, chat_id, format_market_overview(result["market_context"]))
        await asyncio.sleep(1)
        await _safe_send(bot, chat_id, format_stocks_table(result["stocks"]))
        await asyncio.sleep(1)
        await _safe_send(bot, chat_id, format_ai_analysis(result["llm_response"]))

    except Exception as e:
        logger.error(f"Run command failed: {e}", exc_info=True)
        await _safe_send(bot, chat_id, "❌ *Ошибка анализа.* Повторите позже.")


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

    if not run_full_analysis:
        await _safe_send(context, chat_id, "Analysis not configured")
        return

    await _safe_send(context, chat_id, "🔄 *Запуск полного анализа S&P 500*\n\nЭто займёт 5-10 минут...\nБот продолжает работать — можете пользоваться другими командами.")

    # Fire-and-forget: analysis runs in background, bot stays responsive
    task = asyncio.create_task(_run_analysis_background(context.bot, chat_id))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


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


# ---- Portfolio Commands ----

async def cmd_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /take TICKER QTY — open a position at current price."""
    if not _is_authorized(update):
        return
    chat_id = update.effective_chat.id
    user_id = str(chat_id)
    args = context.args

    if not args or len(args) < 2:
        await _safe_send(context, chat_id, "Использование: `/take AAPL 5` (5 акций AAPL)")
        return

    ticker = args[0].upper()
    if not _TICKER_RE.match(ticker):
        await _safe_send(context, chat_id, "❌ Неверный формат тикера")
        return

    try:
        shares = float(args[1])
        if shares <= 0:
            raise ValueError
    except ValueError:
        await _safe_send(context, chat_id, "❌ Количество должно быть положительным числом")
        return

    await _safe_send(context, chat_id, f"⏳ Получаю цену *{ticker}*...")

    from portfolio.tracker import fetch_current_price, add_position

    try:
        price = await asyncio.get_event_loop().run_in_executor(None, fetch_current_price, ticker)
        if not price:
            await _safe_send(context, chat_id, f"❌ Не удалось получить цену *{ticker}*")
            return

        ok = add_position(user_id, ticker, shares, price)
        if ok:
            total = round(price * shares, 2)
            await _safe_send(context, chat_id,
                f"✅ *Позиция открыта*\n\n"
                f"*{ticker}*: {shares} шт. × ${price:.2f} = ${total:,.2f}\n\n"
                f"Смотреть: `/portfolio`"
            )
        else:
            await _safe_send(context, chat_id, "❌ Ошибка сохранения позиции")
    except Exception as e:
        logger.error(f"cmd_take failed: {e}", exc_info=True)
        await _safe_send(context, chat_id, "❌ Ошибка. Повторите позже.")


async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sell TICKER — close oldest open position at current price."""
    if not _is_authorized(update):
        return
    chat_id = update.effective_chat.id
    user_id = str(chat_id)
    args = context.args

    if not args:
        await _safe_send(context, chat_id, "Использование: `/sell AAPL`")
        return

    ticker = args[0].upper()
    if not _TICKER_RE.match(ticker):
        await _safe_send(context, chat_id, "❌ Неверный формат тикера")
        return

    await _safe_send(context, chat_id, f"⏳ Получаю цену *{ticker}*...")

    from portfolio.tracker import fetch_current_price, close_position

    try:
        price = await asyncio.get_event_loop().run_in_executor(None, fetch_current_price, ticker)
        if not price:
            await _safe_send(context, chat_id, f"❌ Не удалось получить цену *{ticker}*")
            return

        result = close_position(user_id, ticker, price)
        if not result:
            await _safe_send(context, chat_id, f"❌ Нет открытой позиции по *{ticker}*")
            return

        pnl_emoji = "📈" if result["pnl_pct"] >= 0 else "📉"
        await _safe_send(context, chat_id,
            f"✅ *Позиция закрыта*\n\n"
            f"*{ticker}*: {result['shares']} шт.\n"
            f"Покупка: ${result['buy_price']:.2f} → Продажа: ${price:.2f}\n"
            f"{pnl_emoji} P&L: *{result['pnl_pct']:+.2f}%* (${result['pnl_abs']:+.2f})\n\n"
            f"История: `/portfolio history`"
        )
    except Exception as e:
        logger.error(f"cmd_sell failed: {e}", exc_info=True)
        await _safe_send(context, chat_id, "❌ Ошибка. Повторите позже.")


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /portfolio [history] — show positions or history."""
    if not _is_authorized(update):
        return
    chat_id = update.effective_chat.id
    user_id = str(chat_id)
    args = context.args

    if args and args[0].lower() == "history":
        from storage.database import get_closed_positions
        try:
            trades = await asyncio.get_event_loop().run_in_executor(
                None, get_closed_positions, user_id,
            )
            await _safe_send(context, chat_id, format_portfolio_history(trades))
        except Exception as e:
            logger.error(f"cmd_portfolio history failed: {e}", exc_info=True)
            await _safe_send(context, chat_id, "❌ Ошибка загрузки истории.")
        return

    # Show open positions with live prices
    from storage.database import get_open_positions
    from portfolio.tracker import refresh_prices, get_portfolio_summary

    await _safe_send(context, chat_id, "⏳ Загружаю портфель...")

    try:
        positions = await asyncio.get_event_loop().run_in_executor(
            None, get_open_positions, user_id,
        )
        if not positions:
            await _safe_send(context, chat_id, format_portfolio([], {}))
            return

        enriched = await asyncio.get_event_loop().run_in_executor(
            None, refresh_prices, positions,
        )
        summary = get_portfolio_summary(enriched)
        await _safe_send(context, chat_id, format_portfolio(enriched, summary))
    except Exception as e:
        logger.error(f"cmd_portfolio failed: {e}", exc_info=True)
        await _safe_send(context, chat_id, "❌ Ошибка загрузки портфеля.")


async def cmd_performance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /performance — show theoretical vs actual portfolio returns."""
    if not _is_authorized(update):
        return
    chat_id = update.effective_chat.id
    user_id = str(chat_id)

    await _safe_send(context, chat_id, "⏳ Считаю доходность...")

    try:
        from storage.database import get_performance_stats
        stats = await asyncio.get_event_loop().run_in_executor(
            None, get_performance_stats, user_id,
        )
        await _safe_send(context, chat_id, format_performance(stats))
    except Exception as e:
        logger.error(f"cmd_performance failed: {e}", exc_info=True)
        await _safe_send(context, chat_id, "❌ Ошибка подсчёта доходности.")


# ---- Chart Command ----

async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /chart — send statistics PNG chart."""
    if not _is_authorized(update):
        return
    chat_id = update.effective_chat.id

    await _safe_send(context, chat_id, "📊 Генерирую графики...")

    try:
        from storage.database import get_stats_summary
        from charts.generator import generate_stats_chart

        stats = await asyncio.get_event_loop().run_in_executor(None, get_stats_summary)
        total_checked = stats.get("total_checked", 0)
        if total_checked == 0:
            await _safe_send(context, chat_id, "📊 Нет проверенных рекомендаций для графиков.")
            return

        png_bytes = await asyncio.get_event_loop().run_in_executor(
            None, generate_stats_chart, stats
        )
        if png_bytes is None:
            await _safe_send(context, chat_id, "❌ Не удалось сгенерировать график.")
            return

        caption = (
            f"📊 Статистика бота — {total_checked} проверенных рекомендаций\n"
            f"Win rate: {stats.get('win_rate_pct', 0)}% (>0%) | "
            f"Успех: {stats.get('success_pct', 0)}% (>+3%) | "
            f"Avg: {stats.get('avg_result_pct', 0):+.2f}%"
        )
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=png_bytes,
            caption=caption,
        )
    except Exception as e:
        logger.error(f"cmd_chart failed: {e}", exc_info=True)
        await _safe_send(context, chat_id, "❌ Ошибка генерации графика.")


# ---- Backtest Command ----

async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /backtest [days|YYYY-MM-DD] — run historical backtest."""
    if not _is_authorized(update):
        return
    chat_id = update.effective_chat.id
    args = context.args

    if not args:
        await _safe_send(context, chat_id,
            "*Использование:*\n"
            "`/backtest 365` — бэктест за последние 365 дней\n"
            "`/backtest 2024-01-15` — бэктест на конкретную дату\n\n"
            "_Бэктест занимает 2-5 минут._"
        )
        return

    arg = args[0]

    # Determine if it's a date or number of days
    is_date = "-" in arg and len(arg) == 10
    if is_date:
        try:
            datetime.strptime(arg, "%Y-%m-%d")
        except ValueError:
            await _safe_send(context, chat_id, "❌ Формат даты: `YYYY-MM-DD` (напр. `2024-01-15`)")
            return
        await _safe_send(context, chat_id,
            f"🧪 *Запуск бэктеста на {arg}...*\n\n"
            "Это займёт 2-5 минут.\n"
            "Бот продолжает работать — можете пользоваться другими командами."
        )
        task = asyncio.create_task(_run_backtest_background(context.bot, chat_id, date=arg))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    else:
        try:
            days = int(arg)
            if days < 30 or days > 3650:
                await _safe_send(context, chat_id, "❌ Укажите от 30 до 3650 дней")
                return
        except ValueError:
            await _safe_send(context, chat_id, "❌ Укажите число дней или дату: `/backtest 365` или `/backtest 2024-01-15`")
            return
        await _safe_send(context, chat_id,
            f"🧪 *Запуск бэктеста за {days} дней...*\n\n"
            "Это займёт 2-5 минут.\n"
            "Бот продолжает работать — можете пользоваться другими командами."
        )
        task = asyncio.create_task(_run_backtest_background(context.bot, chat_id, days=days))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)


async def _run_backtest_background(bot, chat_id, days=None, date=None):
    """Run backtest in background and send results."""
    from backtest.engine import run_backtest, run_backtest_date

    try:
        if date:
            result = await asyncio.get_event_loop().run_in_executor(
                None, run_backtest_date, date,
            )
        else:
            result = await asyncio.get_event_loop().run_in_executor(
                None, run_backtest, days,
            )

        msg = format_backtest(result)
        await _safe_send(bot, chat_id, msg)

    except Exception as e:
        logger.error(f"Backtest failed: {e}", exc_info=True)
        await _safe_send(bot, chat_id, "❌ *Ошибка бэктеста.* Повторите позже.")


# ── Paper Trading Commands ──────────────────────────────────────────────────────

async def cmd_paper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/paper — show paper trading dashboard."""
    if not _is_authorized(update):
        return
    chat_id = str(update.effective_chat.id)

    await update.message.reply_text("🔄 Загружаю данные paper trading...")

    try:
        from trading.alpaca_executor import AlpacaExecutor
        executor = AlpacaExecutor.get_instance()

        mode = get_setting("paper_trading_mode", "off")
        stats = await asyncio.get_event_loop().run_in_executor(None, get_paper_trades_stats)
        account = await asyncio.get_event_loop().run_in_executor(
            None, executor.get_account
        ) if executor else {}
        positions = await asyncio.get_event_loop().run_in_executor(
            None, executor.get_open_positions
        ) if executor else []

        msg = format_paper_dashboard(mode, account, stats, positions)
        await _safe_send(context.bot, chat_id, msg)
    except Exception as e:
        logger.error(f"cmd_paper failed: {e}", exc_info=True)
        await _safe_send(context.bot, chat_id, f"❌ Ошибка: {e}")


async def cmd_paper_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/paper_mode [auto|hybrid|off] — switch paper trading mode (admin only)."""
    if not _is_authorized(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Только для администратора.")
        return

    chat_id = str(update.effective_chat.id)
    args = context.args or []

    current = get_setting("paper_trading_mode", "off")

    if not args:
        mode_labels = {"auto": "🤖 Авто", "hybrid": "🔀 Гибрид", "off": "⏸ Отключён"}
        await update.message.reply_text(
            f"📌 Текущий режим: *{mode_labels.get(current, current)}*\n\n"
            f"Доступные режимы:\n"
            f"• `/paper_mode auto` — автоматически исполнять все рекомендации\n"
            f"• `/paper_mode hybrid` — подтверждать каждую сделку вручную\n"
            f"• `/paper_mode off` — отключить paper trading",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    new_mode = args[0].lower()
    if new_mode not in ("auto", "hybrid", "off"):
        await update.message.reply_text("❌ Допустимые значения: `auto`, `hybrid`, `off`",
                                         parse_mode=ParseMode.MARKDOWN)
        return

    set_setting("paper_trading_mode", new_mode)
    mode_labels = {"auto": "🤖 Авто", "hybrid": "🔀 Гибрид", "off": "⏸ Отключён"}
    await update.message.reply_text(
        f"✅ Режим paper trading изменён:\n"
        f"{mode_labels.get(current, current)} → *{mode_labels.get(new_mode, new_mode)}*",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info(f"Paper trading mode changed: {current} → {new_mode} by {chat_id}")


async def paper_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks for Hybrid mode approve/reject."""
    query = update.callback_query
    await query.answer()

    if not query.data:
        return

    # Expected format: paper_approve_<trade_id> or paper_reject_<trade_id>
    parts = query.data.split("_")
    if len(parts) != 3 or parts[0] != "paper":
        return

    action = parts[1]   # "approve" or "reject"
    try:
        trade_id = int(parts[2])
    except ValueError:
        return

    trade = get_paper_trade_by_id(trade_id)
    if not trade:
        await query.edit_message_text("❌ Сделка не найдена.")
        return

    if trade["status"] != "pending_approval":
        await query.edit_message_text(
            f"⚠️ Сделка {trade['symbol']} уже обработана (статус: {trade['status']})"
        )
        return

    if action == "reject":
        reject_paper_trade(trade_id)
        await query.edit_message_text(
            f"❌ *{trade['symbol']}* — сделка отклонена.",
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info(f"Paper trade {trade_id} ({trade['symbol']}) rejected by user")
        return

    if action == "approve":
        await query.edit_message_text(
            f"⏳ *{trade['symbol']}* — исполняю ордер...",
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            from trading.alpaca_executor import AlpacaExecutor, PAPER_POSITION_SIZE_USD
            executor = AlpacaExecutor.get_instance()
            if not executor:
                await query.edit_message_text(
                    f"❌ Alpaca API недоступен. Проверь ALPACA_API_KEY в .env"
                )
                return

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: executor.open_position(
                    trade["symbol"], trade.get("notional_usd", PAPER_POSITION_SIZE_USD)
                )
            )

            if result["status"] == "success":
                update_paper_trade_open(
                    trade_id,
                    order_id=result["order_id"],
                    entry_price=result.get("filled_price"),
                    qty=result.get("qty"),
                )
                price_str = f"${result['filled_price']:.2f}" if result.get("filled_price") else "ожидает открытия рынка"
                await query.edit_message_text(
                    f"✅ *{trade['symbol']}* — ордер исполнен!\n"
                    f"  Цена входа: {price_str}\n"
                    f"  SL: ${trade.get('stop_loss_price', '?')}\n"
                    f"  Order ID: `{result['order_id']}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                logger.info(f"Paper trade {trade_id} ({trade['symbol']}) approved & executed")
            else:
                update_paper_trade_error(trade_id, result.get("error", "unknown"))
                await query.edit_message_text(
                    f"❌ *{trade['symbol']}* — ошибка ордера: {result.get('error')}",
                    parse_mode=ParseMode.MARKDOWN,
                )
        except Exception as e:
            logger.error(f"paper_callback approve error: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Ошибка исполнения: {e}")


async def _execute_paper_trades(bot, stocks: list[dict], signal_date: str):
    """
    Called after recommendations are generated.
    In 'auto' mode: executes all trades immediately.
    In 'hybrid' mode: sends approval buttons for each trade.
    In 'off' mode: does nothing.
    """
    from config import PAPER_POSITION_SIZE_USD, PAPER_HYBRID_TIMEOUT_HOURS, TELEGRAM_CHAT_ID
    from storage.database import save_paper_trade
    from trading.alpaca_executor import AlpacaExecutor

    mode = get_setting("paper_trading_mode", "off")
    if mode == "off" or not stocks:
        return

    executor = AlpacaExecutor.get_instance() if mode == "auto" else None
    admin_id = TELEGRAM_CHAT_ID

    logger.info(f"[PaperTrading] Mode={mode}, processing {len(stocks)} recommendations")

    for stock in stocks:
        symbol = stock.get("symbol") or stock.get("ticker", "?")
        scores = stock.get("scores", {})
        tech = stock.get("technical", {})
        score = scores.get("composite_score", 0.0)
        price = tech.get("current_price", 0.0) or 0.0
        sl_pct = -8.0
        tp_pct = 15.0
        sl_price = round(price * (1 + sl_pct / 100), 2) if price else None
        tp_price = round(price * (1 + tp_pct / 100), 2) if price else None

        try:
            if mode == "auto":
                # Create record first
                trade_id = save_paper_trade(
                    symbol=symbol,
                    notional_usd=PAPER_POSITION_SIZE_USD,
                    signal_date=signal_date,
                    signal_score=score,
                    stop_loss_price=sl_price,
                    take_profit_price=tp_price,
                    status="pending",
                )
                # Execute immediately
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda s=symbol: executor.open_position(s, PAPER_POSITION_SIZE_USD)
                )
                if result["status"] == "success":
                    update_paper_trade_open(
                        trade_id,
                        order_id=result["order_id"],
                        entry_price=result.get("filled_price"),
                        qty=result.get("qty"),
                    )
                    logger.info(f"[PaperTrading] AUTO executed: {symbol} order_id={result['order_id']}")
                else:
                    update_paper_trade_error(trade_id, result.get("error", "auto exec failed"))
                    logger.warning(f"[PaperTrading] AUTO failed: {symbol} — {result.get('error')}")

            elif mode == "hybrid":
                # Create record with pending_approval status
                trade_id = save_paper_trade(
                    symbol=symbol,
                    notional_usd=PAPER_POSITION_SIZE_USD,
                    signal_date=signal_date,
                    signal_score=score,
                    stop_loss_price=sl_price,
                    take_profit_price=tp_price,
                    status="pending_approval",
                )
                # Send approval message with inline keyboard
                msg = format_paper_approval_message(
                    symbol=symbol,
                    score=score,
                    price=price,
                    stop_loss=sl_price,
                    take_profit=tp_price,
                    trade_id=trade_id,
                    mode_timeout_h=PAPER_HYBRID_TIMEOUT_HOURS,
                )
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(f"✅ Купить {symbol}", callback_data=f"paper_approve_{trade_id}"),
                        InlineKeyboardButton("❌ Пропустить", callback_data=f"paper_reject_{trade_id}"),
                    ]
                ])
                if admin_id:
                    try:
                        await bot.send_message(
                            chat_id=int(admin_id),
                            text=msg,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=keyboard,
                        )
                        logger.info(f"[PaperTrading] HYBRID approval sent: {symbol} trade_id={trade_id}")
                    except Exception as e:
                        logger.warning(f"[PaperTrading] Could not send approval for {symbol}: {e}")

                # Schedule auto-cancel after timeout
                async def _auto_cancel(tid=trade_id, sym=symbol):
                    await asyncio.sleep(PAPER_HYBRID_TIMEOUT_HOURS * 3600)
                    from storage.database import get_paper_trade_by_id
                    t = get_paper_trade_by_id(tid)
                    if t and t["status"] == "pending_approval":
                        from storage.database import update_paper_trade_error
                        update_paper_trade_error(tid, "hybrid_timeout")
                        logger.info(f"[PaperTrading] HYBRID timeout cancelled: {sym} trade_id={tid}")
                        if admin_id:
                            try:
                                await bot.send_message(
                                    chat_id=int(admin_id),
                                    text=f"⏰ *{sym}* — время истекло, сделка отменена автоматически.",
                                    parse_mode=ParseMode.MARKDOWN,
                                )
                            except Exception:
                                pass

                task = asyncio.create_task(_auto_cancel())
                _bg_tasks.add(task)
                task.add_done_callback(_bg_tasks.discard)

        except Exception as e:
            logger.error(f"[PaperTrading] Error processing {symbol}: {e}", exc_info=True)


# ── Paper Trading SL/TP Monitor (called from check_results_job) ─────────────────

async def check_paper_sl_tp(bot):
    """
    Check open paper trades for SL/TP hits.
    Called daily after market close from check_results_job.
    """
    from config import STOP_LOSS_PCT, TAKE_PROFIT_PCT, TELEGRAM_CHAT_ID
    from storage.database import get_open_paper_trades, close_paper_trade
    from trading.alpaca_executor import AlpacaExecutor

    executor = AlpacaExecutor.get_instance()
    if not executor:
        return

    open_trades = get_open_paper_trades()
    if not open_trades:
        return

    logger.info(f"[PaperSL/TP] Checking {len(open_trades)} open trades")
    import yfinance as yf

    for trade in open_trades:
        symbol = trade["symbol"]
        entry_price = trade.get("entry_price")
        sl_price = trade.get("stop_loss_price")
        tp_price = trade.get("take_profit_price")

        if not entry_price:
            continue

        try:
            ticker = yf.Ticker(symbol)
            current_price = ticker.fast_info.get("last_price") or ticker.fast_info.last_price
            if not current_price:
                continue

            hit_sl = sl_price and current_price <= sl_price
            hit_tp = tp_price and current_price >= tp_price

            if hit_sl or hit_tp:
                reason = "SL" if hit_sl else "TP"
                # Close on Alpaca
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda s=symbol: executor.close_position(s)
                )
                exit_price = result.get("filled_price") or current_price
                exit_order = result.get("order_id")

                close_paper_trade(trade["id"], exit_price, reason, exit_order)
                pl_pct = round((exit_price - entry_price) / entry_price * 100, 2)
                sign = "+" if pl_pct >= 0 else ""
                emoji = "🟢" if pl_pct >= 0 else "🔴"

                logger.info(f"[PaperSL/TP] Closed {symbol} via {reason}: {sign}{pl_pct:.2f}%")

                if TELEGRAM_CHAT_ID:
                    try:
                        await bot.send_message(
                            chat_id=int(TELEGRAM_CHAT_ID),
                            text=(
                                f"{emoji} *Paper Trade закрыта ({reason})*\n\n"
                                f"📌 {symbol}\n"
                                f"  Вход: ${entry_price:.2f}\n"
                                f"  Выход: ${exit_price:.2f}\n"
                                f"  P&L: {sign}{pl_pct:.2f}%"
                            ),
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception as e:
                        logger.warning(f"SL/TP notify failed for {symbol}: {e}")

        except Exception as e:
            logger.warning(f"[PaperSL/TP] Error checking {symbol}: {e}")


# ── Scheduled Report ──────────────────────────────────────────────────────────

async def scheduled_report_job(context: ContextTypes.DEFAULT_TYPE):
    """Job callback for python-telegram-bot's job_queue (replaces APScheduler)."""
    logger.info("Scheduled report triggered by job_queue")
    await send_scheduled_report(context.bot)


async def send_scheduled_report(bot):
    """Send automated report to all subscribed users (non-blocking)."""
    subscribers = get_subscribed_users()
    if not subscribers and TELEGRAM_CHAT_ID:
        subscribers = [TELEGRAM_CHAT_ID]
    if not subscribers:
        logger.error("No subscribers and TELEGRAM_CHAT_ID not set")
        return

    if not run_full_analysis:
        for cid in subscribers:
            await _safe_send(bot, cid, "Analysis not configured")
        return

    for cid in subscribers:
        await _safe_send(bot, cid, "🔄 *Автоматический анализ S&P 500...*")

    async def _do_report():
        try:
            result = await asyncio.get_event_loop().run_in_executor(None, run_full_analysis)

            if not result or not result.get("stocks"):
                for cid in subscribers:
                    await _safe_send(bot, cid, "⚠️ По текущим критериям нет подходящих кандидатов.")
                return

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

            # Paper trading: execute or request approval for each recommendation
            try:
                from datetime import date as _date
                signal_date = _date.today().isoformat()
                await _execute_paper_trades(bot, result["stocks"], signal_date)
            except Exception as e:
                logger.error(f"Paper trading execution failed: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Scheduled report failed: {e}", exc_info=True)
            for cid in subscribers:
                await _safe_send(bot, cid, "❌ *Ошибка авто-отчёта.* Следующая попытка по расписанию.")

    task = asyncio.create_task(_do_report())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def check_results_job(context: ContextTypes.DEFAULT_TYPE):
    """Job callback: check pending recommendation results after market close."""
    logger.info("Check results job triggered")

    subscribers = get_subscribed_users()
    if not subscribers and TELEGRAM_CHAT_ID:
        subscribers = [TELEGRAM_CHAT_ID]

    # 10-day check
    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, check_pending_results)
        if results:
            msg = format_check_results(results)
            for cid in subscribers:
                try:
                    await _safe_send(context.bot, cid, msg)
                except telegram.error.Forbidden:
                    logger.warning(f"User {cid} blocked bot (10d check)")
                except Exception as e:
                    logger.warning(f"Failed to send 10d results to {cid}: {e}")
        else:
            logger.info("No pending 10d results to report")
    except Exception as e:
        logger.error(f"Check results (10d) job failed: {e}", exc_info=True)

    # 30-day check
    try:
        loop = asyncio.get_event_loop()
        results_30d = await loop.run_in_executor(None, check_pending_30d_results)
        if results_30d:
            msg_30d = format_check_results(results_30d, period="30д")
            for cid in subscribers:
                try:
                    await _safe_send(context.bot, cid, msg_30d)
                except telegram.error.Forbidden:
                    logger.warning(f"User {cid} blocked bot (30d check)")
                except Exception as e:
                    logger.warning(f"Failed to send 30d results to {cid}: {e}")
        else:
            logger.info("No pending 30d results to report")
    except Exception as e:
        logger.error(f"Check results (30d) job failed: {e}", exc_info=True)

    # Paper trading SL/TP check
    try:
        await check_paper_sl_tp(context.bot)
    except Exception as e:
        logger.error(f"Paper SL/TP check failed: {e}", exc_info=True)


async def dynamic_snapshot_job(context: ContextTypes.DEFAULT_TYPE):
    """Job callback: Tue/Thu/Sat dynamic tracking digest for all active recommendations."""
    logger.info("Dynamic snapshot job triggered")

    subscribers = get_subscribed_users()
    if not subscribers and TELEGRAM_CHAT_ID:
        subscribers = [TELEGRAM_CHAT_ID]

    try:
        loop = asyncio.get_event_loop()
        snapshot = await loop.run_in_executor(None, snapshot_all_recommendations)
        if not snapshot:
            logger.info("Snapshot: no data to send")
            return

        msg = format_snapshot_digest(snapshot)
        if not msg:
            return

        for cid in subscribers:
            try:
                await _safe_send(context.bot, cid, msg)
            except telegram.error.Forbidden:
                logger.warning(f"User {cid} blocked bot (snapshot)")
            except Exception as e:
                logger.warning(f"Failed to send snapshot to {cid}: {e}")

        logger.info(f"Snapshot digest sent to {len(subscribers)} users")
    except Exception as e:
        logger.error(f"Dynamic snapshot job failed: {e}", exc_info=True)


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
    """Job callback: check watchlist alerts, entry signals, and exit signals."""
    from alerts.watchlist_monitor import check_watchlist_alerts, check_entry_signals, check_exit_signals

    # 1. Standard watchlist alerts (price/RSI)
    try:
        loop = asyncio.get_event_loop()
        alerts_by_user = await loop.run_in_executor(None, check_watchlist_alerts)
        if alerts_by_user:
            for uid, alerts in alerts_by_user.items():
                try:
                    msg = format_alerts(alerts)
                    if msg:
                        await _safe_send(context.bot, uid, msg)
                except telegram.error.Forbidden:
                    logger.warning(f"User {uid} blocked bot (alerts)")
                except Exception as e:
                    logger.warning(f"Failed to send alerts to {uid}: {e}")
    except Exception as e:
        logger.error(f"Watchlist alert job failed: {e}", exc_info=True)

    # 2. Entry signals (bounce opportunities on watchlist)
    try:
        entry_signals = await loop.run_in_executor(None, check_entry_signals)
        if entry_signals:
            for uid, signals in entry_signals.items():
                try:
                    msg = format_entry_signals(signals)
                    if msg:
                        await _safe_send(context.bot, uid, msg)
                except telegram.error.Forbidden:
                    logger.warning(f"User {uid} blocked bot (entry signals)")
                except Exception as e:
                    logger.warning(f"Failed to send entry signals to {uid}: {e}")
            logger.info(f"Sent entry signals to {len(entry_signals)} users")
    except Exception as e:
        logger.error(f"Entry signals check failed: {e}", exc_info=True)

    # 3. Exit signals (for open positions)
    try:
        exit_signals = await loop.run_in_executor(None, check_exit_signals)
        if exit_signals:
            for uid, signals in exit_signals.items():
                try:
                    msg = format_exit_signals(signals)
                    if msg:
                        await _safe_send(context.bot, uid, msg)
                except telegram.error.Forbidden:
                    logger.warning(f"User {uid} blocked bot (exit signals)")
                except Exception as e:
                    logger.warning(f"Failed to send exit signals to {uid}: {e}")
            logger.info(f"Sent exit signals to {len(exit_signals)} users")
    except Exception as e:
        logger.error(f"Exit signals check failed: {e}", exc_info=True)


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
    app.add_handler(CommandHandler("take", cmd_take))
    app.add_handler(CommandHandler("sell", cmd_sell))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("performance", cmd_performance))
    app.add_handler(CommandHandler("chart", cmd_chart))
    app.add_handler(CommandHandler("backtest", cmd_backtest))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("paper", cmd_paper))
    app.add_handler(CommandHandler("paper_mode", cmd_paper_mode))
    # Inline button callbacks for Hybrid mode approve/reject
    app.add_handler(CallbackQueryHandler(paper_callback, pattern="^paper_"))

    logger.info("Telegram bot application created")
    return app
