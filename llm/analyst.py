import json
from openai import OpenAI
from config import OPENAI_API_KEY, OPENAI_MODEL, logger


client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def generate_analysis(stocks_data: list[dict], market_context: dict) -> str:
    """
    Send all data to GPT-4o for comprehensive bounce probability analysis.
    stocks_data: list of dicts, each containing technical, fundamental, sentiment, scores.
    Returns LLM response text.
    """
    if not client:
        return "OpenAI API key not configured."

    prompt = _build_prompt(stocks_data, market_context)

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=4000,
        )
        return response.choices[0].message.content or "No response"
    except Exception as e:
        logger.error(f"OpenAI request failed: {e}")
        return f"AI analysis unavailable: {e}"


def generate_single_stock_analysis(stock_data: dict, market_context: dict) -> str:
    """Deep analysis for a single stock (/analyze command)."""
    if not client:
        return "OpenAI API key not configured."

    prompt = _build_single_prompt(stock_data, market_context)

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=3000,
        )
        return response.choices[0].message.content or "No response"
    except Exception as e:
        logger.error(f"OpenAI single analysis failed: {e}")
        return f"AI analysis unavailable: {e}"


SYSTEM_PROMPT = """Ты опытный финансовый аналитик, специализирующийся на swing-трейдинге американскими акциями.
Твоя задача — анализировать акции S&P 500 с просадкой и оценивать вероятность отскока/разворота.

ПРАВИЛА:
1. Анализируй ТОЛЬКО данные, которые тебе предоставлены (технические индикаторы, фундаментальные показатели, новостной фон, инсайдерские сделки).
2. Для КАЖДОЙ акции ОБЪЯСНИ ПОЧЕМУ есть или нет вероятность отскока, ссылаясь на конкретные цифры.
3. Указывай ФАКТОРЫ РИСКА для каждой акции.
4. Давай оценку вероятности: ВЫСОКАЯ / СРЕДНЯЯ / НИЗКАЯ с обоснованием.
5. Предлагай зону входа, стоп-лосс и цель на основе уровней поддержки/сопротивления и Фибоначчи.
6. Если есть красные флаги (скорые отчеты, инсайдерские продажи, негативные новости), ОБЯЗАТЕЛЬНО отмечай их.
7. Используй русский язык.
8. Не используй символы # для заголовков (Telegram Markdown).
9. Не придумывай данных — работай только с тем, что дано.
10. Горизонт: 2-6 недель (среднесрочный swing)."""


def _build_prompt(stocks_data: list[dict], market_context: dict) -> str:
    regime = market_context.get("regime", "unknown")
    spy = market_context.get("spy", {})
    vix = market_context.get("vix", {})

    prompt = f"""КОНТЕКСТ РЫНКА:
- Режим: {regime.upper()}
- SPY: ${spy.get('price', '?')}, RSI {spy.get('rsi', '?')}, {'выше' if spy.get('above_sma200') else 'ниже'} SMA200
- VIX: {vix.get('value', '?')} ({vix.get('label', '?')})

ОТОБРАННЫЕ АКЦИИ ({len(stocks_data)} шт.) — прошли фильтры по просадке, перепроданности, цене ≤$200.
Отсортированы по composite score (больше = лучший кандидат на отскок).

По каждой акции напиши детальный блок:
1. Тикер и краткий вердикт (1 строка)
2. Вероятность отскока: ВЫСОКАЯ / СРЕДНЯЯ / НИЗКАЯ с обоснованием
3. Технический анализ: RSI, MACD, Bollinger Bands, объем, momentum, дивергенции
4. Фундаментальный профиль: P/E, рост прибыли/выручки, маржа, долг, FCF
5. Новостной фон и инсайдеры: что происходит с компанией
6. Факторы риска (красные флаги)
7. Торговый план: зона входа, стоп-лосс, цель (на основе S/R и Фибоначчи)

В конце — общий вывод по секторам и рынку (2-3 предложения).

ДАННЫЕ:
"""

    for stock in stocks_data:
        prompt += _format_stock_data(stock) + "\n---\n"

    return prompt


def _build_single_prompt(stock_data: dict, market_context: dict) -> str:
    regime = market_context.get("regime", "unknown")
    spy = market_context.get("spy", {})
    vix = market_context.get("vix", {})

    prompt = f"""КОНТЕКСТ РЫНКА: {regime.upper()}, SPY RSI {spy.get('rsi', '?')}, VIX {vix.get('value', '?')}

Дай ДЕТАЛЬНЫЙ анализ этой акции. Включи:
1. Общий вердикт и вероятность отскока
2. Подробный технический анализ (каждый индикатор)
3. Фундаментальный профиль компании
4. Новостной фон, инсайдерские сделки, мнение аналитиков
5. Факторы риска
6. Торговый план: вход, стоп, цель

ДАННЫЕ АКЦИИ:
{_format_stock_data(stock_data)}
"""
    return prompt


def _format_stock_data(stock: dict) -> str:
    t = stock.get("technical", {})
    f = stock.get("fundamental", {})
    s = stock.get("sentiment", {})
    sc = stock.get("scores", {})

    lines = [
        f"ТИКЕР: {t.get('symbol', '?')}",
        f"Цена: ${t.get('current_price', '?')} | Просадка: {t.get('drawdown_pct', '?')}%",
        f"Composite Score: {sc.get('composite_score', '?')}/100 ({sc.get('bounce_probability', '?')})",
        f"  Tech: {sc.get('technical_score', '?')} | Fund: {sc.get('fundamental_score', '?')} | Sent: {sc.get('sentiment_score', '?')} | Market: {sc.get('market_score', '?')}",
        "",
        "ТЕХНИЧЕСКИЕ ИНДИКАТОРЫ:",
        f"  RSI(14): {t.get('rsi', '?')} | StochRSI: {t.get('stoch_rsi', '?')}",
        f"  RSI дивергенция (бычья): {'ДА' if t.get('rsi_bullish_divergence') else 'нет'}",
        f"  MACD: {t.get('macd', '?')} | Signal: {t.get('macd_signal', '?')} | Histogram: {t.get('macd_histogram', '?')}",
        f"  MACD бычий кросс: {'ДА' if t.get('macd_cross_bullish') else 'нет'} | Гистограмма растёт: {'ДА' if t.get('macd_histogram_rising') else 'нет'}",
        f"  Bollinger: %B={t.get('bb_pct_b', '?')} | Ниже нижней: {'ДА' if t.get('bb_below_lower') else 'нет'} | Squeeze: {'ДА' if t.get('bb_squeeze') else 'нет'}",
        f"  SMA20: {t.get('sma20', '?')} | SMA50: {t.get('sma50', '?')} | SMA200: {t.get('sma200', '?')}",
        f"  От SMA200: {t.get('pct_from_sma200', '?')}% | Death Cross: {'ДА' if t.get('death_cross') else 'нет'}",
        f"  ATR%: {t.get('atr_pct', '?')}%",
        f"  Volume Ratio: {t.get('volume_ratio', '?')}x | OBV тренд: {t.get('obv_trend', '?')}",
        f"  Momentum: ROC5={t.get('roc5', '?')}%, ROC10={t.get('roc10', '?')}%, ROC20={t.get('roc20', '?')}%",
        f"  Pivot: S2={t.get('support2', '?')} S1={t.get('support1', '?')} P={t.get('pivot', '?')} R1={t.get('resistance1', '?')} R2={t.get('resistance2', '?')}",
        f"  Fibonacci: 38.2%={t.get('fib_382', '?')} 50%={t.get('fib_500', '?')} 61.8%={t.get('fib_618', '?')} | Ближайший: {t.get('fib_nearest_level', '?')} (${t.get('fib_nearest_price', '?')})",
        "",
        "ФУНДАМЕНТАЛЬНЫЕ ПОКАЗАТЕЛИ:",
        f"  P/E trailing: {f.get('pe_trailing', '?')} | forward: {f.get('pe_forward', '?')} | сектор медиана: {f.get('sector_pe_median', '?')} → {f.get('pe_vs_sector', '?')}",
        f"  EPS: {f.get('eps_trailing', '?')} → {f.get('eps_forward', '?')} | Рост EPS ожид: {f.get('eps_growth_expected', '?')}%",
        f"  Рост прибыли: {f.get('earnings_growth', '?')}% | Рост выручки: {f.get('revenue_growth', '?')}%",
        f"  Маржа: чистая {f.get('profit_margin', '?')}% | операц. {f.get('operating_margin', '?')}% | валовая {f.get('gross_margin', '?')}%",
        f"  ROE: {f.get('roe', '?')}% | ROA: {f.get('roa', '?')}%",
        f"  Debt/Equity: {f.get('debt_to_equity', '?')} | Current Ratio: {f.get('current_ratio', '?')}",
        f"  FCF: {'положительный' if f.get('fcf_positive') else 'отрицательный/нет данных'}",
        f"  Дивиденд: {f.get('dividend_yield', 'нет')}%",
        f"  Капитализация: {f.get('market_cap_label', '?')} | Качество: {f.get('quality_grade', '?')}",
        f"  Аналитики: target ${f.get('target_mean', '?')} (upside {f.get('upside_to_target', '?')}%)",
        "",
        "СЕНТИМЕНТ:",
        f"  Новости ({s.get('news_total', 0)} шт.): {s.get('news_sentiment', '?')} (pos:{s.get('news_positive', 0)} neg:{s.get('news_negative', 0)})",
        f"  Аналитики: {s.get('analyst_consensus', '?')} (buy:{s.get('analyst_buy', 0)} hold:{s.get('analyst_hold', 0)} sell:{s.get('analyst_sell', 0)})",
        f"  Отчётность: {s.get('earnings_next_date', 'неизвестно')} (через {s.get('earnings_days_until', '?')} дн.) {'⚠️ СКОРО!' if s.get('earnings_within_14d') else ''}",
        f"  Инсайдеры: {s.get('insider_sentiment', '?')} (покупки:{s.get('insider_buy_count', 0)} продажи:{s.get('insider_sell_count', 0)} нетто:${s.get('insider_net_value', 0):,.0f})",
    ]
    return "\n".join(lines)
