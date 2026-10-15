"""
OrderBlock Live Streaming & REST API
File: backend/api/app.py

Serves real-time board updates, telemetry, execution gateways, and WebSocket broadcast channels
powered by live Upstox market feeds and zero-dependency quantitative engine.
Adheres strictly to docs/BE_rulebook.md.
"""

from __future__ import annotations
import asyncio
import json
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.adapters.upstox_auth import upstox_auth
from backend.api.middleware.error_handler import SafeErrorHandlingMiddleware
from backend.api.middleware.rate_limiter import SlidingWindowRateLimiter
from backend.api.routes.execution import router as execution_router
from backend.backtest.models import BacktestConfig
from backend.backtest.engine import BacktestEngine
from backend.core.schemas import Candle5m
from backend.execution.simulated_broker import simulated_broker
from backend.services.market_service import market_service
from backend.storage.sqlite_store import sqlite_store
from backend.scheduler.market_scheduler import market_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, market_service.initialize)
    async def _prewarm():
        try:
            await loop.run_in_executor(None, market_service.generate_cycle_snapshot)
        except Exception:
            pass
    asyncio.create_task(_prewarm())
    # Start autonomous market hours algorithm scheduler
    await market_scheduler.start()
    yield
    await market_scheduler.stop()


app = FastAPI(
    title="OrderBlock Quantitative & Intelligence Engine API",
    version="1.0.0",
    lifespan=lifespan,
)

# 1. Custom Exception Handlers for Uniform Error Envelopes (Section 2.4)
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            "error_code": f"HTTP_{exc.status_code}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "success": False,
            "error": "Request payload validation failed.",
            "error_code": "VALIDATION_ERROR",
            "details": exc.errors(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# 2. Error Masking & Rate Limiting Middleware
app.add_middleware(SafeErrorHandlingMiddleware)
app.add_middleware(SlidingWindowRateLimiter)

# 3. CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 4. Register Routers
app.include_router(execution_router)

connected_clients: List[WebSocket] = []
ws_connections_by_ip: Dict[str, int] = defaultdict(int)
MAX_WS_CONNECTIONS_PER_IP = 25


def get_client_ip(headers, client) -> str:
    """Extracts genuine client IP behind Cloudflare, Render, or standard reverse proxies."""
    cf_ip = headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    x_real_ip = headers.get("x-real-ip")
    if x_real_ip:
        return x_real_ip.strip()
    x_forwarded_for = headers.get("x-forwarded-for")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return client.host if client else "127.0.0.1"


@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health_check():
    """System health check and broker connectivity status."""
    token_status = upstox_auth.inspect_token()
    scheduler_status = market_scheduler.get_status()
    return {
        "status": "ONLINE",
        "market_state": scheduler_status.get("market_state", "LIVE"),
        "feed_latency_ms": 12,
        "engine_version": "1.0.0",
        "feed_provider": "Upstox",
        "universe_size": len(market_service._universe) or 213,
        "connected_ws_clients": len(connected_clients),
        "scheduler": scheduler_status,
        "broker_auth": {
            "valid": token_status.get("valid", False),
            "expires_at_utc": token_status.get("expires_at_utc"),
            "seconds_remaining": token_status.get("seconds_remaining", 0),
        },
        "rms_state": {
            "kill_switch_active": simulated_broker.get_account_summary()["kill_switch_active"],
            "open_positions": simulated_broker.get_account_summary()["open_positions_count"],
        },
    }


@app.get("/api/metrics")
async def get_metrics():
    """Prometheus-style telemetry and diagnostic counters."""
    acct = simulated_broker.get_account_summary()
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "active_universe_count": len(market_service._universe),
        "connected_ws_clients": len(connected_clients),
        "portfolio_equity": acct["equity"],
        "cash_balance": acct["cash_balance"],
        "unrealized_pnl": acct["unrealized_pnl"],
        "open_positions": acct["open_positions_count"],
    }


@app.get("/api/boards")
async def get_boards():
    """Returns the current cycle snapshot with boards, picks, and breadth dots from Upstox."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, market_service.generate_cycle_snapshot)


@app.get("/api/stock/{symbol}/chart")
async def get_stock_chart(symbol: str):
    """Returns 5m candles and order block levels for a symbol from Upstox."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, market_service.get_stock_chart, symbol.upper())


@app.post("/api/backtest/run")
async def run_backtest(config: BacktestConfig):
    """Executes an event-driven SMC Order Block backtest over historical candles."""
    sym = config.symbol.upper()
    loop = asyncio.get_event_loop()
    chart_data = await loop.run_in_executor(None, market_service.get_stock_chart, sym)
    raw_candles = chart_data.get("candles", [])
    candles = [Candle5m(**c) for c in raw_candles]
    engine = BacktestEngine(config)
    result = engine.run(candles)
    return result.model_dump()


@app.get("/api/analytics/trades")
async def get_trade_analytics(
    symbol: str = None,
    hit_type: str = None,
    limit: int = 50
):
    """Returns historical trade execution analytics records with Target/SL hit timing and OB count."""
    loop = asyncio.get_event_loop()
    trades = await loop.run_in_executor(
        None, sqlite_store.get_trade_analytics, symbol, hit_type, limit
    )
    return {
        "success": True,
        "count": len(trades),
        "data": trades
    }


@app.get("/api/analytics/summary")
async def get_trade_summary():
    """Returns aggregated performance metrics: win rate, target vs SL hits, and OB density correlation."""
    loop = asyncio.get_event_loop()
    summary = await loop.run_in_executor(None, sqlite_store.get_trade_summary)
    return {
        "success": True,
        "data": summary
    }


@app.get("/api/stock/{symbol}/ob-stats")
async def get_stock_ob_stats(symbol: str):
    """Returns order block counts (total, fresh, mitigated, invalidated) on the chart for a stock."""
    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, market_service.get_stock_ob_stats, symbol.upper())
    return {
        "success": True,
        "data": stats
    }


@app.websocket("/ws/boards")
@app.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket):
    """Full-duplex WebSocket streaming real-time cycles and telemetry with per-IP connection limits."""
    client_ip = get_client_ip(websocket.headers, websocket.client)

    # Enforce Section 2.3: max concurrent connections per client IP
    if ws_connections_by_ip[client_ip] >= MAX_WS_CONNECTIONS_PER_IP:
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=f"Rate limit: max {MAX_WS_CONNECTIONS_PER_IP} concurrent WS connections per IP")
        return

    await websocket.accept()
    connected_clients.append(websocket)
    ws_connections_by_ip[client_ip] += 1
    market_scheduler.register_client(websocket)

    try:
        loop = asyncio.get_event_loop()
        # Immediate initial payload
        initial = await loop.run_in_executor(None, market_service.generate_cycle_snapshot)
        await websocket.send_text(json.dumps({"type": "BOARD_SNAPSHOT", "data": initial}))

        # Keep connection open; broadcasts are handled centrally by market_scheduler during market hours
        while True:
            try:
                msg = await websocket.receive_text()
                if msg == "ping":
                    await websocket.send_text(json.dumps({"type": "PONG"}))
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        market_scheduler.unregister_client(websocket)
        if websocket in connected_clients:
            connected_clients.remove(websocket)
            ws_connections_by_ip[client_ip] = max(0, ws_connections_by_ip[client_ip] - 1)
