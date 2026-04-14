import sqlite3
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from config import DB_PATH, CHECK_PERIOD_DAYS, SUCCESS_THRESHOLD_PCT, FAILURE_THRESHOLD_PCT, STOP_LOSS_PCT, TAKE_PROFIT_PCT, logger


class NumpyEncoder(json.JSONEncoder):
    """Handle numpy types for JSON serialization."""
    def default(self, obj):
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        if isinstance(obj, (pd.Timestamp, datetime)):
            return obj.isoformat()
        if isinstance(obj, float) and np.isnan(obj):
            return None
        return super().default(obj)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_connection()

    # Phase 1: Core tables (existed before multi-user) — safe for any DB state
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            market_regime TEXT,
            stocks_json TEXT,
            llm_response TEXT,
            scores_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date);
        CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);

        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            price_at_signal REAL NOT NULL,
            rsi REAL,
            macd_direction TEXT,
            divergence INTEGER DEFAULT 0,
            stoch_rsi REAL,
            bb_pct_b REAL,
            drawdown_pct REAL,
            composite_score REAL NOT NULL,
            bounce_probability TEXT,
            technical_score REAL,
            fundamental_score REAL,
            sentiment_score REAL,
            market_score REAL,
            pe_ratio REAL,
            eps_growth REAL,
            quality_grade TEXT,
            sector TEXT,
            market_regime TEXT,
            check_date TEXT,
            price_at_check REAL,
            result_pct REAL,
            max_price_in_period REAL,
            min_price_in_period REAL,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT UNIQUE NOT NULL,
            spy_price REAL,
            spy_rsi REAL,
            spy_trend TEXT,
            spy_above_sma50 INTEGER,
            spy_above_sma200 INTEGER,
            vix_value REAL,
            vix_label TEXT,
            market_regime TEXT,
            total_candidates INTEGER,
            total_recommended INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_rec_status ON recommendations(status);
        CREATE INDEX IF NOT EXISTS idx_rec_date ON recommendations(signal_date);
        CREATE INDEX IF NOT EXISTS idx_rec_ticker ON recommendations(ticker);
        CREATE INDEX IF NOT EXISTS idx_rec_check ON recommendations(check_date, status);

        -- Multi-user support
        CREATE TABLE IF NOT EXISTS users (
            chat_id TEXT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            is_active INTEGER DEFAULT 1,
            is_admin INTEGER DEFAULT 0,
            subscribed_reports INTEGER DEFAULT 1,
            alert_enabled INTEGER DEFAULT 1,
            registered_at TEXT DEFAULT (datetime('now')),
            last_active TEXT
        );
    """)
    conn.commit()

    # Phase 2: Migrate old watchlist BEFORE creating new tables that reference user_id
    # (old watchlist may exist without user_id column)
    _migrate_watchlist(conn)

    # Phase 3: Tables with user_id — created individually to handle legacy DB
    _safe_create_table(conn, """
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT '',
            symbol TEXT NOT NULL,
            added_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, symbol)
        )
    """)

    _safe_create_table(conn, """
        CREATE TABLE IF NOT EXISTS alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            price REAL,
            change_pct REAL,
            rsi REAL,
            sent_at TEXT DEFAULT (datetime('now'))
        )
    """)

    _safe_create_table(conn, """
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            shares REAL NOT NULL,
            buy_price REAL NOT NULL,
            buy_date TEXT NOT NULL,
            rec_id INTEGER REFERENCES recommendations(id),
            status TEXT DEFAULT 'open',
            sell_price REAL,
            sell_date TEXT,
            pnl_pct REAL,
            pnl_abs REAL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Phase 3b: Add 30-day check columns if not exist (migration)
    for col_sql in [
        "ALTER TABLE recommendations ADD COLUMN check_date_30d TEXT",
        "ALTER TABLE recommendations ADD COLUMN price_at_check_30d REAL",
        "ALTER TABLE recommendations ADD COLUMN result_pct_30d REAL",
        "ALTER TABLE recommendations ADD COLUMN status_30d TEXT DEFAULT 'pending_30d'",
    ]:
        try:
            conn.execute(col_sql)
            conn.commit()
        except Exception:
            pass  # Column already exists

    # Phase 3c: Add SL/TP columns (migration 2026-04-12)
    for col_sql in [
        "ALTER TABLE recommendations ADD COLUMN stop_loss_price REAL",
        "ALTER TABLE recommendations ADD COLUMN take_profit_price REAL",
        "ALTER TABLE recommendations ADD COLUMN stop_loss_pct REAL",
        "ALTER TABLE recommendations ADD COLUMN take_profit_pct REAL",
        "ALTER TABLE recommendations ADD COLUMN capped_result_pct REAL",
    ]:
        try:
            conn.execute(col_sql)
            conn.commit()
        except Exception:
            pass  # Column already exists

    # Backfill SL/TP for existing recs that don't have them yet
    try:
        conn.execute("""
            UPDATE recommendations
            SET stop_loss_pct = ?,
                take_profit_pct = ?,
                stop_loss_price = ROUND(price_at_signal * (1 + ? / 100.0), 2),
                take_profit_price = ROUND(price_at_signal * (1 + ? / 100.0), 2)
            WHERE stop_loss_pct IS NULL AND price_at_signal IS NOT NULL
        """, (STOP_LOSS_PCT, TAKE_PROFIT_PCT, STOP_LOSS_PCT, TAKE_PROFIT_PCT))
        # Backfill capped_result_pct for checked recs
        conn.execute("""
            UPDATE recommendations
            SET capped_result_pct = CASE
                WHEN result_pct IS NULL THEN NULL
                WHEN result_pct <= ? THEN ?
                ELSE result_pct
            END
            WHERE capped_result_pct IS NULL AND status != 'pending'
        """, (STOP_LOSS_PCT, STOP_LOSS_PCT))
        conn.commit()
    except Exception as e:
        logger.warning(f"SL/TP backfill note: {e}")

    # Phase 3d: Paper trading table (Alpaca integration)
    _safe_create_table(conn, """
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER REFERENCES recommendations(id),
            symbol TEXT NOT NULL,
            notional_usd REAL NOT NULL,
            qty REAL,
            entry_price REAL,
            entry_time TEXT,
            exit_price REAL,
            exit_time TEXT,
            exit_reason TEXT,
            realized_pl REAL,
            realized_pl_pct REAL,
            alpaca_entry_order_id TEXT,
            alpaca_exit_order_id TEXT,
            status TEXT DEFAULT 'pending',
            signal_score REAL,
            signal_date TEXT,
            stop_loss_price REAL,
            take_profit_price REAL,
            created_at TEXT DEFAULT (datetime('now')),
            approved_at TEXT,
            rejected_at TEXT
        )
    """)

    # Phase 3e: Bot settings table (dynamic runtime config: paper trading mode, etc.)
    _safe_create_table(conn, """
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Default paper trading mode = 'off' (user must explicitly enable)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('paper_trading_mode', 'off')"
        )
        conn.commit()
    except Exception:
        pass

    # Phase 4: Indexes on user_id columns (safe after migration)
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_alerts_user_ticker ON alert_history(user_id, ticker, sent_at)",
        "CREATE INDEX IF NOT EXISTS idx_portfolio_user ON portfolio(user_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status)",
        "CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol ON paper_trades(symbol)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception as e:
            logger.warning(f"Index creation skipped: {e}")

    conn.commit()
    conn.close()
    logger.info("Database initialized")


def _safe_create_table(conn: sqlite3.Connection, sql: str):
    """Execute CREATE TABLE, ignore if already exists."""
    try:
        conn.execute(sql)
        conn.commit()
    except Exception as e:
        logger.warning(f"Table creation note: {e}")


def _migrate_watchlist(conn: sqlite3.Connection):
    """Migrate legacy watchlist rows without user_id (one-time)."""
    try:
        # Check if there are rows with empty user_id (old schema had no user_id column)
        # If the old table existed with different schema, this handles the transition
        cols = [r[1] for r in conn.execute("PRAGMA table_info(watchlist)").fetchall()]
        if "user_id" not in cols:
            # Old schema detected — rebuild table
            from config import TELEGRAM_CHAT_ID
            logger.info("Migrating watchlist to multi-user schema...")
            rows = conn.execute("SELECT symbol, added_at FROM watchlist").fetchall()
            conn.execute("DROP TABLE watchlist")
            conn.execute("""
                CREATE TABLE watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    added_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(user_id, symbol)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id)")
            for r in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO watchlist (user_id, symbol, added_at) VALUES (?, ?, ?)",
                    (TELEGRAM_CHAT_ID, r["symbol"], r["added_at"]),
                )
            conn.commit()
            logger.info(f"Migrated {len(rows)} watchlist entries to user {TELEGRAM_CHAT_ID}")
    except Exception as e:
        logger.warning(f"Watchlist migration check: {e}")


# --- Reports ---

def save_report(date: str, market_regime: str, stocks: list, llm_response: str, scores: list):
    conn = get_connection()
    conn.execute(
        "INSERT INTO reports (date, market_regime, stocks_json, llm_response, scores_json) VALUES (?, ?, ?, ?, ?)",
        (date, market_regime, json.dumps(stocks, ensure_ascii=False, cls=NumpyEncoder),
         llm_response, json.dumps(scores, ensure_ascii=False, cls=NumpyEncoder)),
    )
    conn.commit()
    conn.close()


def get_last_report() -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM reports ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "date": row["date"],
        "market_regime": row["market_regime"],
        "stocks": json.loads(row["stocks_json"]) if row["stocks_json"] else [],
        "llm_response": row["llm_response"],
        "scores": json.loads(row["scores_json"]) if row["scores_json"] else [],
        "created_at": row["created_at"],
    }


# --- Users ---

def register_user(chat_id: str, username: str = None, first_name: str = None) -> bool:
    """Register or update user. Returns True if new user."""
    conn = get_connection()
    try:
        existing = conn.execute("SELECT chat_id FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE users SET last_active = datetime('now'), username = COALESCE(?, username), first_name = COALESCE(?, first_name) WHERE chat_id = ?",
                (username, first_name, chat_id),
            )
            conn.commit()
            conn.close()
            return False
        conn.execute(
            "INSERT INTO users (chat_id, username, first_name, last_active) VALUES (?, ?, ?, datetime('now'))",
            (chat_id, username, first_name),
        )
        conn.commit()
        conn.close()
        logger.info(f"New user registered: {chat_id} ({username})")
        return True
    except Exception as e:
        logger.error(f"register_user failed: {e}")
        conn.close()
        return False


def get_user(chat_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_active_users() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM users WHERE is_active = 1 ORDER BY registered_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_subscribed_users() -> list[str]:
    """Get chat_ids of users subscribed to auto-reports."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT chat_id FROM users WHERE is_active = 1 AND subscribed_reports = 1"
    ).fetchall()
    conn.close()
    return [r["chat_id"] for r in rows]


def update_user_setting(chat_id: str, field: str, value) -> bool:
    """Update a single user setting. Field must be whitelisted."""
    allowed = {"subscribed_reports", "alert_enabled", "is_active", "is_admin"}
    if field not in allowed:
        return False
    conn = get_connection()
    conn.execute(f"UPDATE users SET {field} = ? WHERE chat_id = ?", (value, chat_id))
    conn.commit()
    changed = conn.total_changes > 0
    conn.close()
    return changed


def set_first_admin(chat_id: str):
    """Promote the first registered user to admin (called once at startup)."""
    conn = get_connection()
    admin_exists = conn.execute("SELECT 1 FROM users WHERE is_admin = 1").fetchone()
    if not admin_exists:
        conn.execute("UPDATE users SET is_admin = 1 WHERE chat_id = ?", (chat_id,))
        conn.commit()
    conn.close()


# --- Watchlist ---

def add_to_watchlist(symbol: str, user_id: str = None) -> bool:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (user_id, symbol) VALUES (?, ?)",
            (user_id or "", symbol.upper()),
        )
        conn.commit()
        changed = conn.total_changes > 0
        conn.close()
        return changed
    except Exception:
        conn.close()
        return False


def remove_from_watchlist(symbol: str, user_id: str = None) -> bool:
    conn = get_connection()
    cursor = conn.execute(
        "DELETE FROM watchlist WHERE symbol = ? AND user_id = ?",
        (symbol.upper(), user_id or ""),
    )
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def get_watchlist(user_id: str = None) -> list[str]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT symbol FROM watchlist WHERE user_id = ? ORDER BY symbol",
        (user_id or "",),
    ).fetchall()
    conn.close()
    return [r["symbol"] for r in rows]


# --- Alerts ---

def save_alert(user_id: str, ticker: str, alert_type: str,
               price: float, change_pct: float, rsi: float | None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO alert_history (user_id, ticker, alert_type, price, change_pct, rsi) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, ticker, alert_type, price, change_pct, rsi),
    )
    conn.commit()
    conn.close()


def get_last_alert_time(user_id: str, ticker: str, alert_type: str) -> str | None:
    """Get the most recent sent_at for this user/ticker/type combo."""
    conn = get_connection()
    row = conn.execute(
        "SELECT sent_at FROM alert_history WHERE user_id = ? AND ticker = ? AND alert_type = ? ORDER BY sent_at DESC LIMIT 1",
        (user_id, ticker, alert_type),
    ).fetchone()
    conn.close()
    return row["sent_at"] if row else None


# --- Cache ---

def cache_get(key: str) -> str | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM cache WHERE key = ? AND expires_at > datetime('now')",
        (key,),
    ).fetchone()
    conn.close()
    return row["value"] if row else None


def cache_set(key: str, value: str, ttl_seconds: int = 86400):
    conn = get_connection()
    expires = datetime.utcnow().timestamp() + ttl_seconds
    expires_str = datetime.utcfromtimestamp(expires).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
        (key, value, expires_str),
    )
    conn.commit()
    conn.close()


def cache_cleanup():
    conn = get_connection()
    conn.execute("DELETE FROM cache WHERE expires_at <= datetime('now')")
    conn.commit()
    conn.close()


# --- Recommendations ---

def _calc_check_date(signal_date: str, days: int, calendar_days: bool = False) -> str:
    """Calculate check date by adding N business or calendar days to signal date."""
    dt = datetime.strptime(signal_date, "%Y-%m-%d")
    if calendar_days:
        return (dt + timedelta(days=days)).strftime("%Y-%m-%d")
    added = 0
    while added < days:
        dt += timedelta(days=1)
        if dt.weekday() < 5:  # Mon-Fri
            added += 1
    return dt.strftime("%Y-%m-%d")


def save_recommendations(stocks: list[dict], market_ctx: dict, report_date: str):
    """Save each recommended stock as a separate row for tracking."""
    conn = get_connection()
    try:
        regime = market_ctx.get("regime", "unknown")
        for stock in stocks:
            tech = stock.get("technical", {})
            fund = stock.get("fundamental", {})
            scores = stock.get("scores", {})
            check_date = _calc_check_date(report_date, CHECK_PERIOD_DAYS)

            check_date_30d = _calc_check_date(report_date, 30, calendar_days=True)
            price = tech.get("current_price")
            sl_price = round(price * (1 + STOP_LOSS_PCT / 100), 2) if price else None
            tp_price = round(price * (1 + TAKE_PROFIT_PCT / 100), 2) if price else None
            conn.execute("""
                INSERT INTO recommendations
                (signal_date, ticker, price_at_signal, rsi, macd_direction,
                 divergence, stoch_rsi, bb_pct_b, drawdown_pct,
                 composite_score, bounce_probability,
                 technical_score, fundamental_score, sentiment_score, market_score,
                 pe_ratio, eps_growth, quality_grade, sector,
                 market_regime, check_date, status, check_date_30d, status_30d,
                 stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 'pending_30d',
                        ?, ?, ?, ?)
            """, (
                report_date,
                tech.get("symbol", "?"),
                price,
                tech.get("rsi"),
                "up" if tech.get("macd_histogram_rising") else "down",
                1 if tech.get("rsi_bullish_divergence") else 0,
                tech.get("stoch_rsi"),
                tech.get("bb_pct_b"),
                tech.get("drawdown_pct"),
                scores.get("composite_score"),
                scores.get("bounce_probability"),
                scores.get("technical_score"),
                scores.get("fundamental_score"),
                scores.get("sentiment_score"),
                scores.get("market_score"),
                fund.get("pe_trailing"),
                fund.get("eps_growth_expected"),
                fund.get("quality_grade"),
                fund.get("sector", "Unknown"),
                regime,
                check_date,
                check_date_30d,
                sl_price, tp_price, STOP_LOSS_PCT, TAKE_PROFIT_PCT,
            ))
        conn.commit()
        logger.info(f"Saved {len(stocks)} recommendations for {report_date}")
    except Exception as e:
        logger.error(f"Failed to save recommendations: {e}")
    finally:
        conn.close()


def save_market_snapshot(market_ctx: dict, report_date: str,
                         candidates_count: int, recommended_count: int):
    """Save market conditions at report time."""
    conn = get_connection()
    try:
        spy = market_ctx.get("spy", {})
        vix = market_ctx.get("vix", {})
        conn.execute("""
            INSERT OR REPLACE INTO market_snapshots
            (report_date, spy_price, spy_rsi, spy_trend,
             spy_above_sma50, spy_above_sma200,
             vix_value, vix_label, market_regime,
             total_candidates, total_recommended)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report_date,
            spy.get("price"),
            spy.get("rsi"),
            spy.get("trend"),
            1 if spy.get("above_sma50") else 0,
            1 if spy.get("above_sma200") else 0,
            vix.get("value"),
            vix.get("label"),
            market_ctx.get("regime"),
            candidates_count,
            recommended_count,
        ))
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to save market snapshot: {e}")
    finally:
        conn.close()


def get_pending_recommendations() -> list[dict]:
    """Get recommendations ready for 10-day result checking."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM recommendations WHERE status = 'pending' AND check_date <= ?",
        (today,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_active_recommendations_for_snapshot(days_back: int = 60) -> list[dict]:
    """Get all recommendations from last N days for dynamic snapshot tracking."""
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    conn = get_connection()
    rows = conn.execute(
        """SELECT ticker, signal_date, price_at_signal, composite_score
           FROM recommendations
           WHERE signal_date >= ?
           ORDER BY signal_date DESC""",
        (cutoff,),
    ).fetchall()
    conn.close()
    # Deduplicate: keep best score per (ticker, signal_date)
    seen = {}
    for r in rows:
        key = (r["ticker"], r["signal_date"])
        if key not in seen or r["composite_score"] > seen[key]["composite_score"]:
            seen[key] = dict(r)
    return list(seen.values())


def get_pending_30d_recommendations() -> list[dict]:
    """Get recommendations ready for 30-day result checking."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM recommendations WHERE status_30d = 'pending_30d' AND check_date_30d <= ? AND check_date_30d IS NOT NULL",
        (today,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_recommendation_result(rec_id: int, price_at_check: float,
                                 result_pct: float, max_price: float | None,
                                 min_price: float | None, status: str):
    """Update a recommendation with check results. Caps losses at SL level."""
    capped = max(result_pct, STOP_LOSS_PCT)  # SL caps the worst case
    conn = get_connection()
    conn.execute("""
        UPDATE recommendations
        SET price_at_check = ?, result_pct = ?,
            max_price_in_period = ?, min_price_in_period = ?,
            status = ?, capped_result_pct = ?,
            updated_at = datetime('now')
        WHERE id = ?
    """, (price_at_check, result_pct, max_price, min_price, status, capped, rec_id))
    conn.commit()
    conn.close()


def update_recommendation_30d_result(rec_id: int, price_at_check: float,
                                     result_pct: float, status_30d: str):
    """Update a recommendation with 30-day check results."""
    conn = get_connection()
    conn.execute("""
        UPDATE recommendations
        SET price_at_check_30d = ?, result_pct_30d = ?,
            status_30d = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (price_at_check, result_pct, status_30d, rec_id))
    conn.commit()
    conn.close()


# --- Portfolio ---

def add_portfolio_position(user_id: str, ticker: str, shares: float,
                           buy_price: float, rec_id: int | None = None) -> int | None:
    """Add an open position. Returns row id."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO portfolio (user_id, ticker, shares, buy_price, buy_date, rec_id) VALUES (?, ?, ?, ?, datetime('now'), ?)",
            (user_id, ticker.upper(), shares, buy_price, rec_id),
        )
        conn.commit()
        row_id = cur.lastrowid
        conn.close()
        return row_id
    except Exception as e:
        logger.error(f"add_portfolio_position failed: {e}")
        conn.close()
        return None


def close_portfolio_position(user_id: str, ticker: str, sell_price: float) -> dict | None:
    """Close the oldest open position for ticker. Returns closed position dict."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM portfolio WHERE user_id = ? AND ticker = ? AND status = 'open' ORDER BY buy_date ASC LIMIT 1",
        (user_id, ticker.upper()),
    ).fetchone()
    if not row:
        conn.close()
        return None

    pnl_abs = round((sell_price - row["buy_price"]) * row["shares"], 2)
    pnl_pct = round((sell_price / row["buy_price"] - 1) * 100, 2) if row["buy_price"] else 0

    conn.execute(
        "UPDATE portfolio SET status = 'closed', sell_price = ?, sell_date = datetime('now'), pnl_pct = ?, pnl_abs = ? WHERE id = ?",
        (sell_price, pnl_pct, pnl_abs, row["id"]),
    )
    conn.commit()

    result = dict(row)
    result["sell_price"] = sell_price
    result["pnl_pct"] = pnl_pct
    result["pnl_abs"] = pnl_abs
    conn.close()
    return result


def get_open_positions(user_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM portfolio WHERE user_id = ? AND status = 'open' ORDER BY buy_date DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_closed_positions(user_id: str, limit: int = 20) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM portfolio WHERE user_id = ? AND status = 'closed' ORDER BY sell_date DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats_summary() -> dict:
    """Get aggregated statistics for /stats command."""
    conn = get_connection()

    # Total counts by status
    rows = conn.execute("""
        SELECT status, COUNT(*) as cnt, AVG(result_pct) as avg_pct
        FROM recommendations
        WHERE status != 'pending'
        GROUP BY status
    """).fetchall()

    checked = {r["status"]: {"count": r["cnt"], "avg_pct": r["avg_pct"]} for r in rows}

    # Pending count
    pending = conn.execute(
        "SELECT COUNT(*) as cnt FROM recommendations WHERE status = 'pending'"
    ).fetchone()["cnt"]

    # Best and worst
    best = conn.execute(
        "SELECT ticker, result_pct, signal_date FROM recommendations WHERE status != 'pending' ORDER BY result_pct DESC LIMIT 1"
    ).fetchone()
    worst = conn.execute(
        "SELECT ticker, result_pct, signal_date FROM recommendations WHERE status != 'pending' ORDER BY result_pct ASC LIMIT 1"
    ).fetchone()

    # Score vs result correlation
    score_bins = conn.execute("""
        SELECT
            CASE
                WHEN composite_score >= 70 THEN 'high'
                WHEN composite_score >= 60 THEN 'medium'
                ELSE 'low'
            END as score_bin,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successes
        FROM recommendations
        WHERE status != 'pending'
        GROUP BY score_bin
    """).fetchall()

    # Total recommendations ever
    total_all = conn.execute("SELECT COUNT(*) as cnt FROM recommendations").fetchone()["cnt"]

    # Win rate >0%: count checked recs with positive result
    win_count_row = conn.execute(
        "SELECT COUNT(*) as cnt FROM recommendations WHERE status != 'pending' AND result_pct > 0"
    ).fetchone()
    win_count = win_count_row["cnt"] if win_count_row else 0

    conn.close()

    total_checked = sum(v["count"] for v in checked.values())
    success_count = checked.get("success", {}).get("count", 0)
    neutral_count = checked.get("neutral", {}).get("count", 0)
    failure_count = checked.get("failure", {}).get("count", 0)

    all_avg = None
    if total_checked > 0:
        total_result = sum(v["avg_pct"] * v["count"] for v in checked.values() if v["avg_pct"])
        all_avg = round(total_result / total_checked, 2) if total_checked else None

    return {
        "total_all": total_all,
        "total_checked": total_checked,
        "pending": pending,
        "success": success_count,
        "neutral": neutral_count,
        "failure": failure_count,
        "success_pct": round(success_count / total_checked * 100, 1) if total_checked else 0,
        "win_count": win_count,
        "win_rate_pct": round(win_count / total_checked * 100, 1) if total_checked else 0,
        "avg_result_pct": all_avg,
        "best": dict(best) if best else None,
        "worst": dict(worst) if worst else None,
        "score_bins": [dict(r) for r in score_bins],
    }


def get_30d_summary() -> dict:
    """Summary statistics for 30-day checked recommendations."""
    conn = get_connection()

    pending_30d = conn.execute(
        "SELECT COUNT(*) as cnt FROM recommendations WHERE status_30d = 'pending_30d'"
    ).fetchone()["cnt"]

    rows = conn.execute("""
        SELECT ticker, signal_date, result_pct_30d, status_30d
        FROM recommendations
        WHERE status_30d NOT IN ('pending_30d')
        AND result_pct_30d IS NOT NULL
    """).fetchall()

    conn.close()

    if not rows:
        return {"total": 0, "pending": pending_30d}

    pcts = [r["result_pct_30d"] for r in rows]
    wins = [p for p in pcts if p > 0]
    success = sum(1 for r in rows if r["status_30d"] == "success_30d")
    neutral = sum(1 for r in rows if r["status_30d"] == "neutral_30d")
    failure = sum(1 for r in rows if r["status_30d"] == "failure_30d")
    best = max(rows, key=lambda r: r["result_pct_30d"])
    worst = min(rows, key=lambda r: r["result_pct_30d"])

    return {
        "total": len(rows),
        "pending": pending_30d,
        "success": success,
        "neutral": neutral,
        "failure": failure,
        "avg_pct": round(sum(pcts) / len(pcts), 2),
        "win_rate": round(len(wins) / len(pcts) * 100, 1),
        "best": {"ticker": best["ticker"], "pct": best["result_pct_30d"], "date": best["signal_date"]},
        "worst": {"ticker": worst["ticker"], "pct": worst["result_pct_30d"], "date": worst["signal_date"]},
    }


def get_all_checked_recommendations() -> list[dict]:
    """Return all checked (non-pending) recommendations for charting."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT ticker, signal_date, composite_score, price_at_signal,
               price_at_check, result_pct, status
        FROM recommendations
        WHERE status != 'pending' AND result_pct IS NOT NULL
        ORDER BY signal_date ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_performance_stats(user_id: str) -> dict:
    """Calculate theoretical, SL-capped simulated portfolio, and actual portfolio performance."""
    conn = get_connection()

    # Theoretical (raw): all checked recommendations, no SL applied
    theo_rows = conn.execute("""
        SELECT ticker, result_pct, capped_result_pct, status
        FROM recommendations
        WHERE status != 'pending' AND result_pct IS NOT NULL
    """).fetchall()

    theoretical = {}
    simulated = {}
    if theo_rows:
        # Raw returns
        returns = [r["result_pct"] for r in theo_rows]
        wins = [r for r in returns if r > 0]
        best = max(theo_rows, key=lambda r: r["result_pct"])
        worst = min(theo_rows, key=lambda r: r["result_pct"])
        total_recs = conn.execute("SELECT COUNT(*) as cnt FROM recommendations").fetchone()["cnt"]
        theoretical = {
            "total": total_recs,
            "checked": len(returns),
            "avg_return": round(sum(returns) / len(returns), 2),
            "win_rate": round(len(wins) / len(returns) * 100, 1),
            "best_ticker": best["ticker"],
            "best_pct": best["result_pct"],
            "worst_ticker": worst["ticker"],
            "worst_pct": worst["result_pct"],
        }

        # SL-capped simulated portfolio ($1000 per trade)
        capped = [r["capped_result_pct"] if r["capped_result_pct"] is not None
                  else max(r["result_pct"], STOP_LOSS_PCT) for r in theo_rows]
        trade_size = 1000.0
        total_invested = trade_size * len(capped)
        trade_pnls = [trade_size * (pct / 100.0) for pct in capped]
        total_pnl = sum(trade_pnls)
        capped_wins = [p for p in capped if p > 0]
        simulated = {
            "trades": len(capped),
            "invested": total_invested,
            "total_pnl": round(total_pnl, 2),
            "portfolio_return_pct": round(total_pnl / total_invested * 100, 2) if total_invested else 0,
            "avg_per_trade": round(sum(capped) / len(capped), 2),
            "win_rate": round(len(capped_wins) / len(capped) * 100, 1),
            "max_loss_per_trade": STOP_LOSS_PCT,
            "best_trade": round(max(capped), 2),
            "worst_trade": round(min(capped), 2),
        }

    # Actual: user's portfolio (closed + open)
    closed = conn.execute("""
        SELECT ticker, pnl_pct, pnl_abs
        FROM portfolio
        WHERE user_id = ? AND status = 'closed' AND pnl_pct IS NOT NULL
    """, (user_id,)).fetchall()

    open_pos = conn.execute("""
        SELECT ticker, buy_price, shares
        FROM portfolio
        WHERE user_id = ? AND status = 'open'
    """, (user_id,)).fetchall()

    actual = {"total": len(closed) + len(open_pos), "open": len(open_pos), "closed": len(closed)}

    if closed:
        pnls = [r["pnl_pct"] for r in closed]
        pnl_abs = [r["pnl_abs"] for r in closed]
        wins = [p for p in pnls if p > 0]
        actual["avg_return"] = round(sum(pnls) / len(pnls), 2)
        actual["total_pnl"] = round(sum(pnl_abs), 2)
        actual["win_rate"] = round(len(wins) / len(pnls) * 100, 1)

    conn.close()

    return {"theoretical": theoretical, "simulated": simulated, "actual": actual}


# ── Bot Settings ────────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    """Get a runtime setting from bot_settings table."""
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM bot_settings WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    """Upsert a runtime setting."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO bot_settings (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, value),
    )
    conn.commit()
    conn.close()


# ── Paper Trading ───────────────────────────────────────────────────────────────

def save_paper_trade(
    symbol: str,
    notional_usd: float,
    signal_date: str,
    signal_score: float,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
    recommendation_id: int | None = None,
    status: str = "pending_approval",
) -> int:
    """Create a new paper trade record. Returns the new trade id."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO paper_trades
           (symbol, notional_usd, signal_date, signal_score,
            stop_loss_price, take_profit_price, recommendation_id, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (symbol, notional_usd, signal_date, signal_score,
         stop_loss_price, take_profit_price, recommendation_id, status),
    )
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def update_paper_trade_open(
    trade_id: int,
    order_id: str,
    entry_price: float | None,
    qty: float | None,
):
    """Mark trade as open after order submitted/filled."""
    conn = get_connection()
    conn.execute(
        """UPDATE paper_trades
           SET status = 'open',
               alpaca_entry_order_id = ?,
               entry_price = ?,
               qty = ?,
               entry_time = datetime('now'),
               approved_at = datetime('now')
           WHERE id = ?""",
        (order_id, entry_price, qty, trade_id),
    )
    conn.commit()
    conn.close()


def update_paper_trade_error(trade_id: int, error_msg: str):
    """Mark trade as error (order submission failed)."""
    conn = get_connection()
    conn.execute(
        """UPDATE paper_trades
           SET status = 'error',
               exit_reason = ?
           WHERE id = ?""",
        (error_msg[:200], trade_id),
    )
    conn.commit()
    conn.close()


def reject_paper_trade(trade_id: int):
    """Mark trade as rejected (user declined in hybrid mode)."""
    conn = get_connection()
    conn.execute(
        """UPDATE paper_trades
           SET status = 'rejected',
               rejected_at = datetime('now')
           WHERE id = ?""",
        (trade_id,),
    )
    conn.commit()
    conn.close()


def close_paper_trade(
    trade_id: int,
    exit_price: float,
    exit_reason: str,
    order_id: str | None = None,
):
    """Close a paper trade and calculate realized P&L."""
    conn = get_connection()
    trade = conn.execute(
        "SELECT entry_price, notional_usd FROM paper_trades WHERE id = ?", (trade_id,)
    ).fetchone()

    if trade and trade["entry_price"]:
        entry = trade["entry_price"]
        pl_pct = round((exit_price - entry) / entry * 100, 2)
        pl_abs = round(trade["notional_usd"] * (pl_pct / 100.0), 2)
    else:
        pl_pct = None
        pl_abs = None

    conn.execute(
        """UPDATE paper_trades
           SET status = 'closed',
               exit_price = ?,
               exit_time = datetime('now'),
               exit_reason = ?,
               alpaca_exit_order_id = ?,
               realized_pl = ?,
               realized_pl_pct = ?
           WHERE id = ?""",
        (exit_price, exit_reason, order_id, pl_abs, pl_pct, trade_id),
    )
    conn.commit()
    conn.close()


def get_open_paper_trades() -> list[dict]:
    """Return all currently open paper trades."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM paper_trades
           WHERE status = 'open'
           ORDER BY entry_time ASC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_approval_trades() -> list[dict]:
    """Return trades waiting for hybrid-mode approval (not yet approved/rejected)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM paper_trades
           WHERE status = 'pending_approval'
           ORDER BY created_at ASC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_paper_trade_by_id(trade_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM paper_trades WHERE id = ?", (trade_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_paper_trades_stats() -> dict:
    """
    Aggregate stats for paper trading dashboard:
    - Total trades, open, closed, rejected/cancelled
    - Win rate, avg P&L, best/worst trade
    - Closed P&L vs starting $100k
    """
    conn = get_connection()

    totals = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_count,
            SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) as closed_count,
            SUM(CASE WHEN status IN ('rejected', 'cancelled') THEN 1 ELSE 0 END) as skipped_count,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count
        FROM paper_trades
    """).fetchone()

    closed = conn.execute("""
        SELECT symbol, realized_pl, realized_pl_pct, entry_price, exit_price, exit_reason
        FROM paper_trades
        WHERE status = 'closed' AND realized_pl_pct IS NOT NULL
    """).fetchall()

    open_trades = conn.execute("""
        SELECT symbol, entry_price, notional_usd, entry_time, signal_score,
               stop_loss_price, take_profit_price
        FROM paper_trades
        WHERE status = 'open'
        ORDER BY entry_time ASC
    """).fetchall()

    conn.close()

    stats = {
        "total": totals["total"] if totals else 0,
        "open_count": totals["open_count"] if totals else 0,
        "closed_count": totals["closed_count"] if totals else 0,
        "skipped_count": totals["skipped_count"] if totals else 0,
        "error_count": totals["error_count"] if totals else 0,
        "open_trades": [dict(r) for r in open_trades],
        "closed_trades_summary": [],
        "total_realized_pl": 0.0,
        "win_rate": 0.0,
        "avg_pl_pct": 0.0,
        "best_trade": None,
        "worst_trade": None,
    }

    if closed:
        pls = [r["realized_pl"] for r in closed]
        pcts = [r["realized_pl_pct"] for r in closed]
        wins = [p for p in pcts if p > 0]
        best = max(closed, key=lambda r: r["realized_pl_pct"])
        worst = min(closed, key=lambda r: r["realized_pl_pct"])

        stats["total_realized_pl"] = round(sum(pls), 2)
        stats["win_rate"] = round(len(wins) / len(pcts) * 100, 1) if pcts else 0.0
        stats["avg_pl_pct"] = round(sum(pcts) / len(pcts), 2) if pcts else 0.0
        stats["best_trade"] = {
            "symbol": best["symbol"], "pct": best["realized_pl_pct"],
            "reason": best["exit_reason"]
        }
        stats["worst_trade"] = {
            "symbol": worst["symbol"], "pct": worst["realized_pl_pct"],
            "reason": worst["exit_reason"]
        }

    return stats
