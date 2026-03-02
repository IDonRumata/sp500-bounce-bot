import sqlite3
import json
import numpy as np
from datetime import datetime
from config import DB_PATH, logger


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
