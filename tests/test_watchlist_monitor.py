"""Tests for alerts/watchlist_monitor.py — exit signal thresholds."""
import pytest
from config import STOP_LOSS_PCT, TAKE_PROFIT_PCT
from alerts.watchlist_monitor import EXIT_TAKE_PROFIT_PCT, EXIT_STOP_LOSS_PCT


class TestExitThresholds:
    """Verify exit thresholds match config SL/TP."""

    def test_sl_matches_config(self):
        assert EXIT_STOP_LOSS_PCT == STOP_LOSS_PCT
        assert EXIT_STOP_LOSS_PCT == -8.0

    def test_tp_matches_config(self):
        assert EXIT_TAKE_PROFIT_PCT == TAKE_PROFIT_PCT
        assert EXIT_TAKE_PROFIT_PCT == 15.0
