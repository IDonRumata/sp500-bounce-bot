from datetime import datetime


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

        link = f"https://finance.yahoo.com/quote/{symbol}"

        msg += f"*{i}. {score_emoji} [{symbol}]({link})*\n"
        msg += f"   ${t.get('current_price', '?')} | Просадка: {t.get('drawdown_pct', '?')}%\n"
        msg += f"   Score: *{composite}*/100 ({prob_ru})\n"
        msg += f"   RSI: {t.get('rsi', '?')} | MACD: {'↑' if t.get('macd_histogram_rising') else '↓'}"

        # Bullish divergence flag
        if t.get("rsi_bullish_divergence"):
            msg += " | Дивергенция!"

        msg += f"\n   P/E: {f.get('pe_forward', '?')} | Рост EPS: {f.get('earnings_growth', '?')}%"
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
        link = f"https://finance.yahoo.com/quote/{s}"
        msg += f"• [{s}]({link})\n"
    msg += "\nУправление: `/watchlist add TICKER` / `/watchlist remove TICKER`"
    return msg


def format_help() -> str:
    return """📊 *S&P 500 Bounce Analyzer*

*Команды:*
`/run` — Полный анализ (5-10 мин)
`/report` — Последний отчёт
`/analyze TICKER` — Анализ одной акции
`/watchlist` — Watchlist
`/watchlist add TICKER` — Добавить в watchlist
`/watchlist remove TICKER` — Убрать из watchlist
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


def format_status(last_run: str | None, next_run: str | None, uptime: str) -> str:
    return f"""🤖 *Статус бота*

⏱ Uptime: {uptime}
📅 Последний запуск: {last_run or 'нет'}
⏰ Следующий запуск: {next_run or 'по расписанию'}
✅ Бот работает"""
