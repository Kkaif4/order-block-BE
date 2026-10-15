"""
Risk Management System (RMS)
File: backend/rms/risk_manager.py

Enforces institutional capital protection and trading constraints:
- Hard daily loss limits (circuit breaker halts new orders).
- Max concurrent open positions guard.
- Dynamic ATR-based position sizing.
- Global Kill-Switch execution.
Adheres strictly to docs/BE_rulebook.md.
"""

from __future__ import annotations
import math
from typing import Dict, Any, Optional, Tuple

from backend.config import (
    RMS_MAX_DAILY_LOSS_PCT,
    RMS_MAX_OPEN_POSITIONS,
    RMS_RISK_PER_TRADE_PCT,
    PAPER_TRADING_INITIAL_CAPITAL,
)


class RiskManager:
    def __init__(
        self,
        capital: float = PAPER_TRADING_INITIAL_CAPITAL,
        max_daily_loss_pct: float = RMS_MAX_DAILY_LOSS_PCT,
        max_open_positions: int = RMS_MAX_OPEN_POSITIONS,
        risk_per_trade_pct: float = RMS_RISK_PER_TRADE_PCT,
    ):
        self.capital = capital
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_open_positions = max_open_positions
        self.risk_per_trade_pct = risk_per_trade_pct
        self.realized_daily_loss = 0.0
        self.is_kill_switch_active = False

    def check_order_allowed(
        self,
        symbol: str,
        side: str,
        requested_qty: int,
        price: float,
        current_open_positions_count: int,
        is_closing: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """Validates whether an order complies with all RMS rules."""
        # Closing/flattening orders to reduce risk are always allowed
        if is_closing:
            if not math.isfinite(price) or price <= 0.0 or requested_qty <= 0:
                return False, "RMS_REJECT: Invalid closing price or quantity."
            return True, None

        if self.is_kill_switch_active:
            return False, "RMS_REJECT: Global Kill-Switch is active. All new order creation is halted."

        # 1. Daily Loss Limit Circuit Breaker
        max_loss_amount = (self.max_daily_loss_pct / 100.0) * self.capital
        if self.realized_daily_loss >= max_loss_amount:
            return False, f"RMS_REJECT: Hard daily loss limit reached ({self.realized_daily_loss:.2f} >= {max_loss_amount:.2f}). Trading halted for session."

        # 2. Maximum Concurrent Positions Guard
        if current_open_positions_count >= self.max_open_positions:
            return False, f"RMS_REJECT: Maximum open positions limit ({self.max_open_positions}) reached."

        # 3. Numeric Geometry & Sanity Checks
        if not math.isfinite(price) or price <= 0.0:
            return False, "RMS_REJECT: Non-positive or non-finite price."
        if requested_qty <= 0:
            return False, "RMS_REJECT: Quantity must be a positive integer."

        # 4. Single-Order Capital Allocation Cap (max 25% of total capital per position)
        order_value = price * requested_qty
        if order_value > 0.25 * self.capital:
            return False, f"RMS_REJECT: Order value ({order_value:.2f}) exceeds single-trade 25% capital limit ({0.25 * self.capital:.2f})."

        return True, None

    def calculate_position_size(self, price: float, atr: float) -> int:
        """
        Calculates safe position size based on fractional capital risk and ATR volatility stop.
        """
        if not math.isfinite(price) or price <= 0.0 or not math.isfinite(atr) or atr <= 0.0:
            return 1

        risk_amount = (self.risk_per_trade_pct / 100.0) * self.capital
        per_share_risk = max(1.5 * atr, price * 0.005)  # Minimum 50 bps risk
        qty = math.floor(risk_amount / per_share_risk)
        return max(1, min(qty, int((0.25 * self.capital) / price)))

    def record_pnl(self, realized_pnl: float):
        """Updates cumulative daily realized loss."""
        if realized_pnl < 0:
            self.realized_daily_loss += abs(realized_pnl)

    def trigger_kill_switch(self):
        """Immediately engages global circuit breaker."""
        self.is_kill_switch_active = True

    def reset_kill_switch(self):
        """Manually disengages global circuit breaker."""
        self.is_kill_switch_active = False


risk_manager = RiskManager()
