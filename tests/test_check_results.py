"""Tests for evaluation/check_results.py — snapshot deduplication, result checking."""
import pytest
from unittest.mock import patch, MagicMock
from evaluation.check_results import snapshot_all_recommendations, check_pending_results


class TestSnapshotDeduplication:
    """Test that top5/bottom5 show unique tickers."""

    @patch("evaluation.check_results._fetch_current_prices")
    @patch("storage.database.get_active_recommendations_for_snapshot")
    def test_dedup_top5(self, mock_get_recs, mock_prices):
        """Same ticker appearing 3 times should only show once in top5."""
        mock_get_recs.return_value = [
            {"ticker": "NEM", "signal_date": "2026-03-20", "price_at_signal": 40.0, "composite_score": 65},
            {"ticker": "NEM", "signal_date": "2026-03-25", "price_at_signal": 42.0, "composite_score": 68},
            {"ticker": "NEM", "signal_date": "2026-04-01", "price_at_signal": 38.0, "composite_score": 70},
            {"ticker": "AAPL", "signal_date": "2026-04-01", "price_at_signal": 150.0, "composite_score": 72},
            {"ticker": "MSFT", "signal_date": "2026-04-01", "price_at_signal": 400.0, "composite_score": 71},
        ]
        mock_prices.return_value = {
            "NEM": 50.0,   # different P&L for different entries
            "AAPL": 160.0,
            "MSFT": 420.0,
        }

        result = snapshot_all_recommendations()

        assert result["total"] == 5  # all recs counted in aggregate
        top5 = result["top5"]
        # NEM should appear only once in top5 (with best P&L)
        nem_entries = [e for e in top5 if e["ticker"] == "NEM"]
        assert len(nem_entries) <= 1

    @patch("evaluation.check_results._fetch_current_prices")
    @patch("storage.database.get_active_recommendations_for_snapshot")
    def test_aggregate_uses_all_recs(self, mock_get_recs, mock_prices):
        """Aggregate stats (avg, win_rate) should use ALL recs, not deduped."""
        mock_get_recs.return_value = [
            {"ticker": "A", "signal_date": "2026-04-01", "price_at_signal": 100.0, "composite_score": 70},
            {"ticker": "B", "signal_date": "2026-04-01", "price_at_signal": 100.0, "composite_score": 65},
        ]
        mock_prices.return_value = {"A": 110.0, "B": 95.0}

        result = snapshot_all_recommendations()

        assert result["total"] == 2
        assert result["wins"] == 1  # A: +10% → win (>3%)
        assert result["losses"] == 1  # B: -5% → loss (<-3%)
        assert result["avg_return"] == 2.5  # (10 + (-5)) / 2

    @patch("evaluation.check_results._fetch_current_prices")
    @patch("storage.database.get_active_recommendations_for_snapshot")
    def test_empty_recs(self, mock_get_recs, mock_prices):
        mock_get_recs.return_value = []
        result = snapshot_all_recommendations()
        assert result == {}


class TestCheckPendingResults:
    """Test check_pending_results() with mocked DB and prices."""

    @patch("evaluation.check_results._get_period_range", return_value=(110.0, 95.0))
    @patch("evaluation.check_results._fetch_current_prices")
    @patch("evaluation.check_results.get_pending_recommendations")
    @patch("evaluation.check_results.update_recommendation_result")
    def test_success_status(self, mock_update, mock_pending, mock_prices, mock_range):
        mock_pending.return_value = [{
            "id": 1, "ticker": "AAPL", "price_at_signal": 100.0,
            "signal_date": "2026-04-01", "check_date": "2026-04-15",
            "composite_score": 70,
        }]
        mock_prices.return_value = {"AAPL": 105.0}

        results = check_pending_results()

        assert len(results) == 1
        assert results[0]["status"] == "success"
        assert results[0]["result_pct"] == 5.0

        # Verify update was called with capped result
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[1]["status"] == "success"
        assert call_kwargs[1]["result_pct"] == 5.0

    @patch("evaluation.check_results._get_period_range", return_value=(102.0, 85.0))
    @patch("evaluation.check_results._fetch_current_prices")
    @patch("evaluation.check_results.get_pending_recommendations")
    @patch("evaluation.check_results.update_recommendation_result")
    def test_failure_status(self, mock_update, mock_pending, mock_prices, mock_range):
        mock_pending.return_value = [{
            "id": 2, "ticker": "TSLA", "price_at_signal": 100.0,
            "signal_date": "2026-04-01", "check_date": "2026-04-15",
            "composite_score": 65,
        }]
        mock_prices.return_value = {"TSLA": 90.0}

        results = check_pending_results()

        assert len(results) == 1
        assert results[0]["status"] == "failure"
        assert results[0]["result_pct"] == -10.0
