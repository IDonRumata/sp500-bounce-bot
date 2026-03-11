# HANDOFF v2 — S&P 500 Bounce Analyzer Bot
# Документ для переноса разработки в новый чат

**Дата:** 2026-03-11
**Репозиторий:** https://github.com/IDonRumata/sp500-bounce-bot
**VPS:** Ubuntu 22.04 | 185.229.251.166 | systemd-сервис `sp500bot`
**Python:** 3.13 | venv в `/root/sp500-bounce-bot/venv`

---

## 1. ЧТО УЖЕ СДЕЛАНО (текущее состояние)

### 1.1 Бот полностью работает на VPS
- Запущен как systemd-сервис, автозапуск при перезагрузке
- Отправляет авто-отчёты Пн/Ср/Пт 08:00 UTC
- Все команды работают: `/run`, `/report`, `/analyze`, `/watchlist`, `/stats`, `/status`, `/help`

### 1.2 Структура проекта (3392 строки кода)

```
sp500-bounce-bot/
├── main.py                  (343)  — точка входа, job_queue, планировщик
├── config.py                (84)   — все настройки через .env
├── analysis/
│   ├── technical.py         (372)  — 12 теханализ индикаторов + calc_rsi() (публичный)
│   ├── fundamental.py       (204)  — P/E, EPS, margins, FCF, качество
│   └── sentiment.py         (58)   — новости, инсайдеры, аналитики
├── scoring/
│   └── scorer.py            (339)  — скоринг 0-100, 4 группы весов
├── data/
│   ├── price_fetcher.py     (178)  — yfinance batch/single, quick_prefilter()
│   ├── market_context.py    (165)  — SPY, VIX, 11 секторов
│   ├── finnhub_client.py    (252)  — новости, инсайдеры, консенсус
│   └── sp500_list.py        (67)   — список S&P 500 с Wikipedia + fallback
├── llm/
│   └── analyst.py           (198)  — промпт + OpenAI API (gpt-4o-mini)
├── bot/
│   ├── telegram_bot.py      (321)  — все хендлеры, job callbacks
│   └── formatters.py        (260)  — форматирование Telegram-сообщений
├── storage/
│   └── database.py          (408)  — SQLite, WAL mode, все таблицы
└── evaluation/
    └── check_results.py     (117)  — проверка результатов через yfinance
```

### 1.3 База данных (SQLite, WAL mode)

**Таблицы:**

```sql
reports          — отчёты целиком (JSON), история
watchlist        — пользовательский watchlist
cache            — кэш данных с TTL
recommendations  — каждая рекомендация отдельной строкой (28 полей):
                   ticker, signal_date, price_at_signal, rsi, composite_score,
                   check_date, price_at_check, result_pct, max_price, min_price,
                   status (pending/success/neutral/failure)
market_snapshots — срез рынка на момент отчёта (VIX, SPY RSI, режим)
```

### 1.4 Скоринг (0-100, взвешенный)

| Группа | Вес | Что считает |
|--------|-----|-------------|
| Technical | 40% | RSI, MACD, BB, SMA, дивергенция, volume |
| Fundamental | 30% | P/E, EPS рост, маржа, долг, FCF |
| Sentiment | 15% | новости, аналитики, инсайдеры |
| Market | 15% | режим рынка, сектор, VIX |

Порог для рекомендации: `composite_score >= 58` (из .env `MIN_COMPOSITE_SCORE`)

### 1.5 Пайплайн `/run`

```
1. Получить список S&P 500 (~500 тикеров) с Wikipedia
2. Пре-фильтр: цена < $200, просадка < -10%, RSI < 45 → ~30-60 кандидатов
3. Глубокий теханализ каждого кандидата
4. Фундаментальный анализ (yfinance)
5. Сентимент-анализ (Finnhub)
6. Скоринг → топ-7
7. LLM-нарратив (gpt-4o-mini)
8. Сохранение отчёта + рекомендаций в БД
9. Отправка 3 сообщений в Telegram
```

### 1.6 Конфиг (.env переменные)

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
AUTHORIZED_CHAT_IDS=         # список через запятую, по умолчанию = CHAT_ID
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
FINNHUB_API_KEY=
SCHEDULE_DAYS=mon,wed,fri
SCHEDULE_HOUR=8
MAX_PRICE=200
MIN_DRAWDOWN=-10
MIN_COMPOSITE_SCORE=58
TOP_PICKS_COUNT=7
PRE_FILTER_RSI=45
CHECK_PERIOD_DAYS=10
SUCCESS_THRESHOLD_PCT=3.0
FAILURE_THRESHOLD_PCT=-3.0
WEEKLY_REPORT_DAY=sun
WEEKLY_REPORT_HOUR=10
CMD_COOLDOWN_SEC=300
```

### 1.7 Авто-расписание (job_queue)

| Задача | Расписание | Функция |
|--------|-----------|---------|
| Авто-анализ | Пн/Ср/Пт 08:00 UTC | `scheduled_report_job` |
| Проверка результатов | Пн-Пт 18:00 UTC | `check_results_job` |
| Еженедельная статистика | Вс 10:00 UTC | `weekly_stats_job` |

---

## 2. ЧТО НУЖНО ДОБАВИТЬ (5 новых функций)

---

### Функция 1: Бэктест исторических сигналов

**Суть:** Пользователь запускает `/backtest [days]` — бот берёт исторические данные
за `days` дней назад, прогоняет через текущие алгоритмы скоринга и показывает,
какой был бы результат, если бы рекомендации были даны тогда.

**Что создать:**

```
backtest/
├── __init__.py
└── engine.py           — логика бэктеста
```

**`backtest/engine.py`:**
```python
def run_backtest(lookback_days: int = 90) -> dict:
    """
    Для каждой торговой недели за последние lookback_days дней:
    1. Берём исторические цены на дату сигнала (через yfinance)
    2. Применяем quick_prefilter() и scorer с теми же весами
    3. Считаем, что случилось через CHECK_PERIOD_DAYS
    4. Возвращаем: win_rate, avg_return, best_trade, worst_trade, equity_curve
    """
```

**Новые функции в `storage/database.py`:**
```python
def save_backtest_run(run_id, params, results_json)
def get_backtest_runs() -> list
```

**Новая команда в `bot/telegram_bot.py`:**
```python
async def cmd_backtest(update, context):
    # /backtest       — за 90 дней по умолчанию
    # /backtest 180   — за 180 дней
```

**Новая функция в `bot/formatters.py`:**
```python
def format_backtest(result: dict) -> str:
    # Win rate, avg return, equity curve как текстовый график (▁▂▃▅▆▇)
```

**Новая таблица в БД:**
```sql
CREATE TABLE backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    lookback_days INTEGER,
    total_signals INTEGER,
    win_rate REAL,
    avg_return REAL,
    max_drawdown REAL,
    results_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

**Ограничения:**
- Не пересчитывать фундаментал (yfinance.info не даёт исторических данных)
- Считать только технический + рыночный скоринг (60% итогового)
- Лимит: не более 6 месяцев назад (yfinance ограничение точности)

---

### Функция 2: Портфельный трекер (P&L)

**Суть:** Пользователь может "принять" рекомендацию командой `/take AAPL 5` (5 акций),
и бот начинает отслеживать реальный P&L. `/portfolio` показывает текущее состояние.

**Что создать:**

```
portfolio/
├── __init__.py
└── tracker.py          — логика отслеживания P&L
```

**Новые таблицы в БД:**
```sql
CREATE TABLE portfolio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,              -- Telegram chat_id
    ticker TEXT NOT NULL,
    shares REAL NOT NULL,
    buy_price REAL NOT NULL,
    buy_date TEXT NOT NULL,
    rec_id INTEGER REFERENCES recommendations(id),
    status TEXT DEFAULT 'open',         -- open / closed
    sell_price REAL,
    sell_date TEXT,
    pnl_pct REAL,
    pnl_abs REAL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_portfolio_user ON portfolio(user_id, status);
```

**Новые команды Telegram:**
```
/take AAPL 5          — зафиксировать покупку 5 акций AAPL по текущей цене
/sell AAPL            — зафиксировать продажу (по текущей цене)
/portfolio            — показать открытые позиции с текущим P&L
/portfolio history    — история закрытых сделок
```

**`portfolio/tracker.py`:**
```python
def add_position(user_id, ticker, shares, price, rec_id=None) -> bool
def close_position(user_id, ticker, price) -> dict
def get_open_positions(user_id) -> list[dict]
def get_portfolio_summary(user_id) -> dict   # total_pnl, best, worst, sharpe
def refresh_prices(user_id) -> list[dict]    # актуальные цены через yfinance
```

**Форматтеры:**
```python
def format_portfolio(positions: list, summary: dict) -> str
def format_portfolio_history(trades: list) -> str
```

**Важно:** Позиция привязывается к Telegram `chat_id` — каждый пользователь видит только свой портфель (нужно для Multi-user).

---

### Функция 3: Веб-дашборд (графики, история)

**Суть:** Простой веб-интерфейс на Flask/FastAPI, доступный по `http://VPS_IP:8080`.
Показывает историю рекомендаций, статистику, equity curve.

**Что создать:**

```
dashboard/
├── __init__.py
├── app.py              — Flask/FastAPI приложение
├── templates/
│   ├── base.html
│   ├── index.html      — главная: последний отчёт + статистика
│   ├── history.html    — таблица всех рекомендаций с фильтрами
│   └── stats.html      — графики: equity curve, win rate по секторам
└── static/
    └── chart.js        — Chart.js для графиков
```

**Стек:** Flask + Jinja2 + Chart.js (минимальные зависимости, без React)

**Роуты:**
```
GET /                    — главная страница
GET /history             — история рекомендаций (таблица, фильтр по дате/тикеру)
GET /stats               — статистика + equity curve
GET /api/recommendations — JSON для графиков
GET /api/stats           — JSON статистики
```

**`dashboard/app.py` (базовая структура):**
```python
from flask import Flask, render_template, jsonify
from storage.database import get_stats_summary, get_pending_recommendations, get_last_report

app = Flask(__name__)

@app.route('/')
def index():
    report = get_last_report()
    stats = get_stats_summary()
    return render_template('index.html', report=report, stats=stats)

@app.route('/api/stats')
def api_stats():
    return jsonify(get_stats_summary())
```

**Запуск:**
```
# В systemd или отдельным процессом:
python -m dashboard.app --port 8080
```

**Безопасность:** Добавить HTTP Basic Auth или IP whitelist (VPS открывает только нужные порты).

**Зависимости:** `flask>=3.0` (добавить в requirements.txt)

---

### Функция 4: Алерты по watchlist в реальном времени

**Суть:** Раз в N минут (по умолчанию 15 мин в торговые часы) бот проверяет
тикеры из watchlist и шлёт алерт если цена изменилась больше чем на X% за день
ИЛИ RSI вошёл в зону перепроданности (< 30).

**Что создать:**

```
alerts/
├── __init__.py
└── watchlist_monitor.py
```

**`alerts/watchlist_monitor.py`:**
```python
def check_watchlist_alerts(user_id: str = None) -> list[dict]:
    """
    Для каждого тикера из watchlist:
    1. Fetch текущая цена + RSI (yfinance, 1d данные за 30 дней)
    2. Сравнить с ценой открытия дня (% change intraday)
    3. Если abs(change) > ALERT_PRICE_PCT или RSI < ALERT_RSI_OVERSOLD → алерт
    4. Избежать дублей: хранить last_alert_time в БД/памяти
    Returns: список алертов [{ticker, price, change_pct, rsi, reason}]
    """

def _is_market_open() -> bool:
    """NYSE часы: 9:30-16:00 ET (14:30-21:00 UTC), Пн-Пт"""
```

**Новые .env параметры:**
```env
ALERT_ENABLED=true
ALERT_INTERVAL_MIN=15       # как часто проверять (в минут)
ALERT_PRICE_PCT=3.0         # алерт если цена изменилась на X%
ALERT_RSI_OVERSOLD=30       # алерт если RSI < X
ALERT_RSI_OVERBOUGHT=75     # алерт если RSI > X (для шорт-сигналов)
```

**Новая таблица в БД:**
```sql
CREATE TABLE alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    alert_type TEXT NOT NULL,    -- price_drop / price_surge / rsi_oversold / rsi_overbought
    price REAL,
    change_pct REAL,
    rsi REAL,
    sent_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_alerts_user_ticker ON alert_history(user_id, ticker, sent_at);
```

**Подключение в `main.py`:**
```python
# Добавить в setup_jobs():
app.job_queue.run_repeating(
    watchlist_alert_job,
    interval=ALERT_INTERVAL_MIN * 60,
    first=60,
    name="watchlist_alerts"
)
```

**Новая job-функция в `bot/telegram_bot.py`:**
```python
async def watchlist_alert_job(context):
    """Проверяет watchlist и шлёт алерты всем авторизованным пользователям."""
    # Для multi-user: итерировать по всем user_id из БД
```

**Антиспам:** Не слать алерт по одному тикеру чаще 1 раза за 4 часа.

---

### Функция 5: Multi-user (несколько подписчиков)

**Суть:** Бот поддерживает несколько Telegram-пользователей с индивидуальными
настройками, своим watchlist и портфелем.

**Изменения в базе данных:**

```sql
-- Добавить таблицу users
CREATE TABLE users (
    chat_id TEXT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    is_active INTEGER DEFAULT 1,
    is_admin INTEGER DEFAULT 0,
    subscribed_reports INTEGER DEFAULT 1,  -- получать авто-отчёты
    alert_enabled INTEGER DEFAULT 1,
    registered_at TEXT DEFAULT (datetime('now')),
    last_active TEXT
);

-- Переделать watchlist: добавить user_id
ALTER TABLE watchlist ADD COLUMN user_id TEXT;
-- (или пересоздать таблицу с user_id)

-- alert_history и portfolio уже спроектированы с user_id
```

**Новые функции в `storage/database.py`:**
```python
def register_user(chat_id, username, first_name) -> bool
def get_user(chat_id) -> dict | None
def get_all_active_users() -> list[dict]
def get_subscribed_users() -> list[str]   # chat_id тех, кто subscribed_reports=1
def update_user_activity(chat_id)
```

**Новые команды Telegram:**
```
/subscribe    — подписаться на авто-отчёты
/unsubscribe  — отписаться
/settings     — показать текущие настройки пользователя
```

**Изменения в `bot/telegram_bot.py`:**
```python
# В каждом cmd_* добавить register_user(chat_id) для auto-registration
# cmd_help / cmd_start → auto-register

# Авторизация: расширить _is_authorized()
# Вместо AUTHORIZED_CHAT_IDS → проверять users.is_active в БД
# Или оставить AUTHORIZED_CHAT_IDS как whitelist для регистрации

# Изменить send_scheduled_report: рассылать всем subscribed_reports=1
async def send_scheduled_report(bot):
    users = get_subscribed_users()
    for chat_id in users:
        await _safe_send(bot, chat_id, ...)
```

**Команды администратора (is_admin=1):**
```
/admin users         — список пользователей
/admin broadcast MSG — разослать сообщение всем
/admin ban CHAT_ID   — заблокировать пользователя
```

---

## 3. РЕКОМЕНДУЕМЫЙ ПОРЯДОК РЕАЛИЗАЦИИ

```
Этап 1 (2-3 часа): Multi-user
  → Основа для всего остального (портфель, алерты — всё с user_id)
  → users таблица, auto-register, рассылка по подписчикам

Этап 2 (2-3 часа): Алерты по watchlist
  → watchlist_monitor.py, job в main.py, антиспам

Этап 3 (3-4 часа): Портфельный трекер
  → portfolio таблица, /take /sell /portfolio команды

Этап 4 (2-3 часа): Бэктест
  → backtest/engine.py, /backtest команда, текстовый equity curve

Этап 5 (4-6 часов): Веб-дашборд
  → Flask, 3 страницы, Chart.js, Basic Auth, отдельный systemd unit
```

---

## 4. ВАЖНЫЕ ТЕХНИЧЕСКИЕ ДЕТАЛИ

### Зависимости (requirements.txt — текущие)
```
python-telegram-bot[job-queue]
yfinance
pandas
numpy
openai
finnhub-python
python-dotenv
requests
flask        # добавить для дашборда
```

### Деплой на VPS
```bash
ssh root@185.229.251.166
cd /root/sp500-bounce-bot
git pull
pip install -r requirements.txt
systemctl restart sp500bot
systemctl status sp500bot    # должен быть active (running)
```

### Логи
```bash
journalctl -u sp500bot -n 100 --no-pager   # последние 100 строк
tail -f /root/sp500-bounce-bot/bot.log      # real-time
```

### Systemd unit файл
```
/etc/systemd/system/sp500bot.service
ExecStart=/root/sp500-bounce-bot/venv/bin/python main.py
WorkingDirectory=/root/sp500-bounce-bot
```

### Архитектура вызовов (для понимания зависимостей)
```
main.py
  └── bot/telegram_bot.py         (job callbacks + command handlers)
       ├── bot/formatters.py      (форматирование)
       ├── storage/database.py    (чтение/запись)
       └── evaluation/check_results.py (проверка итогов)
  └── analysis/                   (используется в run_full_analysis)
  └── scoring/scorer.py
  └── data/
  └── llm/analyst.py
```

### Ключевые quirks (известные особенности)
1. **yfinance Multi-ticker**: при 1 тикере возвращает плоский DataFrame, при N > 1 — MultiIndex. Везде есть проверка `if len(symbols) == 1`.
2. **S&P 500 список**: берётся с Wikipedia. Если 403 — есть fallback список ~490 тикеров в sp500_list.py.
3. **OpenAI модель**: по умолчанию `gpt-4o-mini` (дешёвая). Можно переключить на `gpt-4o` через .env.
4. **RSI**: единая реализация `calc_rsi()` в `analysis/technical.py` (EWM метод). Импортируется в market_context.py и price_fetcher.py.
5. **job_queue**: python-telegram-bot v20+ использует `run_daily()` и `run_repeating()`. APScheduler не используется.

---

## 5. ФАЙЛЫ КОТОРЫЕ НЕ ТРОГАТЬ

- `.env` — содержит секреты, не в git
- `bot_data.db` — база данных, бэкап перед изменением схемы
- `venv/` — виртуальное окружение на VPS

---

*Конец документа. Актуален на коммит `d627fd8`*
