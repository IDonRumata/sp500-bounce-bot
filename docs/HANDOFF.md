# HANDOFF: S&P 500 Bounce Probability Analyzer Bot

> **Цель документа:** Полное описание проекта для продолжения разработки в новом чате.
> **Дата:** 2026-03-11
> **Репозиторий:** https://github.com/IDonRumata/sp500-bounce-bot
> **VPS:** 185.229.251.166 (Ubuntu 22.04), systemd service `sp500bot.service`

---

## 1. ЧТО ЭТО

Telegram-бот для анализа акций S&P 500 с просадкой. Ищет кандидатов на отскок (bounce) с помощью многофакторного анализа: технические индикаторы (20+), фундаментальные показатели, сентимент (Finnhub), контекст рынка (SPY/VIX/секторы). Генерирует AI-отчёт через GPT-4o-mini.

**Стек:** Python 3.11+, python-telegram-bot 22.6, yfinance, Finnhub API, OpenAI API, SQLite, systemd.

---

## 2. ТЕКУЩЕЕ СОСТОЯНИЕ (всё работает)

| Компонент | Статус |
|---|---|
| Telegram-бот (6 команд) | Работает |
| Расписание Пн/Ср/Пт 08:00 UTC | Работает (job_queue) |
| Меню-кнопка в Telegram | Работает |
| Технический анализ (RSI, MACD, BB, Fib, Pivot, StochRSI, ATR, OBV, ROC) | Работает |
| Фундаментальный анализ (P/E, EPS, Margins, D/E, FCF, Quality A-F) | Работает |
| Сентимент (Finnhub: новости, аналитики, инсайдеры, earnings) | Работает |
| Composite scoring 0-100 (Tech 40% + Fund 30% + Sent 15% + Market 15%) | Работает |
| Market context (SPY, VIX, секторные ETF, режим рынка) | Работает |
| Режимный мультипликатор (bullish:1.0 ... panic:0.70) | Работает |
| GPT-4o-mini + 24h LLM кэш | Работает |
| SQLite (reports, watchlist, cache) | Работает |
| Авторизация (AUTHORIZED_CHAT_IDS) | Работает |
| Валидация тикеров (regex) | Работает |
| Подавление токенов в логах (httpx) | Работает |
| systemd на VPS | Работает |

---

## 3. СТРУКТУРА ПРОЕКТА

```
sp500-bounce-bot/
├── main.py                     # 316 строк — Entry point, job_queue, menu setup, pipeline
├── config.py                   # 73 строки — Все настройки из .env
├── requirements.txt            # 9 зависимостей
├── .env.example                # Шаблон .env
├── .env                        # НЕ в git (токены, ключи)
│
├── data/
│   ├── sp500_list.py           # 68 строк — Wikipedia scraping + fallback 100
│   ├── price_fetcher.py        # 183 строки — yfinance: batch/single OHLCV + fundamentals
│   ├── finnhub_client.py       # 253 строки — News, analysts, insiders, earnings
│   └── market_context.py       # 176 строк — SPY, VIX, 11 секторных ETF, regime
│
├── analysis/
│   ├── technical.py            # 358 строк — RSI, MACD, BB, SMA, ATR, StochRSI, Volume, Pivot, Fib
│   ├── fundamental.py          # 204 строки — PE, EPS, Margins, D/E, FCF, Quality grade A-F
│   └── sentiment.py            # 58 строк — Обёртка над finnhub_client
│
├── scoring/
│   └── scorer.py               # 340 строк — Composite scoring с 4 под-скорами
│
├── bot/
│   ├── telegram_bot.py         # 320 строк — Handlers: /run /report /analyze /watchlist /status /help
│   └── formatters.py           # 185 строк — Telegram Markdown форматирование (русский)
│
├── llm/
│   └── analyst.py              # 199 строк — GPT-4o-mini промпт + 24h кэш
│
├── storage/
│   └── database.py             # 153 строки — SQLite: reports, watchlist, cache
│
└── docs/
    └── TZ_Extended_v2.md       # Расширенное ТЗ v2.0
```

**Итого:** ~2500 строк кода.

---

## 4. PIPELINE (как работает /run)

```
1. fetch_market_context()       → SPY RSI/SMA, VIX уровень, 11 секторов → regime
2. fetch_sp500_tickers()        → 500 тикеров (Wikipedia, кэш 7 дней)
3. quick_prefilter()            → Batch yfinance 500 тикеров → фильтр: price≤200, drawdown≥-10%, RSI≤45
                                  → ~20-50 кандидатов с DataFrame
4. Для каждого кандидата:
   a) full_technical_analysis() → 60+ полей (RSI, MACD, BB, SMA, ATR, StochRSI, Pivot, Fib...)
   b) fetch_fundamentals()      → 25+ метрик (PE, EPS, margins, D/E, FCF...)
   c) analyze_sentiment()       → Finnhub: news, analysts, insiders, earnings
   d) compute_composite_score() → 0-100 (Tech×0.40 + Fund×0.30 + Sent×0.15 + Market×0.15)
   e) Режимный мультипликатор   → score × (0.70-1.0) в зависимости от рынка
5. Фильтр score ≥ MIN_COMPOSITE_SCORE, убрать quality_grade=F + low probability
6. Топ 7 по score → generate_analysis() (GPT-4o-mini, 24h кэш)
7. save_report() → SQLite
8. 3 сообщения в Telegram: market overview, stocks table, AI analysis
```

---

## 5. КЛЮЧЕВЫЕ КОНФИГУРАЦИИ (.env)

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=166456217
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
FINNHUB_API_KEY=...
SCHEDULE_DAYS=mon,wed,fri
SCHEDULE_HOUR=8
SCHEDULE_MINUTE=0
MAX_PRICE=200
MIN_DRAWDOWN=-10
MIN_COMPOSITE_SCORE=58
TOP_PICKS_COUNT=7
PRE_FILTER_RSI=45
AUTHORIZED_CHAT_IDS=166456217
LOG_LEVEL=INFO
```

**Веса скоринга (config.py, хардкод):**
```python
WEIGHT_TECHNICAL = 0.40
WEIGHT_FUNDAMENTAL = 0.30
WEIGHT_SENTIMENT = 0.15
WEIGHT_MARKET = 0.15
```

---

## 6. НАЙДЕННЫЕ УЯЗВИМОСТИ (не исправлены)

### Критичные

**6.1. RSI считается 3 разными способами:**
- `analysis/technical.py:_rsi()` — EWM, `alpha=1/period`
- `data/market_context.py:_calc_rsi()` — Rolling mean (SMA)
- `data/price_fetcher.py:quick_prefilter()` — EWM, `alpha=1/14` (инлайн)

Один тикер может получить RSI=32 в одном модуле и RSI=38 в другом. Нужно вынести в одну функцию.

**6.2. Нет Markdown-экранирования в formatters.py:**
Спецсимволы `*_`[]` из названий компаний/новостей могут сломать Telegram Markdown.

**6.3. .env без chmod 600 на VPS:**
Токены и ключи доступны всем пользователям системы.

### Средние

**6.4. Нет rate limit на /run** — можно спамить, тратя OpenAI/Finnhub.

**6.5. Wikipedia scraping хрупкий** — один fallback на 100 тикеров (из 500).

**6.6. Нет бэкапов SQLite** — потеря VPS = потеря всех данных.

**6.7. Мёртвый код:** `bot/telegram_bot.py` содержит `post_init()` (строки 270-298) и `.post_init(post_init)` в builder (строка 306). Setup перенесён в `main.py:main()` (строки 265-281). post_init никогда не вызывается — нужно удалить.

**6.8. Telegram 4096 limit:** `_safe_send()` разбивает по `\n\n`, но один блок > 4000 символов не обработан.

---

## 7. ЧТО НУЖНО СДЕЛАТЬ (по приоритету)

### Этап 1 — Логирование рекомендаций (Критично)

Сейчас `save_report()` пишет весь отчёт как JSON-блоб. Нельзя делать SQL-запросы по отдельным акциям.

**Задача:** Добавить таблицу `recommendations` в SQLite (одна строка = одна рекомендация):
```sql
CREATE TABLE recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    price_at_signal REAL NOT NULL,
    rsi REAL,
    composite_score REAL NOT NULL,
    bounce_probability TEXT,
    technical_score REAL,
    fundamental_score REAL,
    sentiment_score REAL,
    market_score REAL,
    pe_ratio REAL,
    eps_growth REAL,
    quality_grade TEXT,
    drawdown_pct REAL,
    sector TEXT,
    market_regime TEXT,
    check_date TEXT,           -- signal + 10 рабочих дней
    price_at_check REAL,       -- NULL пока pending
    result_pct REAL,           -- NULL пока pending
    max_price_in_period REAL,
    min_price_in_period REAL,
    status TEXT DEFAULT 'pending',  -- pending/success/neutral/failure
    created_at TEXT DEFAULT (datetime('now'))
);
```

Добавить таблицу `market_snapshots` (одна строка = один отчёт):
```sql
CREATE TABLE market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT UNIQUE NOT NULL,
    spy_price REAL, spy_rsi REAL, spy_trend TEXT,
    spy_above_sma50 INTEGER, spy_above_sma200 INTEGER,
    vix_value REAL, vix_label TEXT,
    market_regime TEXT,
    total_candidates INTEGER,
    total_recommended INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
```

В `main.py:run_full_analysis()` — после скоринга вызвать `save_recommendations()`.

### Этап 2 — Скрипт проверки результатов (Критично)

Новый job в `job_queue` (ежедневно 18:00 UTC Пн-Пт):
1. SELECT из `recommendations` WHERE `status='pending'` AND `check_date <= today`
2. yfinance → текущая цена
3. result_pct = (current - price_at_signal) / price_at_signal × 100
4. Статус: success (+3%+), neutral (-3%..+3%), failure (-3%--)
5. UPDATE в БД

Конфиг (добавить в config.py/.env):
```python
CHECK_PERIOD_DAYS = 10       # рабочих дней
SUCCESS_THRESHOLD_PCT = 3.0
FAILURE_THRESHOLD_PCT = -3.0
```

### Этап 3 — Исправление уязвимостей (Критично)

1. **Унифицировать RSI** → одна функция в `analysis/technical.py`, импортировать в `market_context.py` и `price_fetcher.py`
2. **Markdown escaping** → `_escape_md()` в `formatters.py`
3. **Удалить мёртвый post_init** из `telegram_bot.py`
4. **chmod 600 .env** на VPS
5. **Добавить rate limit** на /run (cooldown 5 мин)
6. **Настроить cron-бэкап** SQLite

### Этап 4 — Команда /stats + еженедельный отчёт (Важно)

Новая команда `/stats` — сводная статистика:
- Всего проверено, % success/neutral/failure
- Средний result_pct
- Корреляция score vs result
- Топ/худшие акции

Еженедельный автоотчёт (воскресенье 10:00 UTC) через job_queue.

---

## 8. VPS DEPLOYMENT

```bash
# Сервис:
/etc/systemd/system/sp500bot.service

# Код:
/root/sp500-bounce-bot/

# Обновление:
cd /root/sp500-bounce-bot
git pull
pip install -r requirements.txt
systemctl restart sp500bot

# Логи:
journalctl -u sp500bot -f --no-pager

# БД:
/root/sp500-bounce-bot/bot_data.db
```

---

## 9. ЗАВИСИМОСТИ (requirements.txt)

```
yfinance>=0.2.31
pandas>=2.0
numpy>=1.24
python-telegram-bot[job-queue]>=20.0
openai>=1.0
requests>=2.31
python-dotenv>=1.0
beautifulsoup4>=4.12
lxml>=4.9
```

---

## 10. АРХИТЕКТУРНЫЕ РЕШЕНИЯ (принятые)

| Решение | Обоснование |
|---|---|
| **SQLite вместо PostgreSQL** | Один writer, ~2600 строк/год. WAL mode. Бэкап = cp файла. |
| **job_queue вместо APScheduler** | Встроен в python-telegram-bot. Не нужна внешняя зависимость. |
| **GPT-4o-mini вместо GPT-4o** | ~20x дешевле ($1-2/мес). Качество достаточное для отчётов. |
| **24h LLM кэш** | Одинаковые stocks+regime → тот же ответ. Экономия API. |
| **Режимный мультипликатор** | При bearish/panic снижает scores, уменьшая ложные сигналы. |
| **Menu setup в main.py (не post_init)** | post_init работает только с run_polling(), не с ручным lifecycle. |
| **Wikipedia + fallback** | Бесплатно, 7-дневный кэш. Fallback = 100 тикеров. |

---

## 11. СКОРИНГ (подробно)

### Technical Score (старт: 50, диапазон: 0-100)
- RSI < 25: +20, < 30: +15, < 35: +8, < 40: +3, > 65: -10
- Bullish RSI divergence: +12
- MACD bullish cross: +10, histogram rising: +6, histogram falling: -5
- BB below lower: +8, %B < 0.1: +5, squeeze: +3
- StochRSI < 0.1: +8, < 0.2: +5, < 0.3: +2
- Momentum deceleration (ROC5 > ROC10): +6, ROC5 < -10: -8
- Volume > 1.5x + drawdown > 15%: +8, > 1.2x: +3, < 0.5x: -5
- OBV up: +3, down: -2
- Above SMA200: +4, >20% below: -5, >10% below: -2
- Death Cross: -6
- Near S1 (2%): +4
- Near Fib 50/61.8% (3%): +5
- ATR% > 8%: -4

### Fundamental Score (старт: 50)
- PE undervalued: +12, fair: +5, overvalued: -5, negative: -10
- Earnings growth > 20%: +12, > 0%: +6, < -30%: -10, < 0%: -5
- Revenue growth > 10%: +8, > 0%: +4, < -10%: -6, < 0%: -3
- Margin > 20%: +8, > 10%: +4, < 0%: -8
- D/E < 50: +6, < 100: +3, > 200: -6, > 150: -3
- FCF positive: +6, negative: -6
- ROE > 20%: +5, > 10%: +2, < 0%: -5
- Current ratio > 2.0: +3, > 1.5: +1, < 1.0: -4
- Dividend > 0: +2
- Analyst upside > 30%: +5, > 15%: +3

### Sentiment Score (старт: 50)
- Analyst buy: +15, hold: +3, sell: -12
- Insider bullish: +15, bearish: -10
- News positive: +8, negative: -6
- Earnings within 7d: -12, within 14d: -5, none: +5

### Market Score (старт: 50)
- Regime bullish: +20, neutral: +5, weak: -5, bearish: -12, panic: -18
- VIX elevated: +10, high_fear: +5, panic: -8, complacent: +2
- Sector strong: +5, weak: -3

### Composite
```
composite = tech×0.40 + fund×0.30 + sent×0.15 + market×0.15
clamp(0, 100)
≥75 = high, ≥60 = medium_high, ≥45 = medium, ≥30 = low, <30 = very_low
```

### Режимный мультипликатор (main.py)
```python
REGIME_MULTIPLIER = {"bullish": 1.0, "neutral": 0.95, "weak": 0.88, "bearish": 0.80, "panic": 0.70}
```

---

## 12. LLM ПРОМПТ (llm/analyst.py)

Системный промпт на русском. Роль: финансовый аналитик, swing-трейдинг.
Правила: анализировать только предоставленные данные, объяснять с цифрами, указывать риски, давать вход/стоп/цель, отмечать красные флаги, горизонт 2-6 недель.
Модель: GPT-4o-mini, temperature: 0.2, max_tokens: 4000.
Кэш: 24h, ключ = MD5(symbols + regime).

---

## 13. TELEGRAM КОМАНДЫ

| Команда | Файл:функция | Описание |
|---|---|---|
| `/run` | telegram_bot.py:cmd_run | Полный анализ (5-10 мин) |
| `/report` | telegram_bot.py:cmd_report | Последний отчёт из SQLite |
| `/analyze TICKER` | telegram_bot.py:cmd_analyze | Анализ одной акции |
| `/watchlist [add/remove TICKER]` | telegram_bot.py:cmd_watchlist | Управление watchlist |
| `/status` | telegram_bot.py:cmd_status | Uptime, последний запуск |
| `/help` | telegram_bot.py:cmd_help | Справка |

Все команды проходят `_is_authorized()` → проверка chat_id в AUTHORIZED_CHAT_IDS.
Тикеры валидируются regex `^[A-Z]{1,5}(-[A-Z]{1,2})?$`.
Анализ выполняется через `run_in_executor()` (в отдельном потоке, не блокирует event loop).

---

## 14. ИЗВЕСТНЫЕ ОСОБЕННОСТИ

1. **Wikipedia может вернуть 403** если без User-Agent → добавлен в sp500_list.py
2. **pandas FutureWarning** на `float(series.iloc[-1])` → используется `_to_float()` с `.item()`
3. **post_init callback** работает ТОЛЬКО с `run_polling()`, НЕ с ручным `initialize()/start()` → setup в main.py
4. **ATVI (Activision Blizzard)** делистирован → заменён на UBER в fallback
5. **Finnhub free tier** — 60 req/min, задержка 1.1 сек между запросами
6. **httpx логи** содержат bot token в URL → подавлены на WARNING level
7. **Язык UI** — русский (Cyrillic), все сообщения и промпты на русском

---

## 15. КАК НАЧАТЬ РАБОТУ

```
1. Прочитать этот файл
2. Изучить docs/TZ_Extended_v2.md — расширенное ТЗ с приоритетами
3. Начать с Этапа 1 (раздел 7) — логирование рекомендаций
4. Код: D:\Claude Code doc\sp500-bounce-bot\
5. Git: https://github.com/IDonRumata/sp500-bounce-bot
6. VPS deploy: git pull → pip install -r requirements.txt → systemctl restart sp500bot
```
