"""
Execution & Paper Trading API Routes
File: backend/api/routes/execution.py

Provides HTTP endpoints for managing virtual orders, tracking positions,
monitoring account margin, and triggering the global emergency kill-switch.
Adheres strictly to docs/BE_rulebook.md.
"""

from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from backend.execution.simulated_broker import simulated_broker
from backend.rms.risk_manager import risk_manager

router = APIRouter(prefix="/api/execution", tags=["Execution & RMS"])


class OrderRequest(BaseModel):
    symbol: str = Field(..., pattern=r"^[A-Z0-9_\-\.&]{1,30}$", description="Valid NSE symbol")
    side: str = Field(..., pattern=r"^(BUY|SELL|buy|sell)$", description="Order direction")
    quantity: int = Field(..., gt=0, le=100000, description="Positive order quantity")
    price: float = Field(..., gt=0.0, description="Order price")
    order_type: str = Field("MARKET", description="MARKET, LIMIT, or SL")
    stop_loss: float = Field(0.0, ge=0.0, description="Stop loss price level")
    target: float = Field(0.0, ge=0.0, description="Take profit price level")


@router.get("/account")
async def get_account_summary():
    """Returns live virtual account balance, equity, margin used, and PnL."""
    return {
        "success": True,
        "data": simulated_broker.get_account_summary(),
    }


@router.get("/orders")
async def get_orders(status_filter: Optional[str] = Query(None, alias="status")):
    """Returns history of placed virtual orders."""
    orders = list(simulated_broker.orders.values())
    if status_filter:
        orders = [o for o in orders if o.get("status") == status_filter.upper()]
    return {
        "success": True,
        "count": len(orders),
        "data": sorted(orders, key=lambda x: x.get("created_at", ""), reverse=True),
    }


@router.post("/orders", status_code=status.HTTP_201_CREATED)
async def place_order(payload: OrderRequest):
    """Submits a new order through the RMS engine and executes simulated fill."""
    try:
        order = simulated_broker.place_order(
            symbol=payload.symbol.upper(),
            side=payload.side.upper(),
            quantity=payload.quantity,
            price=payload.price,
            order_type=payload.order_type.upper(),
            stop_loss=payload.stop_loss,
            target=payload.target,
        )
        if order.get("status") == "REJECTED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Order rejected by RMS: {order.get('metadata', {}).get('reject_reason', 'RMS risk gate')}",
            )
        return {
            "success": True,
            "data": order,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get("/positions")
async def get_positions():
    """Returns list of currently open positions with real-time unrealized PnL."""
    positions = list(simulated_broker.open_positions.values())
    return {
        "success": True,
        "count": len(positions),
        "data": positions,
    }


@router.post("/positions/{symbol}/close")
async def close_position(symbol: str):
    """Manually flattens an open position for a given symbol."""
    sym = symbol.upper()
    if sym not in simulated_broker.open_positions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No open position found for symbol {sym}",
        )
    pos = simulated_broker.open_positions[sym]
    order = simulated_broker.place_order(
        symbol=sym,
        side="SELL" if pos["side"] == "LONG" else "BUY",
        quantity=pos["quantity"],
        price=pos["current_price"],
        exit_reason="MANUAL_CLOSE",
        hit_type="MANUAL",
    )
    return {
        "success": True,
        "data": order,
    }


@router.post("/kill-switch")
async def trigger_kill_switch():
    """Emergency circuit breaker: cancels all pending orders, closes open positions, halts trading."""
    result = simulated_broker.cancel_all_and_flatten()
    return {
        "success": True,
        "data": result,
    }


@router.post("/kill-switch/reset")
async def reset_kill_switch():
    """Disengages the kill-switch and restores normal trading permissions."""
    risk_manager.reset_kill_switch()
    return {
        "success": True,
        "message": "Global Kill-Switch reset. Trading permissions restored.",
    }


@router.get("/analytics/{symbol}")
async def get_symbol_trade_analytics(symbol: str):
    """Returns open 1-share paper trade status and historical OB trade outcomes for a symbol."""
    from backend.storage.sqlite_store import sqlite_store
    sym = symbol.upper()
    open_pos = simulated_broker.open_positions.get(sym)
    history = sqlite_store.get_trade_analytics(symbol=sym, limit=10)
    return {
        "success": True,
        "symbol": sym,
        "open_position": open_pos,
        "history": history,
    }
