"""
Simulated Broker Execution Engine (Paper Trading Gateway)
File: backend/execution/simulated_broker.py

Simulates live broker order matching with:
- Realistic bid/ask spread modeling (5 bps).
- Slippage and order queue latency simulation.
- Real-time mark-to-market unrealized PnL calculation.
- Stop-Loss and Take-Profit automated triggers.
- Persistence to SQLite store.
Adheres strictly to docs/BE_rulebook.md.
"""

from __future__ import annotations
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

from backend.config import PAPER_TRADING_INITIAL_CAPITAL
from backend.rms.risk_manager import risk_manager
from backend.storage.sqlite_store import sqlite_store


class SimulatedBroker:
    def __init__(self, initial_capital: float = PAPER_TRADING_INITIAL_CAPITAL):
        self.initial_capital = initial_capital
        self.cash_balance = initial_capital
        self.open_positions: Dict[str, Dict[str, Any]] = {}  # symbol -> position dict
        self.orders: Dict[str, Dict[str, Any]] = {}          # order_id -> order dict
        self.slippage_bps = 5.0                             # 5 basis points simulated slippage
        self.traded_ob_ids: set[str] = set()
        try:
            self.traded_ob_ids.update(sqlite_store.get_all_traded_ob_ids())
        except Exception:
            pass

    @property
    def margin_used(self) -> float:
        return sum(p["quantity"] * p["current_price"] for p in self.open_positions.values())

    def get_account_summary(self) -> Dict[str, Any]:
        """Calculates live equity, margin used, and total PnL."""
        unrealized_total = sum(p.get("unrealized_pnl", 0.0) for p in self.open_positions.values())
        realized_total = sum(
            o.get("realized_pnl", 0.0) for o in self.orders.values() if o.get("status") == "FILLED"
        )
        equity = self.cash_balance + unrealized_total
        margin_used = sum(p["quantity"] * p["current_price"] for p in self.open_positions.values())

        return {
            "initial_capital": self.initial_capital,
            "cash_balance": round(self.cash_balance, 2),
            "equity": round(equity, 2),
            "margin_used": round(margin_used, 2),
            "available_margin": round(max(0.0, equity - margin_used), 2),
            "unrealized_pnl": round(unrealized_total, 2),
            "open_positions_count": len(self.open_positions),
            "kill_switch_active": risk_manager.is_kill_switch_active,
            "realized_daily_loss": round(risk_manager.realized_daily_loss, 2),
        }

    def place_order(
        self,
        symbol: str,
        side: str,                  # BUY or SELL
        quantity: int,
        price: float,
        order_type: str = "MARKET",
        stop_loss: float = 0.0,
        target: float = 0.0,
        exit_reason: str = "MANUAL",
        hit_type: str = "MANUAL",
        ob_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Submits an order through the RMS filter and executes simulated fill."""
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {side}. Must be BUY or SELL.")

        is_closing = False
        if symbol in self.open_positions:
            pos = self.open_positions[symbol]
            if (pos["side"] == "LONG" and side == "SELL") or (pos["side"] == "SHORT" and side == "BUY"):
                is_closing = True

        # 1. RMS Pre-Trade Validation
        allowed, reject_reason = risk_manager.check_order_allowed(
            symbol=symbol,
            side=side,
            requested_qty=quantity,
            price=price,
            current_open_positions_count=len(self.open_positions),
            is_closing=is_closing,
        )
        order_id = f"ORD-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        if not allowed:
            order_record = {
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "order_type": order_type,
                "quantity": quantity,
                "price": price,
                "stop_loss": stop_loss,
                "target": target,
                "status": "REJECTED",
                "fill_price": 0.0,
                "created_at": now_iso,
                "metadata": {"reject_reason": reject_reason},
            }
            self.orders[order_id] = order_record
            sqlite_store.save_order(order_record)
            return order_record

        # 2. Simulate Execution & Slippage
        slippage_factor = (self.slippage_bps / 10000.0)
        fill_price = price * (1.0 + slippage_factor) if side == "BUY" else price * (1.0 - slippage_factor)

        order_record = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "price": price,
            "stop_loss": stop_loss,
            "target": target,
            "status": "FILLED",
            "fill_price": round(fill_price, 2),
            "created_at": now_iso,
            "filled_at": now_iso,
            "metadata": {"slippage_bps": self.slippage_bps},
        }
        self.orders[order_id] = order_record
        sqlite_store.save_order(order_record)

        # 3. Update Position & Cash
        cost = fill_price * quantity
        if side == "BUY":
            # Check if closing an existing SHORT position
            if symbol in self.open_positions and self.open_positions[symbol]["side"] == "SHORT":
                pos = self.open_positions[symbol]
                realized = (pos["entry_price"] - fill_price) * quantity
                self.cash_balance += cost + realized
                risk_manager.record_pnl(realized)
                pos["status"] = "CLOSED"
                pos["closed_at"] = now_iso
                pos["realized_pnl"] = round(realized, 2)
                sqlite_store.save_position(pos)

                self._record_trade_analytics(
                    pos=pos,
                    exit_order_id=order_id,
                    exit_price=round(fill_price, 2),
                    exit_time=now_iso,
                    exit_reason=exit_reason,
                    hit_type=hit_type,
                    realized_pnl=round(realized, 2),
                    quantity=quantity,
                )
                del self.open_positions[symbol]
            elif symbol in self.open_positions and self.open_positions[symbol]["side"] == "LONG":
                # Add to existing long position
                existing = self.open_positions[symbol]
                total_qty = existing["quantity"] + quantity
                avg_entry = ((existing["quantity"] * existing["entry_price"]) + cost) / total_qty
                existing["quantity"] = total_qty
                existing["entry_price"] = round(avg_entry, 2)
                self.cash_balance -= cost
            else:
                self.cash_balance -= cost
                pos_id = f"POS-{symbol}-{int(time.time())}"
                new_pos = {
                    "position_id": pos_id,
                    "symbol": symbol,
                    "side": "LONG",
                    "quantity": quantity,
                    "entry_price": round(fill_price, 2),
                    "current_price": round(fill_price, 2),
                    "unrealized_pnl": 0.0,
                    "realized_pnl": 0.0,
                    "stop_loss": stop_loss,
                    "target": target,
                    "status": "OPEN",
                    "opened_at": now_iso,
                    "order_id": order_id,
                    "mfe": round(fill_price, 2),
                    "mae": round(fill_price, 2),
                    "chart_total_obs": (ob_metadata or {}).get("chart_total_obs", 0),
                    "chart_fresh_obs": (ob_metadata or {}).get("chart_fresh_obs", 0),
                    "chart_mitigated_obs": (ob_metadata or {}).get("chart_mitigated_obs", 0),
                    "chart_invalidated_obs": (ob_metadata or {}).get("chart_invalidated_obs", 0),
                    "active_ob_id": (ob_metadata or {}).get("active_ob_id"),
                    "active_ob_formed_at": (ob_metadata or {}).get("active_ob_formed_at"),
                    "active_ob_confirmed_at": (ob_metadata or {}).get("active_ob_confirmed_at"),
                    "active_ob_state": (ob_metadata or {}).get("active_ob_state"),
                }
                self.open_positions[symbol] = new_pos
                sqlite_store.save_position(new_pos)
        else:
            # SELL / Short or Close Long
            if symbol in self.open_positions and self.open_positions[symbol]["side"] == "LONG":
                # Closing long position
                pos = self.open_positions[symbol]
                realized = (fill_price - pos["entry_price"]) * quantity
                self.cash_balance += cost + realized
                risk_manager.record_pnl(realized)
                pos["status"] = "CLOSED"
                pos["closed_at"] = now_iso
                pos["realized_pnl"] = round(realized, 2)
                sqlite_store.save_position(pos)

                self._record_trade_analytics(
                    pos=pos,
                    exit_order_id=order_id,
                    exit_price=round(fill_price, 2),
                    exit_time=now_iso,
                    exit_reason=exit_reason,
                    hit_type=hit_type,
                    realized_pnl=round(realized, 2),
                    quantity=quantity,
                )
                del self.open_positions[symbol]
            elif symbol in self.open_positions and self.open_positions[symbol]["side"] == "SHORT":
                # Add to existing short position
                existing = self.open_positions[symbol]
                total_qty = existing["quantity"] + quantity
                avg_entry = ((existing["quantity"] * existing["entry_price"]) + cost) / total_qty
                existing["quantity"] = total_qty
                existing["entry_price"] = round(avg_entry, 2)
                self.cash_balance -= cost
            else:
                # Open new SHORT position
                self.cash_balance -= cost
                pos_id = f"POS-{symbol}-{int(time.time())}"
                new_pos = {
                    "position_id": pos_id,
                    "symbol": symbol,
                    "side": "SHORT",
                    "quantity": quantity,
                    "entry_price": round(fill_price, 2),
                    "current_price": round(fill_price, 2),
                    "unrealized_pnl": 0.0,
                    "realized_pnl": 0.0,
                    "stop_loss": stop_loss,
                    "target": target,
                    "status": "OPEN",
                    "opened_at": now_iso,
                    "order_id": order_id,
                    "mfe": round(fill_price, 2),
                    "mae": round(fill_price, 2),
                    "chart_total_obs": (ob_metadata or {}).get("chart_total_obs", 0),
                    "chart_fresh_obs": (ob_metadata or {}).get("chart_fresh_obs", 0),
                    "chart_mitigated_obs": (ob_metadata or {}).get("chart_mitigated_obs", 0),
                    "chart_invalidated_obs": (ob_metadata or {}).get("chart_invalidated_obs", 0),
                    "active_ob_id": (ob_metadata or {}).get("active_ob_id"),
                    "active_ob_formed_at": (ob_metadata or {}).get("active_ob_formed_at"),
                    "active_ob_confirmed_at": (ob_metadata or {}).get("active_ob_confirmed_at"),
                    "active_ob_state": (ob_metadata or {}).get("active_ob_state"),
                }
                self.open_positions[symbol] = new_pos
                sqlite_store.save_position(new_pos)

        return order_record

    def execute_auto_ob_trade(
        self,
        symbol: str,
        board: str,                # BULLISH or BEARISH
        spot_price: float,
        stop_loss: float,
        target: float,
        ob_id: Optional[str] = None,
        ob_formed_at: Optional[str] = None,
        ob_state: Optional[str] = "FRESH",
    ) -> Optional[Dict[str, Any]]:
        """
        Executes an automated 1-share paper trade immediately when an Order Block is placed.
        Buy 1 share for Bullish OB; Sell/Short 1 share for Bearish OB.
        Monitors TP/SL immediately against incoming market ticks.
        """
        if risk_manager.is_kill_switch_active:
            return None

        # STRICT RULE: Do not make multiple trades in a single order block
        if ob_id and ob_id in self.traded_ob_ids:
            return None

        sym = symbol.upper()
        if sym in self.open_positions:
            return None

        is_bull = board.upper() == "BULLISH"
        side = "BUY" if is_bull else "SELL"
        min_risk = max(round(spot_price * 0.005, 2), 0.05)

        # Directional Stop Loss & Target calculation
        if is_bull:
            if stop_loss >= spot_price:
                stop_loss = round(spot_price - min_risk, 2)
            risk = max(min_risk, spot_price - stop_loss)
            if target <= spot_price:
                target = round(spot_price + risk * 2.5, 2)
        else:
            if stop_loss <= spot_price:
                stop_loss = round(spot_price + min_risk, 2)
            risk = max(min_risk, stop_loss - spot_price)
            if target >= spot_price:
                target = max(0.05, round(spot_price - risk * 2.5, 2))

        ob_metadata = {
            "chart_total_obs": 1,
            "chart_fresh_obs": 1,
            "chart_mitigated_obs": 0,
            "chart_invalidated_obs": 0,
            "active_ob_id": ob_id,
            "active_ob_formed_at": ob_formed_at,
            "active_ob_confirmed_at": None,
            "active_ob_state": ob_state or "FRESH",
        }

        try:
            order = self.place_order(
                symbol=sym,
                side=side,
                quantity=1,
                price=spot_price,
                order_type="MARKET",
                stop_loss=stop_loss,
                target=target,
                exit_reason="AUTO_OB_ENTRY",
                hit_type="ENTRY",
                ob_metadata=ob_metadata,
            )
            if order and order.get("status") == "FILLED" and ob_id:
                self.traded_ob_ids.add(ob_id)
            return order
        except Exception:
            return None

    def _record_trade_analytics(
        self,
        pos: Dict[str, Any],
        exit_order_id: str,
        exit_price: float,
        exit_time: str,
        exit_reason: str,
        hit_type: str,
        realized_pnl: float,
        quantity: int,
    ) -> None:
        """Stores completed trade analytics with Target/SL hit timing and chart OB snapshot."""
        try:
            entry_price = float(pos.get("entry_price", 0.0))
            stop_loss = float(pos.get("stop_loss", 0.0))
            target = float(pos.get("target", 0.0))
            entry_time = pos.get("opened_at", exit_time)
            is_short = pos.get("side") == "SHORT"

            if is_short:
                return_pct = round(((entry_price - exit_price) / entry_price) * 100.0, 2) if entry_price > 0 else 0.0
                risk_per_share = stop_loss - entry_price
                if risk_per_share > 0.01:
                    r_multiple = round((entry_price - exit_price) / risk_per_share, 2)
                else:
                    r_multiple = 1.0 if realized_pnl > 0 else (-1.0 if realized_pnl < 0 else 0.0)
            else:
                return_pct = round(((exit_price - entry_price) / entry_price) * 100.0, 2) if entry_price > 0 else 0.0
                risk_per_share = entry_price - stop_loss
                if risk_per_share > 0.01:
                    r_multiple = round((exit_price - entry_price) / risk_per_share, 2)
                else:
                    r_multiple = 1.0 if realized_pnl > 0 else (-1.0 if realized_pnl < 0 else 0.0)

            holding_seconds = 0
            try:
                t_in = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                t_out = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))
                holding_seconds = max(0, int((t_out - t_in).total_seconds()))
            except Exception:
                pass

            trade_record = {
                "trade_id": f"TRD-{pos.get('symbol', 'SYM')}-{int(time.time())}-{uuid.uuid4().hex[:4]}",
                "position_id": pos.get("position_id"),
                "order_id": pos.get("order_id", exit_order_id),
                "symbol": pos.get("symbol"),
                "side": pos.get("side", "LONG"),
                "entry_time": entry_time,
                "entry_price": entry_price,
                "quantity": quantity,
                "target_price": target,
                "stop_loss_price": stop_loss,
                "exit_time": exit_time,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "hit_type": hit_type,
                "realized_pnl": realized_pnl,
                "return_pct": return_pct,
                "r_multiple_achieved": r_multiple,
                "mfe": float(pos.get("mfe", exit_price)),
                "mae": float(pos.get("mae", entry_price)),
                "holding_seconds": holding_seconds,
                "chart_total_obs": int(pos.get("chart_total_obs", 0)),
                "chart_fresh_obs": int(pos.get("chart_fresh_obs", 0)),
                "chart_mitigated_obs": int(pos.get("chart_mitigated_obs", 0)),
                "chart_invalidated_obs": int(pos.get("chart_invalidated_obs", 0)),
                "active_ob_id": pos.get("active_ob_id"),
                "active_ob_formed_at": pos.get("active_ob_formed_at"),
                "active_ob_confirmed_at": pos.get("active_ob_confirmed_at"),
                "active_ob_state": pos.get("active_ob_state"),
                "metadata": {
                    "entry_order_id": pos.get("order_id"),
                    "exit_order_id": exit_order_id,
                }
            }
            sqlite_store.save_trade_analytics(trade_record)
        except Exception:
            pass

    def on_market_tick(self, symbol: str, current_price: float):
        """Updates open position unrealized PnL, tracks MFE/MAE, and monitors Stop-Loss / Take-Profit immediately."""
        if symbol not in self.open_positions or not math.isfinite(current_price) or current_price <= 0.0:
            return

        pos = self.open_positions[symbol]
        pos["current_price"] = current_price
        if pos["side"] == "LONG":
            pos["unrealized_pnl"] = round((current_price - pos["entry_price"]) * pos["quantity"], 2)
            pos["mfe"] = max(float(pos.get("mfe", current_price)), current_price)
            pos["mae"] = min(float(pos.get("mae", current_price)), current_price)

            # Check Stop-Loss Hit (LONG)
            if pos.get("stop_loss", 0.0) > 0.0 and current_price <= pos["stop_loss"]:
                self.place_order(
                    symbol=symbol,
                    side="SELL",
                    quantity=pos["quantity"],
                    price=current_price,
                    exit_reason="STOP_LOSS_HIT",
                    hit_type="STOP_LOSS"
                )

            # Check Take-Profit Hit (LONG)
            elif pos.get("target", 0.0) > 0.0 and current_price >= pos["target"]:
                self.place_order(
                    symbol=symbol,
                    side="SELL",
                    quantity=pos["quantity"],
                    price=current_price,
                    exit_reason="TARGET_HIT",
                    hit_type="TARGET"
                )
        elif pos["side"] == "SHORT":
            pos["unrealized_pnl"] = round((pos["entry_price"] - current_price) * pos["quantity"], 2)
            pos["mfe"] = min(float(pos.get("mfe", current_price)), current_price)
            pos["mae"] = max(float(pos.get("mae", current_price)), current_price)

            # Check Stop-Loss Hit (SHORT: price spiked above SL)
            if pos.get("stop_loss", 0.0) > 0.0 and current_price >= pos["stop_loss"]:
                self.place_order(
                    symbol=symbol,
                    side="BUY",
                    quantity=pos["quantity"],
                    price=current_price,
                    exit_reason="STOP_LOSS_HIT",
                    hit_type="STOP_LOSS"
                )

            # Check Take-Profit Hit (SHORT: price fell to/below Target)
            elif pos.get("target", 0.0) > 0.0 and current_price <= pos["target"]:
                self.place_order(
                    symbol=symbol,
                    side="BUY",
                    quantity=pos["quantity"],
                    price=current_price,
                    exit_reason="TARGET_HIT",
                    hit_type="TARGET"
                )

    def cancel_all_and_flatten(self) -> Dict[str, Any]:
        """Emergency Kill-Switch handler: flattens all open positions and freezes account."""
        risk_manager.trigger_kill_switch()
        closed_count = 0
        symbols_to_close = list(self.open_positions.keys())
        for sym in symbols_to_close:
            pos = self.open_positions[sym]
            self.place_order(
                symbol=sym,
                side="SELL" if pos["side"] == "LONG" else "BUY",
                quantity=pos["quantity"],
                price=pos["current_price"],
                exit_reason="KILL_SWITCH",
                hit_type="MANUAL"
            )
            closed_count += 1

        return {
            "kill_switch_engaged": True,
            "positions_closed": closed_count,
            "message": "All open positions flattened and order execution halted.",
        }


simulated_broker = SimulatedBroker()
