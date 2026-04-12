# ТЕХНИЧЕСКОЕ ЗАДАНИЕ v2.0 (расширенное)
# Stock Screener Analytics System + Statistics Collector Bot

> **Версия:** 2.0
> **Дата:** Март 2026
> **Статус:** Расширенное ТЗ на основе v1.0 + аудит текущей реализации
> **Стек:** Python 3.11+, VPS (Ubuntu 22.04), Git, SQLite → PostgreSQL

---

## 0. Статус текущей реализации

### Что уже реализовано и работает

| Компонент | Статус | Детали |
|---|---|---|
| Telegram-бот (python-telegram-bot v22.6) | **Работает** | Команды: /run, /report, /analyze, /watchlist, /status, /help |
| Расписание (job_queue) | **Работает** | Пн/Ср/Пт 08:00 UTC, автоотчёты в Telegram |
| Меню-кнопка | **Работает** | BotCommand + MenuButtonCommands |
| Технический анализ (20+ индикаторов) | **Работает** | RSI, MACD, BB, StochRSI, ATR, SMA, Pivot, Fibonacci, OBV, ROC |
| Фундаментальный анализ | **Работает** | P/E, EPS, Revenue, Margins, D/E, FCF, ROE, качество A-F |
| Сентимент (Finnhub) | **Работает** | Новости, аналитики, инсайдеры, календарь отчётностей |
| Composite scoring (0-100) | **Работает** | Tech 40% + Fund 30% + Sent 15% + Market 15% |
| Market context (SPY, VIX, секторы) | **Работает** | Режим рынка + мультипликатор |
| LLM анализ (GPT-4o-mini) | **Работает** | 24h кэш, prompt на русском |
| SQLite хранилище | **Работает** | reports, watchlist, cache |
| Авторизация | **Работает** | AUTHORIZED_CHAT_IDS, regex-валидация тикеров |
| Подавление токенов в логах | **Работает** | httpx logger → WARNING |
| Режимный мультипликатор | **Работает** | bullish:1.0, neutral:0.95, weak:0.88, bearish:0.80, panic:0.70 |
| systemd-сервис | **Работает** | sp500bot.service на VPS |

### Что НЕ реализовано (из оригинального ТЗ v1.0)

| Компонент | Статус | Приоритет |
|---|---|---|
| PostgreSQL (замена SQLite) | **Не начато** | Средний |
| Логирование рекомендаций для статистики | **Частично** | Критично |
| Cron-скрипт проверки результатов | **Не начато** | Критично |
| Baserow/Flask-дашборд | **Не начато** | Желательно |
| Недельный Telegram-отчёт по итогам | **Не начато** | Желательно |

---

## 1. Введение и контекст

Бот-скринер акций S&P 500 работает на VPS (Ubuntu 22.04), анализирует рынок 3 раза в неделю и отправляет отчёты в Telegram. Бот выполняет глубокий многофакторный анализ: технические индикаторы (RSI, MACD, Bollinger Bands, Fibonacci, pivot points), фундаментальные показатели (P/E, EPS, margins, FCF), сентимент (новости, инсайдеры, аналитики) и контекст рынка (SPY, VIX, секторные ETF).

**Текущая архитектура:**
```
sp500-bounce-bot/
├── main.py                  # Entry point + job_queue scheduler
├── config.py                # Настройки из .env
├── requirements.txt
│
├── data/
│   ├── sp500_list.py        # S&P 500 список (Wikipedia + fallback)
│   ├── price_fetcher.py     # yfinance: OHLCV + fundamentals
│   ├── finnhub_client.py    # Finnhub: новости, инсайдеры, отчётности
│   └── market_context.py    # SPY, VIX, секторные ETF
│
├── analysis/
│   ├── technical.py         # 20+ технических индикаторов (358 строк)
│   ├── fundamental.py       # Фундаментальный анализ (204 строки)
│   └── sentiment.py         # Агрегация сентимента (58 строк)
│
├── scoring/
│   └── scorer.py            # Композитный скоринг 0-100 (340 строк)
│
├── bot/
│   ├── telegram_bot.py      # Обработчики команд + scheduled jobs
│   └── formatters.py        # Форматирование для Telegram
│
├── llm/
│   └── analyst.py           # GPT-4o-mini с 24h кэшем
│
└── storage/
    └── database.py          # SQLite: reports, watchlist, cache
```

**Проблема:** после отправки отчёта данные сохраняются в SQLite, но без структуры для последующей оценки. Невозможно ответить:
- Работает ли алгоритм скринера на практике?
- Какой процент рекомендаций оказался прибыльным?
- Какие параметры коррелируют с успехом?

---

## 2. Цели системы (приоритизированные)

| # | Цель | Результат | Приоритет |
|---|---|---|---|
| 1 | Логирование рекомендаций | Каждый запуск сохраняет структурированные данные по каждой акции | Критично |
| 2 | Автопроверка результатов | Через N дней система проверяет цену и присваивает статус | Критично |
| 3 | Исправление найденных уязвимостей | Устранение технических проблем (см. раздел 8) | Критично |
| 4 | Статистические отчёты в Telegram | Еженедельная сводка по результатам | Важно |
| 5 | Дашборд (Baserow/Flask) | Визуализация истории и статистики | Желательно |
| 6 | Миграция SQLite → PostgreSQL | Масштабируемость и надёжность | Желательно |

---

## 3. Критерий оценки сделки

### 3.1. Основной критерий (Вариант А — простой)

Через фиксированное количество рабочих дней после рекомендации система запрашивает текущую цену и сравнивает с ценой на момент сигнала.

| Статус | Условие | Пояснение |
|---|---|---|
| success | +3% и выше | Рекомендация сработала |
| neutral | от -3% до +3% | В пределах шума |
| failure | -3% и ниже | Не сработало |
| pending | < N дней | Ещё не время проверять |

**Период проверки:** 10 рабочих дней (примерно 2 календарные недели).

### 3.2. Расширенные критерии (v2)

| Вариант | Описание | Когда внедрять |
|---|---|---|
| Б — технический | Цена пересекла SMA50 снизу вверх | Итерация 2 |
| В — защитный | Падение ещё на 5%+ = явный провал (ранний стоп) | Итерация 1 (реализовать сразу) |
| Г — max drawdown/max gain | Максимум и минимум за период | Итерация 2 |

**Конфигурация (все пороги в .env / config.py):**
```python
CHECK_PERIOD_DAYS = 10          # Рабочих дней
SUCCESS_THRESHOLD_PCT = 3.0     # Минимум для success
FAILURE_THRESHOLD_PCT = -3.0    # Максимум для failure
EARLY_STOP_PCT = -5.0           # Ранний стоп (Вариант В)
```

---

## 4. Архитектура системы расширения

### 4.1. Компонент 1 — Расширенное логирование (модификация бота)

**Текущее состояние:** `storage/database.py` сохраняет отчёт как один JSON-блоб в таблицу `reports`. Это не позволяет делать SQL-запросы по отдельным акциям.

**Требуемые изменения:**

1. Добавить таблицу `recommendations` в SQLite (см. раздел 5.1)
2. Добавить таблицу `market_snapshots` (см. раздел 5.2)
3. В `main.py` → `run_full_analysis()`: после scoring добавить вызов `save_recommendations()`
4. Запись происходит **до** отправки в Telegram (защита от потери данных)
5. При ошибке записи в БД — бот продолжает работу, логирует ошибку

**Функция save_recommendations():**
```python
def save_recommendations(stocks: list[dict], market_ctx: dict, report_date: str):
    """Сохраняет каждую рекомендацию отдельной строкой."""
    conn = get_connection()
    try:
        for stock in stocks:
            tech = stock["technical"]
            fund = stock["fundamental"]
            sent = stock["sentiment"]
            scores = stock["scores"]

            conn.execute("""
                INSERT INTO recommendations
                (signal_date, ticker, price_at_signal, rsi, macd_direction,
                 divergence, composite_score, bounce_probability,
                 pe_ratio, eps_growth, quality_grade, drawdown_pct,
                 market_regime, sector, check_date, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (
                report_date,
                tech.get("symbol"),
                tech.get("current_price"),
                tech.get("rsi"),
                "up" if tech.get("macd_histogram_rising") else "down",
                bool(tech.get("rsi_bullish_divergence")),
                scores.get("composite_score"),
                scores.get("bounce_probability"),
                fund.get("pe_trailing"),
                fund.get("eps_growth_expected"),
                fund.get("quality_grade"),
                tech.get("drawdown_pct"),
                market_ctx.get("regime"),
                fund.get("sector", "Unknown"),
                _calculate_check_date(report_date, CHECK_PERIOD_DAYS),
            ))
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to save recommendations: {e}")
    finally:
        conn.close()
```

### 4.2. Компонент 2 — Скрипт проверки результатов

**Новый файл:** `evaluation/check_results.py`

Запускается ежедневно через systemd timer или telegram job_queue (предпочтительно — уже есть инфраструктура).

**Логика:**
1. SELECT из `recommendations` WHERE `status = 'pending'` AND `check_date <= today`
2. Для каждой записи: запросить текущую цену через yfinance
3. Рассчитать `result_pct = (current_price - price_at_signal) / price_at_signal * 100`
4. Присвоить статус: success / neutral / failure
5. UPDATE запись в БД
6. Опционально: ранний стоп — ежедневная проверка pending записей, если упали на 5%+

**Интеграция с ботом:**
```python
# В main.py, добавить ещё одну daily job:
app.job_queue.run_daily(
    check_results_job,
    time=dtime(hour=18, minute=0),  # 18:00 UTC (после закрытия рынка)
    days=(0, 1, 2, 3, 4),          # Пн-Пт
    name="check_results",
)
```

### 4.3. Компонент 3 — Статистический отчёт в Telegram

**Новая команда:** `/stats` — показать сводную статистику.

**Еженедельный автоотчёт** (воскресенье 10:00 UTC):
```
ЕЖЕНЕДЕЛЬНАЯ СТАТИСТИКА СКРИНЕРА

Период: 01.03 — 07.03.2026
Проверено рекомендаций: 12

Результаты:
  Успешных: 7 (58.3%)
  Нейтральных: 3 (25.0%)
  Провальных: 2 (16.7%)

Средний результат: +2.8%
Лучшая: AAPL +8.2%
Худшая: META -4.1%

Топ по Score vs результат:
  Score >= 70: 4/5 успешных (80%)
  Score 60-69: 2/4 успешных (50%)
  Score < 60: 1/3 успешных (33%)

Ожидают проверки: 15
```

### 4.4. Компонент 4 — Дашборд (отложено)

**Рекомендация:** начать с Telegram-команд `/stats` и еженедельных отчётов. Flask-дашборд реализовать когда накопится 3+ месяца данных.

**Обоснование:** Baserow требует отдельного сервиса и синхронизации. Flask на том же VPS добавляет сложность (nginx, SSL). Telegram-интерфейс достаточен для первых месяцев.

---

## 5. Схема данных

### 5.1. Таблица recommendations

```sql
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date TEXT NOT NULL,              -- YYYY-MM-DD
    ticker TEXT NOT NULL,                   -- AAPL, MSFT, etc.
    price_at_signal REAL NOT NULL,          -- Цена на момент сигнала

    -- Технические показатели
    rsi REAL,
    macd_direction TEXT,                    -- up / down
    divergence INTEGER DEFAULT 0,          -- 0/1 бычья дивергенция
    stoch_rsi REAL,
    bb_pct_b REAL,
    drawdown_pct REAL,

    -- Скоринг
    composite_score REAL NOT NULL,
    bounce_probability TEXT,                -- high/medium_high/medium/low/very_low
    technical_score REAL,
    fundamental_score REAL,
    sentiment_score REAL,
    market_score REAL,

    -- Фундаментальные
    pe_ratio REAL,
    eps_growth REAL,
    quality_grade TEXT,                     -- A/B/C/D/F
    sector TEXT,

    -- Контекст рынка
    market_regime TEXT,                     -- bullish/neutral/weak/bearish/panic

    -- Результат проверки
    check_date TEXT,                        -- Когда проверять (signal + N рабочих дней)
    price_at_check REAL,                    -- Цена при проверке (NULL пока pending)
    result_pct REAL,                        -- Изменение в % (NULL пока pending)
    max_price_in_period REAL,               -- Макс. цена за период (для анализа)
    min_price_in_period REAL,               -- Мин. цена за период (для стоп-анализа)
    status TEXT DEFAULT 'pending',          -- pending/success/neutral/failure

    -- Метаданные
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_rec_status ON recommendations(status);
CREATE INDEX IF NOT EXISTS idx_rec_date ON recommendations(signal_date);
CREATE INDEX IF NOT EXISTS idx_rec_ticker ON recommendations(ticker);
CREATE INDEX IF NOT EXISTS idx_rec_check ON recommendations(check_date, status);
```

### 5.2. Таблица market_snapshots

```sql
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT UNIQUE NOT NULL,       -- YYYY-MM-DD
    spy_price REAL,
    spy_rsi REAL,
    spy_trend TEXT,                         -- up/down
    spy_above_sma50 INTEGER,
    spy_above_sma200 INTEGER,
    vix_value REAL,
    vix_label TEXT,                         -- complacent/normal/elevated/high_fear/panic
    market_regime TEXT,                     -- bullish/neutral/weak/bearish/panic
    total_candidates INTEGER,              -- Сколько прошло пре-фильтр
    total_recommended INTEGER,             -- Сколько попало в отчёт
    created_at TEXT DEFAULT (datetime('now'))
);
```

---

## 6. Вопрос SQLite vs PostgreSQL

### Анализ

| Критерий | SQLite (текущий) | PostgreSQL |
|---|---|---|
| Установка | Уже работает | Нужно ставить, настраивать |
| Бэкап | cp файла | pg_dump, cron |
| Concurrent writes | WAL mode, 1 writer | Полная конкурентность |
| Объём данных | До ~10GB нормально | Без ограничений |
| Сложность | Нулевая | Средняя |
| Baserow интеграция | Через sync-скрипт | Прямое подключение |

### Рекомендация

**Оставить SQLite на данном этапе.** Причины:
1. Один бот = один writer. WAL mode достаточен.
2. Ожидаемый объём данных: ~50 рекомендаций/неделю × 52 недели = 2600 строк/год. SQLite справляется с миллионами строк.
3. Бэкап: простой `cp bot_data.db bot_data.db.bak` в cron.
4. Миграция на PostgreSQL при необходимости (Baserow, несколько ботов) — straightforward через sqlalchemy.

**Когда мигрировать:** если появятся параллельные writers (второй бот, веб-дашборд с записью) или нужна прямая интеграция с Baserow.

---

## 7. Этапы разработки (обновлённые)

| # | Этап | Задачи | Оценка | Приоритет |
|---|---|---|---|---|
| 1 | Расширенное логирование | Создать таблицы recommendations + market_snapshots. Добавить save_recommendations() в pipeline. Тесты. | 1 день | Критично |
| 2 | Скрипт проверки результатов | check_results_job() в job_queue. yfinance для текущих цен. Обновление статусов. | 1 день | Критично |
| 3 | Исправление уязвимостей | RSI-рассогласование, Markdown escaping, rate limiting, бэкапы (раздел 8) | 1-2 дня | Критично |
| 4 | Команда /stats + авто-отчёт | Telegram-команда со сводной статистикой. Еженедельный отчёт. | 1 день | Важно |
| 5 | Расширенная оценка | max/min за период, ранний стоп, корреляция score-vs-result | 1-2 дня | Важно |
| 6 | Flask-дашборд (опц.) | Веб-интерфейс с Plotly, фильтры, графики | 3-5 дней | Желательно |

---

## 8. Аудит безопасности и уязвимости

### 8.1. КРИТИЧНЫЕ

#### 8.1.1. Несогласованность RSI между модулями

**Проблема:** Три разных реализации RSI:
- `analysis/technical.py` — EWM (экспоненциальное сглаживание, span=period)
- `data/market_context.py` — Rolling mean (простое скользящее среднее)
- `data/price_fetcher.py` — EWM (другая формула, com=period-1)

**Влияние:** Один и тот же тикер может получить RSI=32 в pre-filter (price_fetcher) и RSI=38 в deep analysis (technical). Акция может пройти или не пройти фильтр в зависимости от модуля.

**Решение:** Унифицировать RSI в одну функцию в `analysis/technical.py` и импортировать её во все модули:
```python
# analysis/technical.py — единственная реализация
def calc_rsi(series: pd.Series, period: int = 14) -> float | None:
    """Wilder's smoothing RSI (стандарт индустрии)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return round(float(val), 2) if pd.notna(val) else None
```

#### 8.1.2. Отсутствие Markdown-экранирования

**Проблема:** `bot/formatters.py` вставляет данные из yfinance/Finnhub в Markdown без экранирования. Символы `*`, `_`, `` ` ``, `[`, `]` в названиях компаний или новостях могут сломать форматирование или — при malicious input — вызвать XSS-подобные эффекты в Telegram.

**Пример:** Компания "Under_Armour" — подчёркивания интерпретируются как italic.

**Решение:** Добавить функцию экранирования:
```python
def _escape_md(text: str) -> str:
    """Экранирование спецсимволов Telegram MarkdownV1."""
    for ch in ('*', '_', '`', '[', ']'):
        text = text.replace(ch, '\\' + ch)
    return text
```

#### 8.1.3. Bot token в .env без защиты файла

**Проблема:** `.env` файл на VPS содержит:
- `TELEGRAM_BOT_TOKEN` — полный доступ к боту
- `OPENAI_API_KEY` — расходы на API
- `FINNHUB_API_KEY` — лимиты API

**Решение:**
```bash
# На VPS:
chmod 600 /root/sp500-bounce-bot/.env
chown root:root /root/sp500-bounce-bot/.env
```
Также добавить `.env` в `.gitignore` (если не добавлен).

### 8.2. СРЕДНИЕ

#### 8.2.1. Нет rate limiting для Telegram-команд

**Проблема:** Авторизованный пользователь может отправлять `/run` каждую секунду, создавая нагрузку на yfinance/Finnhub/OpenAI.

**Решение:** Добавить cooldown:
```python
_last_run_time = {}
COOLDOWN_SECONDS = 300  # 5 минут

async def cmd_run(update, context):
    chat_id = update.effective_chat.id
    now = datetime.now().timestamp()
    if chat_id in _last_run_time and now - _last_run_time[chat_id] < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - _last_run_time[chat_id]))
        await _safe_send(context, chat_id, f"Подождите {remaining} сек.")
        return
    _last_run_time[chat_id] = now
    # ... rest of command
```

#### 8.2.2. Wikipedia scraping хрупкий

**Проблема:** `data/sp500_list.py` парсит HTML-таблицу Wikipedia. Любое изменение структуры ломает парсинг. Fallback — только 100 тикеров из ~500.

**Решение:** Добавить альтернативные источники:
1. GitHub: datahub.io S&P 500 CSV (обновляется регулярно)
2. slickcharts.com/sp500 (JSON API)
3. Расширить fallback до полных 500 тикеров (или хранить последний успешный список в БД)

#### 8.2.3. Нет бэкапов БД

**Проблема:** `bot_data.db` — единственная копия всех данных. Потеря VPS = потеря всей истории.

**Решение:**
```bash
# Cron на VPS (ежедневно в 02:00):
0 2 * * * cp /root/sp500-bounce-bot/bot_data.db /root/backups/bot_data_$(date +\%Y\%m\%d).db
# Хранить последние 30 бэкапов:
0 3 * * * find /root/backups/ -name "bot_data_*.db" -mtime +30 -delete
```

Желательно: rsync на внешнее хранилище или S3.

#### 8.2.4. Finnhub rate limiting может занять 30+ минут

**Проблема:** Finnhub free tier: 60 req/min. Для 40 кандидатов × 4 запроса = 160 запросов. При задержке 1.1 сек/запрос = ~3 минуты, но с 429-retry может растянуться.

**Решение:**
1. Кэширование Finnhub данных на 12 часов (sentiment меняется медленно)
2. Batch-обработка: параллельные запросы с семафором (max 5 concurrent)
3. Отказ от news_sentiment в pre-filter (оставить только для deep analysis)

#### 8.2.5. Нет graceful shutdown

**Проблема:** При `systemctl restart` бот может прервать анализ в процессе, оставив незавершённые записи.

**Решение:** Добавить signal handling:
```python
import signal

_shutdown_event = asyncio.Event()

def _signal_handler(sig, frame):
    logger.info(f"Received signal {sig}, initiating graceful shutdown...")
    _shutdown_event.set()

signal.signal(signal.SIGTERM, _signal_handler)
```

### 8.3. НИЗКИЕ

#### 8.3.1. NumpyEncoder неполный

**Проблема:** `storage/database.py` → `NumpyEncoder` обрабатывает только `np.integer`, `np.floating`, `np.ndarray`, `np.bool_`. Другие numpy-типы (np.str_, pd.Timestamp) вызовут `TypeError`.

**Решение:** Расширить encoder:
```python
class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (pd.Timestamp, datetime)):
            return obj.isoformat()
        if pd.isna(obj):
            return None
        return super().default(obj)
```

#### 8.3.2. Сектор PE медианы статичны

**Проблема:** `analysis/fundamental.py` содержит хардкод сектор P/E медиан (Technology: 28, Healthcare: 22, etc.). Эти значения устаревают.

**Решение (долгосрочное):** Раз в месяц пересчитывать медианы на основе реальных данных S&P 500 (через yfinance batch).

**Решение (краткосрочное):** Вынести в config.py, чтобы можно было обновлять без изменения кода.

#### 8.3.3. post_init мёртвый код

**Проблема:** `bot/telegram_bot.py` содержит функцию `post_init()` и регистрацию через `.post_init(post_init)` в `create_bot_application()`. Но setup перенесён в `main.py` после `app.start()`. Код post_init никогда не вызывается (дублирует setup в main.py).

**Решение:** Удалить `post_init()` из `telegram_bot.py` и `.post_init(post_init)` из builder.

#### 8.3.4. Telegram 4096 char limit

**Проблема:** `_safe_send()` разбивает по `\n\n`, но если один блок > 4000 символов (возможно в LLM response), сообщение обрежется.

**Решение:** Добавить hard split по 4000 символов с сохранением целостности строк:
```python
if len(part) > max_len:
    # Split by single newlines within the part
    lines = part.split('\n')
    # ... group lines into chunks <= max_len
```

---

## 9. Рекомендации по улучшению

### 9.1. Замена GPT-4o-mini на Claude (рекомендация)

**Текущее:** GPT-4o-mini, ~$1-2/мес с 24h кэшем.

**Вариант:** Claude 3.5 Haiku через Anthropic API — сравнимая стоимость, лучше в аналитических задачах. Или Claude 3.5 Sonnet для ключевых отчётов.

**Оценка:** Не критично. GPT-4o-mini работает адекватно. Рассмотреть при неудовлетворительном качестве анализа.

### 9.2. Добавить /compare команду

```
/compare AAPL MSFT GOOGL
```
Сравнительная таблица нескольких акций по ключевым метрикам. Полезно для выбора между кандидатами.

### 9.3. Alert-система

При появлении акции с score >= 80 — немедленное уведомление (не ждать расписания). Через job_queue.run_repeating() с интервалом 4 часа в рабочее время.

### 9.4. Хранение DataFrame в кэше

Сейчас yfinance скачивает 6 месяцев OHLCV для всех 500 тикеров при каждом запуске. Кэширование на 4 часа сэкономит время и трафик.

### 9.5. Health-check endpoint

Простой HTTP endpoint (или Telegram /health) для мониторинга:
- Uptime
- Последний успешный запуск
- Ошибки за 24 часа
- Размер БД
- Свободное место на диске

---

## 10. Конфигурационные параметры (полный список)

### Существующие (config.py / .env)

| Параметр | Значение | Описание |
|---|---|---|
| TELEGRAM_BOT_TOKEN | *** | Токен бота |
| TELEGRAM_CHAT_ID | *** | ID чата для отчётов |
| OPENAI_API_KEY | *** | Ключ OpenAI |
| OPENAI_MODEL | gpt-4o-mini | Модель LLM |
| FINNHUB_API_KEY | *** | Ключ Finnhub |
| SCHEDULE_DAYS | mon,wed,fri | Дни расписания |
| SCHEDULE_HOUR | 8 | Час UTC |
| SCHEDULE_MINUTE | 0 | Минута |
| MAX_PRICE | 200 | Макс. цена акции |
| MIN_DRAWDOWN | -10 | Мин. просадка % |
| MIN_COMPOSITE_SCORE | 58 | Порог score |
| TOP_PICKS_COUNT | 7 | Макс. акций в отчёте |
| PRE_FILTER_RSI | 45 | RSI порог пре-фильтра |
| AUTHORIZED_CHAT_IDS | CHAT_ID | Авторизованные пользователи |

### Новые (добавить)

| Параметр | Значение по умолчанию | Описание |
|---|---|---|
| CHECK_PERIOD_DAYS | 10 | Рабочих дней для проверки |
| SUCCESS_THRESHOLD_PCT | 3.0 | Порог успеха % |
| FAILURE_THRESHOLD_PCT | -3.0 | Порог провала % |
| EARLY_STOP_PCT | -5.0 | Ранний стоп % |
| WEEKLY_REPORT_DAY | sun | День недельного отчёта |
| WEEKLY_REPORT_HOUR | 10 | Час UTC |
| FINNHUB_CACHE_TTL | 43200 | Кэш Finnhub (12 часов) |
| CMD_COOLDOWN_SEC | 300 | Cooldown между /run |

---

## 11. Критерии готовности (Definition of Done)

### Этап 1 — Логирование
- [ ] Каждый запуск /run создаёт записи в `recommendations`
- [ ] Каждый запуск создаёт запись в `market_snapshots`
- [ ] Данные корректны и совпадают с Telegram-отчётом
- [ ] Ошибка записи в БД не останавливает отправку в Telegram
- [ ] Индексы созданы, запросы < 100ms

### Этап 2 — Проверка результатов
- [ ] Job check_results запускается ежедневно в 18:00 UTC Пн-Пт
- [ ] Записи с истёкшим периодом получают статус и цену
- [ ] Ранний стоп работает для -5%+
- [ ] Можно запустить вручную: `/check_results` (admin-only)

### Этап 3 — Уязвимости
- [ ] RSI унифицирован в одну функцию
- [ ] Markdown экранирование добавлено
- [ ] .env защищён (chmod 600)
- [ ] Бэкап БД настроен в cron
- [ ] Мёртвый код post_init удалён
- [ ] Rate limit на /run добавлен

### Этап 4 — Статистика
- [ ] Команда /stats показывает сводку
- [ ] Еженедельный автоотчёт работает
- [ ] Корреляция score vs result отображается

---

## 12. Риски и ограничения

| Риск | Вероятность | Влияние | Митигация |
|---|---|---|---|
| yfinance API изменения | Средняя | Высокое | Fallback на Alpha Vantage (бесплатный план) |
| Потеря данных БД | Низкая | Критическое | Ежедневные бэкапы, rsync |
| Finnhub rate limit | Средняя | Среднее | 12h кэш, сокращение запросов |
| Wikipedia структура изменится | Средняя | Низкое | Альтернативные источники списка |
| Статистика используется как инвест. рекомендация | Средняя | Юридическое | Дисклеймер в каждом сообщении |
| Выборка слишком мала | Высокая (старт) | Среднее | Минимум 3 месяца до выводов |
| VPS downtime | Низкая | Среднее | Systemd auto-restart, health-check |

---

## 13. Юридический дисклеймер

Данная система является внутренним инструментом оценки торгового алгоритма. Никакая статистика успешности рекомендаций НЕ является инвестиционной рекомендацией. Публикация данных как "доказательства эффективности" для привлечения инвесторов требует лицензии в большинстве юрисдикций.

Каждое сообщение бота содержит дисклеймер: "Не является инвестиционной рекомендацией."
