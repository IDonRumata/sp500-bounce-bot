import re
from datetime import datetime


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown special characters in user data."""
    if not isinstance(text, str):
        return str(text)
    # Escape *_`[]() which have meaning in Telegram Markdown
    return re.sub(r'([*_`\[\]()])', r'\\\1', text)


def format_market_overview(market_ctx: dict) -> str:
    """Format market context as Telegram message."""
    spy = market_ctx.get("spy", {})
    vix = market_ctx.get("vix", {})
    regime = market_ctx.get("regime", "unknown")
    sectors = market_ctx.get("sectors", [])

    regime_emoji = {
        "bullish": "🟢", "neutral": "🟡", "weak": "🟠", "bearish": "🔴", "panic": "🔴🔴",
    }
    vix_emoji = {
        "complacent": "😴", "normal": "✅", "elevated": "⚠️", "high_fear": "😰", "panic": "🚨",
    }

    msg = f"📊 *Обзор рынка* — {datetime.now().strftime('%d.%m.%Y')}\n\n"
    msg += f"{regime_emoji.get(regime, '❓')} Режим: *{regime.upper()}*\n\n"
    msg += f"*SPY:* ${spy.get('price', '?')}\n"
    msg += f"  RSI: {spy.get('rsi', '?')} | Тренд: {spy.get('trend', '?')}\n"
    msg += f"  SMA50: {'выше ✅' if spy.get('above_sma50') else 'ниже ❌'} | SMA200: {'выше ✅' if spy.get('above_sma200') else 'ниже ❌'}\n\n"

    msg += f"{vix_emoji.get(vix.get('label', ''), '❓')} *VIX:* {vix.get('value', '?')} ({vix.get('label', '?')})\n\n"

    # Sector heatmap (top 5 + bottom 3)
    if sectors:
        msg += "*Секторы (RSI / от макс.):*\n"
        for s in sectors[:5]:
            strength_icon = "🟢" if s["relative_strength"] == "strong" else ("🔴" if s["relative_strength"] == "weak" else "🟡")
            msg += f"  {strength_icon} {s['sector']}: RSI {s['rsi']} ({s['pct_from_high']:+.1f}%)\n"
        if len(sectors) > 5:
            msg += "  ...\n"
            for s in sectors[-3:]:
                strength_icon = "🟢" if s["relative_strength"] == "strong" else ("🔴" if s["relative_strength"] == "weak" else "🟡")
                msg += f"  {strength_icon} {s['sector']}: RSI {s['rsi']} ({s['pct_from_high']:+.1f}%)\n"

    return msg


def format_stocks_table(stocks_data: list[dict]) -> str:
    """Format stocks list as Telegram message."""
    msg = f"📈 *Топ {len(stocks_data)} кандидатов на отскок*\n"
    msg += f"📅 {datetime.now().strftime('%d.%m.%Y')}\n\n"

    for i, stock in enumerate(stocks_data, 1):
        t = stock.get("technical", {})
        f = stock.get("fundamental", {})
        sc = stock.get("scores", {})
        symbol = t.get("symbol", "?")

        # Score → emoji
        composite = sc.get("composite_score", 0)
        if composite >= 70:
            score_emoji = "🟢"
        elif composite >= 55:
            score_emoji = "🟡"
        else:
            score_emoji = "🟠"

        # Probability label in Russian
        prob = sc.get("bounce_probability", "?")
        prob_ru = {
            "high": "ВЫСОКАЯ", "medium_high": "СРЕДНЕ-ВЫСОКАЯ",
            "medium": "СРЕДНЯЯ", "low": "НИЗКАЯ", "very_low": "ОЧЕНЬ НИЗКАЯ",
        }.get(prob, prob)

        link = f"https://www.tradingview.com/chart/?symbol={symbol}"

        msg += f"*{i}. {score_emoji} [{symbol}]({link})*\n"
        msg += f"   ${t.get('current_price', '?')} | Просадка: {t.get('drawdown_pct', '?')}%\n"
        msg += f"   Score: *{composite}*/100 ({prob_ru})\n"
        msg += f"   RSI: {t.get('rsi', '?')} | MACD: {'↑' if t.get('macd_histogram_rising') else '↓'}"

        # Bullish divergence flag
        if t.get("rsi_bullish_divergence"):
            msg += " | Дивергенция!"

        eg = f.get('earnings_growth')
        eg_str = f"{eg}%" if eg is not None else "N/A"
        pe_fwd = f.get('pe_forward')
        pe_str = f"{pe_fwd}" if pe_fwd is not None else "N/A"
        msg += f"\n   P/E: {pe_str} | Рост EPS: {eg_str}"
        msg += f" | Качество: {f.get('quality_grade', '?')}\n\n"

    msg += "⚠️ _Не является инвестиционной рекомендацией_"
    return msg


def format_ai_analysis(llm_response: str) -> str:
    """Format AI analysis as Telegram message."""
    msg = "🧠 *Детальный AI-анализ:*\n\n"
    msg += llm_response
    msg += "\n\n⚠️ _Не является инвестиционной рекомендацией. Для образовательных целей._"
    return msg


def format_single_stock(stock_data: dict, llm_response: str) -> list[str]:
    """Format single stock analysis as multiple Telegram messages."""
    t = stock_data.get("technical", {})
    f = stock_data.get("fundamental", {})
    s = stock_data.get("sentiment", {})
    sc = stock_data.get("scores", {})
    symbol = t.get("symbol", "?")

    # Message 1: Data overview
    msg1 = f"📊 *{symbol}* — Детальный анализ\n\n"
    msg1 += f"💰 Цена: *${t.get('current_price', '?')}* | Просадка: *{t.get('drawdown_pct', '?')}%*\n"
    msg1 += f"🎯 Score: *{sc.get('composite_score', '?')}*/100 ({sc.get('bounce_probability', '?')})\n\n"

    msg1 += "*Технические:*\n"
    msg1 += f"  RSI: {t.get('rsi', '?')} | StochRSI: {t.get('stoch_rsi', '?')}\n"
    msg1 += f"  MACD: {t.get('macd', '?')} (hist: {t.get('macd_histogram', '?')})\n"
    msg1 += f"  BB %B: {t.get('bb_pct_b', '?')} | ATR%: {t.get('atr_pct', '?')}%\n"
    msg1 += f"  SMA200: {t.get('sma200', '?')} ({t.get('pct_from_sma200', '?')}%)\n"
    msg1 += f"  Momentum: ROC5={t.get('roc5', '?')}% ROC10={t.get('roc10', '?')}%\n"
    msg1 += f"  Pivot S1: {t.get('support1', '?')} | R1: {t.get('resistance1', '?')}\n"
    msg1 += f"  Fib 50%: {t.get('fib_500', '?')} | 61.8%: {t.get('fib_618', '?')}\n\n"

    msg1 += "*Фундаментальные:*\n"
    msg1 += f"  P/E: {f.get('pe_trailing', '?')}/{f.get('pe_forward', '?')} (сект: {f.get('sector_pe_median', '?')})\n"
    msg1 += f"  Прибыль: {f.get('earnings_growth', '?')}% | Выручка: {f.get('revenue_growth', '?')}%\n"
    msg1 += f"  Маржа: {f.get('profit_margin', '?')}% | D/E: {f.get('debt_to_equity', '?')}\n"
    msg1 += f"  ROE: {f.get('roe', '?')}% | FCF: {'✅' if f.get('fcf_positive') else '❌'}\n"
    msg1 += f"  Качество: *{f.get('quality_grade', '?')}* | Target: ${f.get('target_mean', '?')} ({f.get('upside_to_target', '?')}%)\n\n"

    msg1 += "*Сентимент:*\n"
    msg1 += f"  Новости: {s.get('news_sentiment', '?')} ({s.get('news_total', 0)} шт.)\n"
    msg1 += f"  Аналитики: {s.get('analyst_consensus', '?')}\n"
    msg1 += f"  Инсайдеры: {s.get('insider_sentiment', '?')}\n"
    if s.get("earnings_within_14d"):
        msg1 += f"  ⚠️ Отчёт скоро: {s.get('earnings_next_date', '?')}\n"

    # Message 2: AI analysis
    msg2 = f"🧠 *AI-анализ {symbol}:*\n\n"
    msg2 += llm_response
    msg2 += "\n\n⚠️ _Не является инвестиционной рекомендацией_"

    return [msg1, msg2]


def format_watchlist(symbols: list[str]) -> str:
    if not symbols:
        return "📋 *Watchlist пуст*\n\nДобавьте тикер: `/watchlist add AAPL`"
    msg = f"📋 *Watchlist* ({len(symbols)} шт.):\n\n"
    for s in symbols:
        link = f"https://www.tradingview.com/chart/?symbol={s}"
        msg += f"• [{s}]({link})\n"
    msg += "\nУправление: `/watchlist add TICKER` / `/watchlist remove TICKER`"
    return msg


def format_stats(stats: dict) -> str:
    """Format recommendation statistics for /stats command."""
    total = stats.get("total_all", 0)
    if total == 0:
        return "📊 *Статистика рекомендаций*\n\nДанных пока нет. Запустите `/run` для первого анализа."

    checked = stats.get("total_checked", 0)
    pending = stats.get("pending", 0)
    success = stats.get("success", 0)
    neutral = stats.get("neutral", 0)
    failure = stats.get("failure", 0)
    success_pct = stats.get("success_pct", 0)
    avg_pct = stats.get("avg_result_pct")

    msg = f"📊 *Статистика рекомендаций*\n\n"
    msg += f"Всего: *{total}* | Проверено: *{checked}* | Ожидают: *{pending}*\n\n"

    if checked > 0:
        msg += f"✅ Успех (+3%): *{success}* ({success_pct}%)\n"
        msg += f"➖ Нейтрально: *{neutral}*\n"
        msg += f"❌ Неудача (-3%): *{failure}*\n"
        if avg_pct is not None:
            msg += f"\n📈 Средний результат: *{avg_pct:+.2f}%*\n"

        # Best / worst
        best = stats.get("best")
        worst = stats.get("worst")
        if best:
            msg += f"\n🏆 Лучшая: *{best['ticker']}* ({best['result_pct']:+.2f}%) от {best['signal_date']}"
        if worst:
            msg += f"\n💀 Худшая: *{worst['ticker']}* ({worst['result_pct']:+.2f}%) от {worst['signal_date']}"

        # Score bins correlation
        bins = stats.get("score_bins", [])
        if bins:
            msg += "\n\n*Score → Win rate:*\n"
            for b in bins:
                total_bin = b["total"]
                successes = b["successes"]
                win_rate = round(successes / total_bin * 100, 1) if total_bin > 0 else 0
                msg += f"  {b['score_bin']}: {successes}/{total_bin} ({win_rate}%)\n"

    return msg


def format_check_results(results: list[dict]) -> str:
    """Format check results for Telegram notification."""
    if not results:
        return ""

    success_count = sum(1 for r in results if r["status"] == "success")
    failure_count = sum(1 for r in results if r["status"] == "failure")

    msg = f"📋 *Проверка рекомендаций* ({len(results)} шт.)\n\n"

    for r in results:
        status_emoji = {"success": "✅", "failure": "❌", "neutral": "➖"}.get(r["status"], "❓")
        msg += f"{status_emoji} *{r['ticker']}*: "
        msg += f"${r['price_at_signal']:.2f} → ${r['price_at_check']:.2f} "
        msg += f"(*{r['result_pct']:+.2f}%*)\n"
        msg += f"   Score: {r['composite_score']} | от {r['signal_date']}\n"

    msg += f"\nИтого: ✅{success_count} ❌{failure_count} ➖{len(results) - success_count - failure_count}"
    return msg


def format_alerts(alerts: list[dict]) -> str:
    """Format watchlist alerts for a single user."""
    if not alerts:
        return ""

    type_emoji = {
        "price_drop": "🔴", "price_surge": "🟢",
        "rsi_oversold": "📉", "rsi_overbought": "📈",
    }
    type_label = {
        "price_drop": "Падение", "price_surge": "Рост",
        "rsi_oversold": "RSI перепродан", "rsi_overbought": "RSI перекуплен",
    }

    msg = f"🔔 *Алерты watchlist* ({len(alerts)})\n\n"
    for a in alerts:
        emoji = type_emoji.get(a["alert_type"], "❓")
        label = type_label.get(a["alert_type"], a["alert_type"])
        link = f"https://www.tradingview.com/chart/?symbol={a['ticker']}"
        msg += f"{emoji} [{a['ticker']}]({link}): {label}\n"
        msg += f"   ${a['price']} ({a['change_pct']:+.2f}%)"
        if a.get("rsi") is not None:
            msg += f" | RSI: {a['rsi']}"
        msg += "\n"
    return msg


def format_portfolio(positions: list[dict], summary: dict) -> str:
    """Format open portfolio positions with live P&L."""
    if not positions:
        return (
            "💼 *Портфель пуст*\n\n"
            "Откройте позицию: `/take AAPL 5`\n"
            "(5 акций AAPL по текущей цене)"
        )

    msg = f"💼 *Портфель* ({summary['count']} позиций)\n\n"

    for p in positions:
        ticker = p["ticker"]
        link = f"https://www.tradingview.com/chart/?symbol={ticker}"
        pnl_pct = p.get("pnl_pct")
        pnl_abs = p.get("pnl_abs")
        cur = p.get("current_price")

        if pnl_pct is not None and pnl_pct >= 0:
            emoji = "🟢"
        elif pnl_pct is not None:
            emoji = "🔴"
        else:
            emoji = "⚪"

        msg += f"{emoji} [{ticker}]({link}): {p['shares']}шт @ ${p['buy_price']:.2f}\n"
        if cur is not None:
            msg += f"   Сейчас: ${cur:.2f} | P&L: *{pnl_pct:+.2f}%* (${pnl_abs:+.2f})\n"
        else:
            msg += f"   Цена недоступна\n"

    # Summary
    msg += f"\n*Итого:*\n"
    msg += f"  Вложено: ${summary['total_invested']:,.2f}\n"
    msg += f"  Стоимость: ${summary['total_value']:,.2f}\n"

    pnl = summary["total_pnl"]
    pnl_pct = summary["total_pnl_pct"]
    pnl_emoji = "📈" if pnl >= 0 else "📉"
    msg += f"  {pnl_emoji} P&L: *{pnl_pct:+.2f}%* (${pnl:+,.2f})\n"

    best = summary.get("best")
    worst = summary.get("worst")
    if best:
        msg += f"\n  🏆 Лучшая: *{best['ticker']}* ({best['pnl_pct']:+.2f}%)"
    if worst:
        msg += f"\n  💀 Худшая: *{worst['ticker']}* ({worst['pnl_pct']:+.2f}%)"

    return msg


def format_portfolio_history(trades: list[dict]) -> str:
    """Format closed trades history."""
    if not trades:
        return "📜 *История сделок*\n\nЗакрытых позиций нет."

    msg = f"📜 *История сделок* ({len(trades)})\n\n"
    total_pnl = 0
    wins = 0

    for t in trades:
        pnl = t.get("pnl_pct", 0) or 0
        pnl_abs = t.get("pnl_abs", 0) or 0
        total_pnl += pnl_abs
        if pnl >= 0:
            wins += 1
        emoji = "✅" if pnl >= 0 else "❌"
        msg += (
            f"{emoji} *{t['ticker']}*: {t['shares']}шт "
            f"${t['buy_price']:.2f} → ${t.get('sell_price', 0):.2f} "
            f"(*{pnl:+.2f}%*, ${pnl_abs:+.2f})\n"
        )

    win_rate = round(wins / len(trades) * 100, 1) if trades else 0
    msg += f"\n*Итого:* ${total_pnl:+,.2f} | Win rate: {win_rate}%"
    return msg


def format_settings(user: dict) -> str:
    """Format user settings for /settings command."""
    sub = "✅ Подписан" if user.get("subscribed_reports") else "❌ Отписан"
    alert = "✅ Вкл" if user.get("alert_enabled") else "❌ Выкл"
    role = "👑 Админ" if user.get("is_admin") else "Пользователь"
    reg = user.get("registered_at", "?")
    last = user.get("last_active", "?")
    return (
        f"⚙️ *Настройки*\n\n"
        f"ID: `{user['chat_id']}`\n"
        f"Имя: {user.get('first_name') or '—'} (@{user.get('username') or '—'})\n"
        f"Роль: {role}\n\n"
        f"📬 Авто-отчёты: {sub}\n"
        f"🔔 Алерты: {alert}\n\n"
        f"📅 Регистрация: {reg}\n"
        f"🕐 Последняя активность: {last}\n\n"
        f"_Управление:_\n"
        f"`/subscribe` — подписаться на отчёты\n"
        f"`/unsubscribe` — отписаться"
    )


def format_admin_users(users: list[dict]) -> str:
    """Format user list for /admin users."""
    if not users:
        return "👥 *Пользователи*\n\nПока нет зарегистрированных пользователей."
    msg = f"👥 *Пользователи* ({len(users)})\n\n"
    for u in users:
        role = "👑" if u.get("is_admin") else "👤"
        sub = "📬" if u.get("subscribed_reports") else "🔕"
        name = u.get("first_name") or "—"
        uname = f"@{u['username']}" if u.get("username") else ""
        msg += f"{role} {sub} `{u['chat_id']}` {name} {uname}\n"
    return msg


def format_help() -> str:
    return """📊 *S&P 500 Bounce Analyzer*

*Команды:*
`/run` — Полный анализ (5-10 мин)
`/report` — Последний отчёт
`/analyze TICKER` — Анализ одной акции
`/stats` — Статистика рекомендаций
`/watchlist` — Watchlist
`/watchlist add TICKER` — Добавить в watchlist
`/watchlist remove TICKER` — Убрать из watchlist
`/take TICKER QTY` — Купить (напр. `/take AAPL 5`)
`/sell TICKER` — Продать позицию
`/portfolio` — Открытые позиции с P&L
`/portfolio history` — История закрытых сделок
`/backtest 365` — Бэктест за N дней
`/backtest 2024-01-15` — Бэктест на дату
`/subscribe` — Подписаться на авто-отчёты
`/unsubscribe` — Отписаться от авто-отчётов
`/settings` — Мои настройки
`/status` — Статус бота
`/help` — Эта справка

⏰ Авто-отчёты: Пн, Ср, Пт 08:00 UTC

*Что анализируется:*
• Технический анализ (RSI, MACD, BB, SMA, Fibonacci, Volume, S/R)
• Фундаментальные показатели (P/E, EPS, Revenue, Margins, Debt)
• Новостной фон и инсайдерские сделки
• Рекомендации аналитиков
• Календарь отчётностей

⚠️ _Не является инвестиционной рекомендацией_"""


def format_backtest(result: dict) -> str:
    """Format backtest results for Telegram."""
    if result.get("error"):
        return f"❌ *Ошибка бэктеста:* {result['error']}"

    total = result.get("total_signals", 0)
    if total == 0:
        return "📉 *Бэктест*\n\nНе найдено сигналов за указанный период."

    stats = result.get("stats", {})
    date_info = result.get("date", "")
    days_back = result.get("days_back", "")

    if date_info:
        msg = f"🧪 *Бэктест на {date_info}*\n\n"
    else:
        msg = f"🧪 *Бэктест за {days_back} дней*\n"
        msg += f"📊 Тестовых дат: {result.get('test_dates', '?')}\n\n"

    msg += f"Всего сигналов: *{total}*\n\n"

    # Results by window
    msg += "*Результаты:*\n"
    for w in [5, 10, 20]:
        wr = stats.get(f"win_rate_{w}d")
        avg = stats.get(f"avg_return_{w}d")
        med = stats.get(f"median_return_{w}d")
        cnt = stats.get(f"count_{w}d", 0)
        if wr is not None:
            wr_emoji = "🟢" if wr >= 55 else ("🟡" if wr >= 45 else "🔴")
            avg_emoji = "📈" if avg >= 0 else "📉"
            msg += (
                f"\n  *{w} дней:*\n"
                f"    {wr_emoji} Win rate: *{wr}%* ({cnt} сигналов)\n"
                f"    {avg_emoji} Средний: *{avg:+.2f}%* | Медиана: *{med:+.2f}%*\n"
                f"    Лучший: {stats.get(f'best_{w}d', '?')}% | Худший: {stats.get(f'worst_{w}d', '?')}%\n"
            )

    # Max gain/loss
    avg_mg = stats.get("avg_max_gain")
    avg_ml = stats.get("avg_max_loss")
    if avg_mg is not None and avg_ml is not None:
        msg += f"\n📊 Ср. макс. рост: *{avg_mg:+.2f}%* | Ср. макс. падение: *{avg_ml:+.2f}%*\n"

    # Score stats
    avg_sc = stats.get("avg_score")
    if avg_sc is not None:
        msg += f"\n🎯 Ср. score: {avg_sc} (мин: {stats.get('min_score', '?')}, макс: {stats.get('max_score', '?')})\n"

    # By regime
    by_regime = stats.get("by_regime", {})
    if by_regime:
        msg += "\n*По режиму рынка (10д):*\n"
        for regime, data in sorted(by_regime.items()):
            r_emoji = {"bullish": "🟢", "neutral": "🟡", "weak": "🟠", "bearish": "🔴", "panic": "🔴🔴"}.get(regime, "❓")
            msg += f"  {r_emoji} {regime}: WR *{data['win_rate']}%* | avg *{data['avg_return']:+.2f}%* ({data['count']})\n"

    # Top signals table (last 10)
    signals = result.get("signals", [])
    if signals:
        msg += "\n*Последние сигналы:*\n"
        for s in signals[-10:]:
            ret10 = s.get("return_10d")
            if ret10 is not None:
                ret_emoji = "✅" if ret10 > 0 else "❌"
                msg += f"  {ret_emoji} {s['date']} *{s['symbol']}* sc:{s['composite_score']} → *{ret10:+.1f}%*\n"
            else:
                msg += f"  ⏳ {s['date']} *{s['symbol']}* sc:{s['composite_score']}\n"

    msg += "\n_Тех. анализ честный (без знания будущего)._\n"
    msg += "_Фунд/сентимент = нейтральный (50). LLM пропущен._"
    return msg


def format_status(last_run: str | None, next_run: str | None, uptime: str) -> str:
    return f"""🤖 *Статус бота*

⏱ Uptime: {uptime}
📅 Последний запуск: {last_run or 'нет'}
⏰ Следующий запуск: {next_run or 'по расписанию'}
✅ Бот работает"""
