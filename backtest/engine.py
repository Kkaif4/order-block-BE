"""
Backtesting Engine for SMC Order Block Strategies
File: backend/backtest/engine.py

Runs event-driven backtests over historical 5-minute candles:
- Pure Python standard library (math, typing, datetime).
- Evaluates Order Block signals (FRESH mitigation retests).
- Models ATR stops, R-multiples, slippage (bps), and transaction fees.
- Generates institutional performance metrics (Sharpe, Sortino, Max Drawdown, Profit Factor).
"""

from __future__ import annotations
import math
from typing import List, Optional, Tuple
from backend.core.schemas import Candle5m, ZoneLifecycleState
from backend.detectors.ob_detector import OrderBlockDetector, OrderBlockZone
from backend.detectors.swing_detector import SwingDetector
from backend.backtest.models import (
    BacktestConfig,
    BacktestResult,
    TradeRecord,
    EquityPoint,
)


class BacktestEngine:
    """Historical backtester evaluating SMC Order Block setups."""

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig(symbol="DEFAULT")
        self.ob_detector = OrderBlockDetector(
            swing_detector=SwingDetector(left_bars=2, right_bars=2)
        )

    def calculate_atr(self, candles: List[Candle5m], period: int = 14) -> List[float]:
        """Calculates True Range and rolling Average True Range."""
        if len(candles) < 2:
            return [0.0] * len(candles)

        tr_values: List[float] = [candles[0].high - candles[0].low]
        for i in range(1, len(candles)):
            curr = candles[i]
            prev = candles[i - 1]
            tr = max(
                curr.high - curr.low,
                abs(curr.high - prev.close),
                abs(curr.low - prev.close)
            )
            tr_values.append(tr)

        # Simple moving average for ATR initial, then Wilder smoothing
        atr: List[float] = [0.0] * len(candles)
        if len(candles) < period:
            avg_tr = sum(tr_values) / max(1, len(tr_values))
            return [avg_tr] * len(candles)

        current_atr = sum(tr_values[:period]) / period
        for i in range(period - 1):
            atr[i] = current_atr
        atr[period - 1] = current_atr

        for i in range(period, len(candles)):
            current_atr = (current_atr * (period - 1) + tr_values[i]) / period
            atr[i] = current_atr

        return atr

    def run(self, candles: List[Candle5m]) -> BacktestResult:
        """Executes the backtest across the provided 5m candle array."""
        if len(candles) < 10:
            return self._empty_result(candles)

        atr_series = self.calculate_atr(candles, period=14)
        active_zones: List[OrderBlockZone] = []
        trades: List[TradeRecord] = []
        equity_curve: List[EquityPoint] = []

        capital = self.config.initial_capital
        peak_capital = capital
        slippage_factor = self.config.slippage_bps / 10000.0
        fee_rate = self.config.fee_pct / 100.0

        equity_curve.append(EquityPoint(
            timestamp=candles[0].timestamp,
            equity=round(capital, 2),
            drawdown_pct=0.0
        ))

        # Active trade state
        open_trade: Optional[dict] = None

        for i in range(5, len(candles)):
            curr_bar = candles[i]
            prev_bar = candles[i - 1]
            atr = atr_series[i] if i < len(atr_series) and atr_series[i] > 0 else (curr_bar.high - curr_bar.low)
            atr = max(atr, curr_bar.close * 0.001)  # Safeguard minimum 10 bps ATR

            # 1. Update/check existing open trade
            if open_trade is not None:
                direction = open_trade["direction"]
                qty = open_trade["quantity"]
                entry_px = open_trade["entry_price"]
                sl_px = open_trade["stop_loss"]
                tp_px = open_trade["target"]
                risk_amount = open_trade["risk_amount"]
                entry_time = open_trade["entry_time"]
                zone_ref = open_trade["zone"]

                exit_trade = False
                exit_price = 0.0
                exit_reason = ""

                # Check EOD square-off (e.g. 15:20 IST onwards)
                is_eod = ":25:00" in curr_bar.timestamp or ":20:00" in curr_bar.timestamp or ":30:00" in curr_bar.timestamp
                if is_eod and "15:" in curr_bar.timestamp:
                    exit_trade = True
                    exit_reason = "EOD_SQUAREOFF"
                    exit_price = curr_bar.close

                elif direction == "BUY":
                    # Check Target hit
                    if curr_bar.high >= tp_px:
                        exit_trade = True
                        exit_reason = "TARGET"
                        exit_price = tp_px
                    # Check Stop Loss hit
                    elif curr_bar.low <= sl_px:
                        exit_trade = True
                        exit_reason = "STOP_LOSS"
                        exit_price = sl_px
                    # Check Zone invalidation body close
                    elif curr_bar.close < zone_ref.invalidation_level:
                        exit_trade = True
                        exit_reason = "INVALIDATION"
                        exit_price = curr_bar.close

                elif direction == "SELL":
                    if curr_bar.low <= tp_px:
                        exit_trade = True
                        exit_reason = "TARGET"
                        exit_price = tp_px
                    elif curr_bar.high >= sl_px:
                        exit_trade = True
                        exit_reason = "STOP_LOSS"
                        exit_price = sl_px
                    elif curr_bar.close > zone_ref.invalidation_level:
                        exit_trade = True
                        exit_reason = "INVALIDATION"
                        exit_price = curr_bar.close

                if exit_trade:
                    # Apply exit slippage
                    if direction == "BUY":
                        exec_exit_price = exit_price * (1.0 - slippage_factor)
                        gross_pnl = (exec_exit_price - entry_px) * qty
                    else:
                        exec_exit_price = exit_price * (1.0 + slippage_factor)
                        gross_pnl = (entry_px - exec_exit_price) * qty

                    turnover = (entry_px + exec_exit_price) * qty
                    fee = turnover * fee_rate
                    net_pnl = gross_pnl - fee
                    capital += net_pnl

                    r_multiple = net_pnl / max(risk_amount, 1.0)
                    ret_pct = (net_pnl / (entry_px * qty)) * 100.0 if (entry_px * qty) > 0 else 0.0

                    trade_rec = TradeRecord(
                        trade_id=f"BT-{self.config.symbol}-{len(trades)+1}",
                        symbol=self.config.symbol,
                        direction=direction,
                        entry_time=entry_time,
                        exit_time=curr_bar.timestamp,
                        entry_price=round(entry_px, 2),
                        exit_price=round(exec_exit_price, 2),
                        quantity=qty,
                        gross_pnl=round(gross_pnl, 2),
                        fee_amount=round(fee, 2),
                        net_pnl=round(net_pnl, 2),
                        net_return_pct=round(ret_pct, 2),
                        exit_reason=exit_reason,  # type: ignore[arg-type]
                        r_multiple_achieved=round(r_multiple, 2)
                    )
                    trades.append(trade_rec)
                    open_trade = None

                    # Track equity curve
                    peak_capital = max(peak_capital, capital)
                    dd_pct = ((peak_capital - capital) / peak_capital) * 100.0 if peak_capital > 0 else 0.0
                    equity_curve.append(EquityPoint(
                        timestamp=curr_bar.timestamp,
                        equity=round(capital, 2),
                        drawdown_pct=round(dd_pct, 2)
                    ))

            # 2. Update Order Blocks detection on completed bars up to curr_bar
            active_zones = self.ob_detector.detect_order_blocks(
                symbol=self.config.symbol,
                candles=candles[:i + 1],
                existing_zones=active_zones
            )

            # 3. Check for new setup entry if no open trade
            if open_trade is None and i < len(candles) - 1:
                for zone in active_zones:
                    # Setup qualifies when zone is FRESH and price retests the zone
                    if zone.state == ZoneLifecycleState.FRESH:
                        if zone.direction == "BULLISH":
                            # Retest entry inside bullish zone
                            if curr_bar.low <= zone.zone_high and curr_bar.close >= zone.zone_low:
                                entry_raw = curr_bar.close
                                entry_px = entry_raw * (1.0 + slippage_factor)
                                sl_dist = max(entry_px - zone.invalidation_level, self.config.atr_stop_multiplier * atr)
                                sl_px = entry_px - sl_dist
                                tp_px = entry_px + (sl_dist * self.config.target_r_multiple)

                                risk_capital = capital * (self.config.risk_per_trade_pct / 100.0)
                                qty = int(risk_capital / max(sl_dist, 0.5))
                                if qty > 0 and (qty * entry_px) <= (capital * 2.0):  # max 2x leverage
                                    open_trade = {
                                        "direction": "BUY",
                                        "entry_price": entry_px,
                                        "stop_loss": sl_px,
                                        "target": tp_px,
                                        "quantity": qty,
                                        "risk_amount": risk_capital,
                                        "entry_time": curr_bar.timestamp,
                                        "zone": zone
                                    }
                                    zone.state = ZoneLifecycleState.MITIGATED
                                    break

                        elif zone.direction == "BEARISH":
                            if curr_bar.high >= zone.zone_low and curr_bar.close <= zone.zone_high:
                                entry_raw = curr_bar.close
                                entry_px = entry_raw * (1.0 - slippage_factor)
                                sl_dist = max(zone.invalidation_level - entry_px, self.config.atr_stop_multiplier * atr)
                                sl_px = entry_px + sl_dist
                                tp_px = entry_px - (sl_dist * self.config.target_r_multiple)

                                risk_capital = capital * (self.config.risk_per_trade_pct / 100.0)
                                qty = int(risk_capital / max(sl_dist, 0.5))
                                if qty > 0 and (qty * entry_px) <= (capital * 2.0):
                                    open_trade = {
                                        "direction": "SELL",
                                        "entry_price": entry_px,
                                        "stop_loss": sl_px,
                                        "target": tp_px,
                                        "quantity": qty,
                                        "risk_amount": risk_capital,
                                        "entry_time": curr_bar.timestamp,
                                        "zone": zone
                                    }
                                    zone.state = ZoneLifecycleState.MITIGATED
                                    break

        # Compute summary metrics
        return self._compile_results(candles, capital, trades, equity_curve)

    def _compile_results(
        self,
        candles: List[Candle5m],
        final_capital: float,
        trades: List[TradeRecord],
        equity_curve: List[EquityPoint]
    ) -> BacktestResult:
        """Computes statistical metrics and return distributions."""
        total_trades = len(trades)
        initial_capital = self.config.initial_capital
        total_net_pnl = final_capital - initial_capital
        total_return_pct = (total_net_pnl / initial_capital) * 100.0

        if total_trades == 0:
            return BacktestResult(
                symbol=self.config.symbol,
                start_time=candles[0].timestamp,
                end_time=candles[-1].timestamp,
                initial_capital=initial_capital,
                final_equity=round(final_capital, 2),
                total_net_pnl=0.0,
                total_net_return_pct=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                profit_factor=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                average_trade_pnl=0.0,
                average_r_multiple=0.0,
                equity_curve=equity_curve,
                trades=trades
            )

        winning = [t for t in trades if t.net_pnl > 0]
        losing = [t for t in trades if t.net_pnl <= 0]
        win_rate = (len(winning) / total_trades) * 100.0

        gross_profit = sum(t.net_pnl for t in winning)
        gross_loss = abs(sum(t.net_pnl for t in losing))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0

        # Max drawdown from equity curve
        max_dd = max((p.drawdown_pct for p in equity_curve), default=0.0)

        # Sharpe & Sortino (annualized based on trade returns)
        trade_returns = [t.net_pnl / initial_capital for t in trades]
        mean_ret = sum(trade_returns) / total_trades
        variance = sum((r - mean_ret) ** 2 for r in trade_returns) / max(1, total_trades - 1)
        std_dev = math.sqrt(variance)

        downside_returns = [r for r in trade_returns if r < 0]
        downside_var = sum(r ** 2 for r in downside_returns) / max(1, len(downside_returns))
        downside_std = math.sqrt(downside_var)

        # Normalizing to ~252 trading periods
        annual_factor = math.sqrt(min(252, total_trades * 4))
        sharpe = round((mean_ret / std_dev) * annual_factor, 2) if std_dev > 1e-6 else 0.0
        sortino = round((mean_ret / downside_std) * annual_factor, 2) if downside_std > 1e-6 else 0.0

        avg_pnl = total_net_pnl / total_trades
        avg_r = sum(t.r_multiple_achieved for t in trades) / total_trades

        return BacktestResult(
            symbol=self.config.symbol,
            start_time=candles[0].timestamp,
            end_time=candles[-1].timestamp,
            initial_capital=initial_capital,
            final_equity=round(final_capital, 2),
            total_net_pnl=round(total_net_pnl, 2),
            total_net_return_pct=round(total_return_pct, 2),
            total_trades=total_trades,
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate_pct=round(win_rate, 2),
            profit_factor=profit_factor,
            max_drawdown_pct=round(max_dd, 2),
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            average_trade_pnl=round(avg_pnl, 2),
            average_r_multiple=round(avg_r, 2),
            equity_curve=equity_curve,
            trades=trades
        )

    def _empty_result(self, candles: List[Candle5m]) -> BacktestResult:
        st = candles[0].timestamp if candles else ""
        et = candles[-1].timestamp if candles else ""
        return BacktestResult(
            symbol=self.config.symbol,
            start_time=st,
            end_time=et,
            initial_capital=self.config.initial_capital,
            final_equity=self.config.initial_capital,
            total_net_pnl=0.0,
            total_net_return_pct=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate_pct=0.0,
            profit_factor=0.0,
            max_drawdown_pct=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            average_trade_pnl=0.0,
            average_r_multiple=0.0,
            equity_curve=[],
            trades=[]
        )
