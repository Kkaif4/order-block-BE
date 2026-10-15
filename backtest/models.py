"""
Backtesting Models and Types
File: backend/backtest/models.py

Defines data structures for backtest configuration, simulated trade records,
performance metrics, and equity curve snapshots.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class BacktestConfig(BaseModel):
    """Configuration parameters for historical backtest run."""
    symbol: str
    initial_capital: float = Field(default=1000000.0, description="Starting capital in INR")
    risk_per_trade_pct: float = Field(default=1.0, description="Percent equity risked per trade")
    slippage_bps: float = Field(default=5.0, description="Slippage in basis points")
    fee_pct: float = Field(default=0.03, description="Exchange fees + STT roundtrip percentage")
    target_r_multiple: float = Field(default=2.5, description="Target reward-to-risk ratio")
    atr_stop_multiplier: float = Field(default=1.5, description="ATR multiplier for stop loss")
    max_open_trades: int = Field(default=1, description="Max concurrent open trades per symbol")


class TradeRecord(BaseModel):
    """Individual trade execution record."""
    trade_id: str
    symbol: str
    direction: Literal["BUY", "SELL"]
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: int
    gross_pnl: float
    fee_amount: float
    net_pnl: float
    net_return_pct: float
    exit_reason: Literal["TARGET", "STOP_LOSS", "INVALIDATION", "EOD_SQUAREOFF"]
    r_multiple_achieved: float


class EquityPoint(BaseModel):
    """Timestamped equity point for equity curve plotting."""
    timestamp: str
    equity: float
    drawdown_pct: float


class BacktestResult(BaseModel):
    """Aggregated backtest performance summary."""
    symbol: str
    start_time: str
    end_time: str
    initial_capital: float
    final_equity: float
    total_net_pnl: float
    total_net_return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    average_trade_pnl: float
    average_r_multiple: float
    equity_curve: List[EquityPoint] = Field(default_factory=list)
    trades: List[TradeRecord] = Field(default_factory=list)
