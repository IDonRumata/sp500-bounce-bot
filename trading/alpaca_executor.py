"""
Alpaca Paper Trading Executor
Handles order execution, position management and account monitoring
for the S&P 500 Bounce Bot strategy validation.

Modes:
  auto   — all recommendations auto-execute immediately
  hybrid — sends approval buttons to admin, executes on confirm (4h timeout)
  off    — paper trading disabled
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY,
    PAPER_POSITION_SIZE_USD, PAPER_INITIAL_BALANCE,
    logger,
)

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    logger.warning("alpaca-py not installed. Paper trading disabled. Run: pip install alpaca-py")


class AlpacaExecutor:
    """Singleton wrapper around Alpaca Paper Trading API."""

    _instance: Optional["AlpacaExecutor"] = None

    def __init__(self):
        if not ALPACA_AVAILABLE:
            raise RuntimeError("alpaca-py not installed. Run: pip install alpaca-py")
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env")

        self.client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
        logger.info("[Alpaca] Executor initialized (paper=True)")

    @classmethod
    def get_instance(cls) -> Optional["AlpacaExecutor"]:
        """Return singleton. Returns None if Alpaca not configured."""
        if cls._instance is None:
            try:
                cls._instance = cls()
            except (RuntimeError, ValueError) as e:
                logger.warning(f"[Alpaca] Not available: {e}")
                return None
        return cls._instance

    @classmethod
    def reset(cls):
        """Force re-init (useful if credentials changed)."""
        cls._instance = None

    # ── Orders ─────────────────────────────────────────────────────────────

    def open_position(self, symbol: str, notional_usd: float = PAPER_POSITION_SIZE_USD) -> dict:
        """
        Submit a market BUY order for `symbol` using dollar amount (notional).
        Works pre-market — order queues and fills at next open.
        Returns: {status, order_id, symbol, qty, filled_price, order_status}
        """
        try:
            order = MarketOrderRequest(
                symbol=symbol,
                notional=round(notional_usd, 2),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
            result = self.client.submit_order(order)

            filled_price = float(result.filled_avg_price) if result.filled_avg_price else None
            filled_qty = float(result.filled_qty) if result.filled_qty else None

            logger.info(
                f"[Alpaca] BUY submitted: {symbol} ${notional_usd:.0f} | "
                f"order_id={result.id} | status={result.status}"
            )
            return {
                "status": "success",
                "order_id": str(result.id),
                "symbol": symbol,
                "notional": notional_usd,
                "qty": filled_qty,
                "filled_price": filled_price,
                "order_status": result.status.value if result.status else "submitted",
            }
        except Exception as e:
            logger.error(f"[Alpaca] Error opening {symbol}: {e}")
            return {"status": "error", "symbol": symbol, "error": str(e)}

    def close_position(self, symbol: str) -> dict:
        """
        Close entire open position for `symbol` at market.
        Returns: {status, order_id, symbol}
        """
        try:
            result = self.client.close_position(symbol)
            filled_price = float(result.filled_avg_price) if result.filled_avg_price else None

            logger.info(f"[Alpaca] SELL submitted: {symbol} | order_id={result.id}")
            return {
                "status": "success",
                "order_id": str(result.id),
                "symbol": symbol,
                "filled_price": filled_price,
            }
        except Exception as e:
            logger.error(f"[Alpaca] Error closing {symbol}: {e}")
            return {"status": "error", "symbol": symbol, "error": str(e)}

    def get_order_fill(self, order_id: str) -> dict:
        """Poll order fill details (for confirming after-hours queued orders)."""
        try:
            order = self.client.get_order_by_id(order_id)
            return {
                "status": order.status.value,
                "filled_price": float(order.filled_avg_price) if order.filled_avg_price else None,
                "filled_qty": float(order.filled_qty) if order.filled_qty else None,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ── Positions ───────────────────────────────────────────────────────────

    def get_open_positions(self) -> list[dict]:
        """Return all open positions with unrealized P&L."""
        try:
            positions = self.client.get_all_positions()
            return [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "avg_entry": float(p.avg_entry_price),
                    "current_price": float(p.current_price) if p.current_price else None,
                    "unrealized_pl": float(p.unrealized_pl) if p.unrealized_pl else None,
                    "unrealized_plpc": float(p.unrealized_plpc) * 100 if p.unrealized_plpc else None,
                    "market_value": float(p.market_value) if p.market_value else None,
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"[Alpaca] Error getting positions: {e}")
            return []

    def position_exists(self, symbol: str) -> bool:
        """Check if an open position exists for symbol."""
        try:
            self.client.get_open_position(symbol)
            return True
        except Exception:
            return False

    # ── Account ─────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        """
        Return account summary with P&L vs starting $100k.
        Returns: {equity, portfolio_value, cash, buying_power, total_pl, total_pl_pct}
        """
        try:
            acc = self.client.get_account()
            equity = float(acc.equity)
            cash = float(acc.cash)
            portfolio_value = float(acc.portfolio_value)
            buying_power = float(acc.buying_power)
            total_pl = equity - PAPER_INITIAL_BALANCE
            total_pl_pct = (total_pl / PAPER_INITIAL_BALANCE) * 100

            return {
                "equity": equity,
                "portfolio_value": portfolio_value,
                "cash": cash,
                "buying_power": buying_power,
                "total_pl": total_pl,
                "total_pl_pct": total_pl_pct,
                "initial_balance": PAPER_INITIAL_BALANCE,
            }
        except Exception as e:
            logger.error(f"[Alpaca] Error getting account: {e}")
            return {}
