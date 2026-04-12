"""Tests for storage/database.py — recommendations, stats, SL/TP, portfolio simulation."""
import sqlite3
import pytest
from storage.database import (
    get_connection, save_recommendations, save_report,
    get_stats_summary, get_performance_stats,
    get_pending_recommendations, update_recommendation_result,
    get_all_checked_recommendations, register_user,
)
from config import STOP_LOSS_PCT, TAKE_PROFIT_PCT


class TestSaveRecommendations:
    """Test recommendation persistence and SL/TP calculation."""

    def test_saves_with_sl_tp(self, tmp_db, sample_stock, sample_market_ctx):
        save_recommendations([sample_stock], sample_market_ctx, "2026-04-10")

        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM recommendations WHERE ticker = 'TEST'").fetchone()
        conn.close()

        assert row is not None
        assert row["price_at_signal"] == 100.0
        assert row["composite_score"] == 72.5
        assert row["stop_loss_pct"] == STOP_LOSS_PCT
        assert row["take_profit_pct"] == TAKE_PROFIT_PCT
        # SL price = 100 * (1 + (-8)/100) = 92.0
        assert row["stop_loss_price"] == 92.0
        # TP price = 100 * (1 + 15/100) = 115.0
        assert row["take_profit_price"] == 115.0

    def test_saves_check_dates(self, tmp_db, sample_stock, sample_market_ctx):
        save_recommendations([sample_stock], sample_market_ctx, "2026-04-10")

        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT check_date, check_date_30d, status, status_30d FROM recommendations").fetchone()
        conn.close()

        assert row["check_date"] is not None
        assert row["check_date_30d"] is not None
        assert row["status"] == "pending"
        assert row["status_30d"] == "pending_30d"

    def test_multiple_stocks(self, tmp_db, sample_stock, sample_market_ctx):
        stock2 = {**sample_stock}
        stock2["technical"] = {**sample_stock["technical"], "symbol": "AAPL", "current_price": 180.0}
        save_recommendations([sample_stock, stock2], sample_market_ctx, "2026-04-10")

        conn = sqlite3.connect(tmp_db)
        count = conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
        conn.close()
        assert count == 2


class TestStatsCalculation:
    """Test get_stats_summary() accuracy — win rate, success rate, averages."""

    def _insert_checked_recs(self, tmp_db, results: list[tuple[str, float, str]]):
        """Helper: insert pre-checked recommendations.
        results: [(ticker, result_pct, status), ...]
        """
        conn = sqlite3.connect(tmp_db)
        for i, (ticker, result_pct, status) in enumerate(results, 1):
            conn.execute("""
                INSERT INTO recommendations
                (signal_date, ticker, price_at_signal, composite_score,
                 check_date, price_at_check, result_pct, status, capped_result_pct,
                 stop_loss_pct, take_profit_pct, stop_loss_price, take_profit_price)
                VALUES (?, ?, 100.0, 65.0, '2026-04-20', ?, ?, ?, ?, ?, ?, 92.0, 115.0)
            """, (
                "2026-04-10", ticker,
                100.0 + result_pct,
                result_pct, status,
                max(result_pct, STOP_LOSS_PCT),
                STOP_LOSS_PCT, TAKE_PROFIT_PCT,
            ))
        conn.commit()
        conn.close()

    def test_win_rate_calculation(self, tmp_db):
        """Win rate = count(result_pct > 0) / total_checked."""
        self._insert_checked_recs(tmp_db, [
            ("AAPL", 5.0, "success"),    # win
            ("MSFT", 1.0, "neutral"),    # win (>0% even if neutral)
            ("GOOG", -2.0, "neutral"),   # loss
            ("AMZN", -4.0, "failure"),   # loss
            ("TSLA", 0.5, "neutral"),    # win
        ])

        stats = get_stats_summary()
        assert stats["total_checked"] == 5
        assert stats["win_count"] == 3       # AAPL, MSFT, TSLA
        assert stats["win_rate_pct"] == 60.0
        assert stats["success"] == 1         # only AAPL > +3%
        assert stats["failure"] == 1         # only AMZN < -3%
        assert stats["neutral"] == 3

    def test_empty_db(self, tmp_db):
        stats = get_stats_summary()
        assert stats["total_all"] == 0
        assert stats["total_checked"] == 0
        assert stats["win_rate_pct"] == 0

    def test_avg_result(self, tmp_db):
        self._insert_checked_recs(tmp_db, [
            ("A", 10.0, "success"),
            ("B", -5.0, "failure"),
        ])
        stats = get_stats_summary()
        assert stats["avg_result_pct"] == 2.5  # (10 + (-5)) / 2

    def test_best_worst(self, tmp_db):
        self._insert_checked_recs(tmp_db, [
            ("WIN", 20.0, "success"),
            ("LOSE", -10.0, "failure"),
            ("MID", 1.0, "neutral"),
        ])
        stats = get_stats_summary()
        assert stats["best"]["ticker"] == "WIN"
        assert stats["best"]["result_pct"] == 20.0
        assert stats["worst"]["ticker"] == "LOSE"
        assert stats["worst"]["result_pct"] == -10.0


class TestSLCappedResults:
    """Test that SL caps losses in update_recommendation_result."""

    def test_capped_result_stored(self, tmp_db, sample_stock, sample_market_ctx):
        save_recommendations([sample_stock], sample_market_ctx, "2026-04-01")

        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        rec = conn.execute("SELECT id FROM recommendations").fetchone()
        conn.close()

        # Simulate a -12% loss — should be capped at SL (-8%)
        update_recommendation_result(
            rec_id=rec["id"],
            price_at_check=88.0,
            result_pct=-12.0,
            max_price=105.0,
            min_price=86.0,
            status="failure",
        )

        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT result_pct, capped_result_pct FROM recommendations").fetchone()
        conn.close()

        assert row["result_pct"] == -12.0        # raw preserved
        assert row["capped_result_pct"] == -8.0   # capped at SL

    def test_profit_not_capped(self, tmp_db, sample_stock, sample_market_ctx):
        save_recommendations([sample_stock], sample_market_ctx, "2026-04-01")

        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        rec = conn.execute("SELECT id FROM recommendations").fetchone()
        conn.close()

        update_recommendation_result(
            rec_id=rec["id"],
            price_at_check=120.0,
            result_pct=20.0,
            max_price=125.0,
            min_price=98.0,
            status="success",
        )

        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT capped_result_pct FROM recommendations").fetchone()
        conn.close()

        assert row["capped_result_pct"] == 20.0  # no cap on profits


class TestPerformanceStats:
    """Test get_performance_stats() simulated portfolio."""

    def test_simulated_portfolio(self, tmp_db):
        register_user("12345")
        conn = sqlite3.connect(tmp_db)
        # 3 recs: +10%, -12% (SL → -8%), +5%
        for ticker, result, capped, status in [
            ("A", 10.0, 10.0, "success"),
            ("B", -12.0, -8.0, "failure"),
            ("C", 5.0, 5.0, "success"),
        ]:
            conn.execute("""
                INSERT INTO recommendations
                (signal_date, ticker, price_at_signal, composite_score,
                 result_pct, capped_result_pct, status,
                 stop_loss_pct, take_profit_pct, stop_loss_price, take_profit_price)
                VALUES ('2026-04-01', ?, 100.0, 65.0, ?, ?, ?, ?, ?, 92.0, 115.0)
            """, (ticker, result, capped, status, STOP_LOSS_PCT, TAKE_PROFIT_PCT))
        conn.commit()
        conn.close()

        stats = get_performance_stats("12345")

        theo = stats["theoretical"]
        assert theo["checked"] == 3
        assert theo["avg_return"] == 1.0  # (10 + (-12) + 5) / 3

        sim = stats["simulated"]
        assert sim["trades"] == 3
        assert sim["invested"] == 3000.0
        # Capped: +10%, -8%, +5% → avg = 7/3 = 2.33
        assert sim["avg_per_trade"] == 2.33
        # Total PnL: $100 + (-$80) + $50 = $70
        assert sim["total_pnl"] == 70.0
        assert sim["portfolio_return_pct"] == 2.33
        assert sim["worst_trade"] == -8.0
        assert sim["best_trade"] == 10.0


class TestCheckedRecommendations:
    """Test get_all_checked_recommendations() for charts."""

    def test_returns_ordered(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        for date, ticker, result in [
            ("2026-04-02", "B", 5.0),
            ("2026-04-01", "A", 3.0),
            ("2026-04-03", "C", -1.0),
        ]:
            conn.execute("""
                INSERT INTO recommendations
                (signal_date, ticker, price_at_signal, composite_score,
                 result_pct, status)
                VALUES (?, ?, 100.0, 65.0, ?, 'success')
            """, (date, ticker, result))
        conn.commit()
        conn.close()

        recs = get_all_checked_recommendations()
        assert len(recs) == 3
        assert recs[0]["ticker"] == "A"  # ordered by date
        assert recs[1]["ticker"] == "B"
        assert recs[2]["ticker"] == "C"

    def test_excludes_pending(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.execute("""
            INSERT INTO recommendations
            (signal_date, ticker, price_at_signal, composite_score, status)
            VALUES ('2026-04-01', 'PEND', 100.0, 65.0, 'pending')
        """)
        conn.commit()
        conn.close()

        recs = get_all_checked_recommendations()
        assert len(recs) == 0
