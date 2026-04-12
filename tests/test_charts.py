"""Tests for charts/generator.py — chart generation produces valid PNG."""
import pytest
from unittest.mock import patch


class TestChartGeneration:
    """Test generate_stats_chart() produces PNG bytes."""

    @patch("storage.database.get_all_checked_recommendations")
    def test_generates_png(self, mock_recs):
        mock_recs.return_value = [
            {"ticker": "AAPL", "signal_date": "2026-04-01", "composite_score": 70,
             "price_at_signal": 150.0, "price_at_check": 157.5, "result_pct": 5.0, "status": "success"},
            {"ticker": "TSLA", "signal_date": "2026-04-02", "composite_score": 62,
             "price_at_signal": 200.0, "price_at_check": 190.0, "result_pct": -5.0, "status": "failure"},
            {"ticker": "MSFT", "signal_date": "2026-04-03", "composite_score": 75,
             "price_at_signal": 400.0, "price_at_check": 404.0, "result_pct": 1.0, "status": "neutral"},
        ]

        stats = {
            "total_checked": 3, "success": 1, "neutral": 1, "failure": 1,
            "win_count": 2, "win_rate_pct": 66.7,
            "success_pct": 33.3, "avg_result_pct": 0.33,
            "score_bins": [],
        }

        from charts.generator import generate_stats_chart
        result = generate_stats_chart(stats)

        assert result is not None
        assert isinstance(result, bytes)
        assert result[:4] == b"\x89PNG"  # PNG magic bytes
        assert len(result) > 1000  # reasonable size for a chart

    @patch("storage.database.get_all_checked_recommendations")
    def test_empty_returns_none(self, mock_recs):
        mock_recs.return_value = []

        from charts.generator import generate_stats_chart
        result = generate_stats_chart({"total_checked": 0})

        assert result is None
