"""Shared fixtures for the test suite."""
import os
import sys
import sqlite3
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Override DB_PATH before any project import
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "12345")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("FINNHUB_API_KEY", "test-key")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Create a temporary SQLite DB and patch config.DB_PATH to point at it."""
    db_file = str(tmp_path / "test_bot.db")
    monkeypatch.setattr("config.DB_PATH", db_file)
    # Also patch the module-level import in storage.database
    import storage.database as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    db_mod.init_db()
    return db_file


@pytest.fixture
def sample_stock():
    """Minimal stock dict matching save_recommendations() format."""
    return {
        "technical": {
            "symbol": "TEST",
            "current_price": 100.0,
            "rsi": 35.0,
            "macd_histogram_rising": True,
            "rsi_bullish_divergence": False,
            "stoch_rsi": 0.15,
            "bb_pct_b": 0.1,
            "drawdown_pct": -20.0,
        },
        "fundamental": {
            "pe_trailing": 15.0,
            "eps_growth_expected": 12.0,
            "quality_grade": "B",
            "sector": "Technology",
        },
        "sentiment": {},
        "scores": {
            "composite_score": 72.5,
            "bounce_probability": "medium_high",
            "technical_score": 85.0,
            "fundamental_score": 70.0,
            "sentiment_score": 55.0,
            "market_score": 60.0,
        },
    }


@pytest.fixture
def sample_market_ctx():
    """Minimal market context dict."""
    return {
        "regime": "neutral",
        "spy": {"price": 520.0, "rsi": 55.0, "trend": "sideways",
                "above_sma50": True, "above_sma200": True},
        "vix": {"value": 18.5, "label": "normal"},
        "sectors": [],
    }
