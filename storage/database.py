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

        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE NOT NULL,
            added_at TEXT DEFAULT (datetime('now'))
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
    """)
    conn.commit()
    conn.close()
    logger.info("Database initialized")


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


# --- Watchlist ---

def add_to_watchlist(symbol: str) -> bool:
    conn = get_connection()
    try:
        conn.execute("INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)", (symbol.upper(),))
        conn.commit()
        changed = conn.total_changes > 0
        conn.close()
        return changed
    except Exception:
        conn.close()
        return False


def remove_from_watchlist(symbol: str) -> bool:
    conn = get_connection()
    cursor = conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def get_watchlist() -> list[str]:
    conn = get_connection()
    rows = conn.execute("SELECT symbol FROM watchlist ORDER BY symbol").fetchall()
    conn.close()
    return [r["symbol"] for r in rows]


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
