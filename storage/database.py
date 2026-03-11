import sqlite3
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from config import DB_PATH, CHECK_PERIOD_DAYS, SUCCESS_THRESHOLD_PCT, FAILURE_THRESHOLD_PCT, logger


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

        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT '',
            symbol TEXT NOT NULL,
            added_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, symbol)
        );

        -- Watchlist alerts history
        CREATE TABLE IF NOT EXISTS alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            price REAL,
            change_pct REAL,
            rsi REAL,
            sent_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_alerts_user_ticker ON alert_history(user_id, ticker, sent_at);

        -- Portfolio tracker
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
        );
        CREATE INDEX IF NOT EXISTS idx_portfolio_user ON portfolio(user_id, status);
    """)
    conn.commit()

    # Migrate old watchlist (no user_id) → new schema
    _migrate_watchlist(conn)

    # Create watchlist index AFTER migration (old table may lack user_id column)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id)")
        conn.commit()
    except Exception:
        pass  # index already exists or migration not yet applied

    conn.close()
    logger.info("Database initialized")


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

def _calc_check_date(signal_date: str, business_days: int) -> str:
    """Calculate check date by adding N business days to signal date."""
    dt = datetime.strptime(signal_date, "%Y-%m-%d")
    added = 0
    while added < business_days:
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

            conn.execute("""
                INSERT INTO recommendations
                (signal_date, ticker, price_at_signal, rsi, macd_direction,
                 divergence, stoch_rsi, bb_pct_b, drawdown_pct,
                 composite_score, bounce_probability,
                 technical_score, fundamental_score, sentiment_score, market_score,
                 pe_ratio, eps_growth, quality_grade, sector,
                 market_regime, check_date, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (
                report_date,
                tech.get("symbol", "?"),
                tech.get("current_price"),
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
    """Get recommendations ready for result checking."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM recommendations WHERE status = 'pending' AND check_date <= ?",
        (today,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_recommendation_result(rec_id: int, price_at_check: float,
                                 result_pct: float, max_price: float | None,
                                 min_price: float | None, status: str):
    """Update a recommendation with check results."""
    conn = get_connection()
    conn.execute("""
        UPDATE recommendations
        SET price_at_check = ?, result_pct = ?,
            max_price_in_period = ?, min_price_in_period = ?,
            status = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (price_at_check, result_pct, max_price, min_price, status, rec_id))
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
        "avg_result_pct": all_avg,
        "best": dict(best) if best else None,
        "worst": dict(worst) if worst else None,
        "score_bins": [dict(r) for r in score_bins],
    }
