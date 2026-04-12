"""Tests for bot/formatters.py — output formatting correctness."""
import pytest
from bot.formatters import (
    format_stats, format_performance, format_check_results,
    format_snapshot_digest, format_entry_signals, format_exit_signals,
    format_stocks_table,
)


class TestFormatStats:
    """Test /stats output formatting."""

    def test_empty_stats(self):
        result = format_stats({"total_all": 0})
        assert "Данных пока нет" in result

    def test_shows_win_rate_and_success(self):
        stats = {
            "total_all": 10, "total_checked": 8, "pending": 2,
            "success": 3, "neutral": 3, "failure": 2,
            "success_pct": 37.5,
            "win_count": 5, "win_rate_pct": 62.5,
            "avg_result_pct": 1.5,
            "best": {"ticker": "AAPL", "result_pct": 10.0, "signal_date": "2026-04-01"},
            "worst": {"ticker": "TSLA", "result_pct": -5.0, "signal_date": "2026-04-02"},
            "score_bins": [],
        }
        result = format_stats(stats)
        assert "Win rate (>0%): *5/8* = *62.5%*" in result
        assert "Успех (>+3%): *3* (37.5%)" in result
        assert "+1.50%" in result
        assert "AAPL" in result
        assert "TSLA" in result


class TestFormatPerformance:
    """Test /performance output with simulated portfolio."""

    def test_shows_all_sections(self):
        stats = {
            "theoretical": {
                "total": 50, "checked": 40,
                "avg_return": 1.67, "win_rate": 49.0,
                "best_ticker": "SBAC", "best_pct": 31.44,
                "worst_ticker": "WDAY", "worst_pct": -12.35,
            },
            "simulated": {
                "trades": 40, "invested": 40000.0,
                "total_pnl": 908.0, "portfolio_return_pct": 2.27,
                "avg_per_trade": 2.27, "win_rate": 52.5,
                "max_loss_per_trade": -8.0,
                "best_trade": 31.44, "worst_trade": -8.0,
            },
            "actual": {"total": 0, "open": 0, "closed": 0},
        }
        result = format_performance(stats)
        assert "Без стоп-лосса" in result
        assert "+1.67%" in result
        assert "С стоп-лоссом -8.0%" in result
        assert "$+908.00" in result or "+908.00" in result
        assert "Ваш портфель:" in result

    def test_empty_portfolio(self):
        stats = {"theoretical": {}, "simulated": {}, "actual": {"total": 0, "open": 0, "closed": 0}}
        result = format_performance(stats)
        assert "позиций нет" in result


class TestFormatCheckResults:
    """Test check results formatting."""

    def test_standard_period(self):
        results = [
            {"ticker": "AAPL", "price_at_signal": 150.0, "price_at_check": 157.5,
             "result_pct": 5.0, "status": "success", "signal_date": "2026-04-01", "composite_score": 70},
        ]
        msg = format_check_results(results, period="10д")
        assert "10д" in msg
        assert "✅" in msg
        assert "AAPL" in msg
        assert "+5.00%" in msg

    def test_empty_returns_empty(self):
        assert format_check_results([]) == ""


class TestFormatSnapshotDigest:
    """Test dynamic snapshot digest formatting."""

    def test_live_label(self):
        snapshot = {
            "date": "2026-04-12", "total": 50, "avg_return": 2.5,
            "win_rate": 55.0, "wins": 30, "losses": 10, "flat": 10,
            "top5": [], "bottom5": [],
            "fresh_avg": 1.5, "older_avg": 3.0,
            "fresh_count": 20, "older_count": 30,
        }
        msg = format_snapshot_digest(snapshot)
        assert "нереализованные" in msg
        assert "live, не финальный" in msg

    def test_empty_returns_empty(self):
        assert format_snapshot_digest({}) == ""
        assert format_snapshot_digest(None) == ""


class TestFormatEntrySignals:
    """Test entry signals show SL/TP levels."""

    def test_shows_sl_tp(self):
        signals = [{
            "ticker": "AAPL", "price": 150.0, "rsi": 32.0,
            "drawdown": -18.5, "tech_score": 72.0,
            "divergence": True, "macd_bullish": False, "bb_below": True,
        }]
        msg = format_entry_signals(signals)
        assert "SL:" in msg
        assert "TP:" in msg
        assert "AAPL" in msg
        assert "$138.0" in msg  # SL: 150 * 0.92 = 138.0
        assert "$172.5" in msg  # TP: 150 * 1.15 = 172.5


class TestFormatStocksTable:
    """Test recommendation table shows SL/TP."""

    def test_shows_sl_tp_in_table(self):
        stocks = [{
            "technical": {
                "symbol": "TEST", "current_price": 100.0,
                "drawdown_pct": -15.0, "rsi": 30.0,
                "macd_histogram_rising": True,
                "rsi_bullish_divergence": False,
            },
            "fundamental": {
                "earnings_growth": 10.0, "pe_forward": 15.0,
                "quality_grade": "B",
            },
            "scores": {
                "composite_score": 72.0,
                "bounce_probability": "medium_high",
            },
        }]
        msg = format_stocks_table(stocks)
        assert "SL:" in msg
        assert "TP:" in msg
        assert "$92.0" in msg   # SL
        assert "$115.0" in msg  # TP
