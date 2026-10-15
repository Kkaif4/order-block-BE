"""
Automated Market Hours Quantitative Scheduler
File: backend/scheduler/market_scheduler.py

Runs the OrderBlock quantitative cycle and algorithmic trading pipeline
autonomously on the backend strictly during Indian Stock Market hours:
- Monday to Friday: 08:55 AM IST to 03:35 PM IST.
- Executes independent of browser tab state (even with 0 connected clients).
- Ingests Upstox market quotes, updates rolling baselines, computes strength rankings,
  detects SMC Order Blocks, updates open position mark-to-market ticks, and executes paper trades.
- Broadcasts real-time updates to any connected WebSockets.
"""

from __future__ import annotations
import asyncio
from datetime import datetime, time as dtime, timedelta, timezone
import json
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger("orderblock.scheduler")

# Indian Standard Time (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30), name="IST")
MARKET_START = dtime(8, 55)
MARKET_END = dtime(15, 35)


class MarketHoursScheduler:
    def __init__(self, cycle_interval_seconds: float = 5.0):
        self.cycle_interval = cycle_interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_cycle_timestamp: Optional[str] = None
        self._cycle_count: int = 0
        self._last_error: Optional[str] = None
        self._connected_clients: List[Any] = []
        self._last_logged_state: Optional[str] = None

    def is_market_hours(self, now_ist: Optional[datetime] = None) -> tuple[bool, str]:
        """Checks if current time is within Indian Stock Market operating window."""
        if now_ist is None:
            now = datetime.now(IST)
        elif now_ist.tzinfo is not None:
            now = now_ist.astimezone(IST)
        else:
            now = now_ist.replace(tzinfo=IST)

        weekday = now.weekday()  # Monday=0, Sunday=6
        day_name = now.strftime("%A")
        cur_time = now.time()

        if weekday in (5, 6):
            return False, f"Weekend ({day_name})"
        if cur_time < MARKET_START:
            return False, f"Pre-market off hours ({cur_time.strftime('%H:%M:%S')} IST < {MARKET_START.strftime('%H:%M')} IST)"
        if cur_time > MARKET_END:
            return False, f"Post-market off hours ({cur_time.strftime('%H:%M:%S')} IST > {MARKET_END.strftime('%H:%M')} IST)"
        return True, f"Active Market Hours ({day_name} {cur_time.strftime('%H:%M:%S')} IST)"

    async def start(self):
        """Starts the autonomous background loop."""
        if self._running:
            return
        self._running = True
        logger.info("[Scheduler] Starting Market Hours Autonomous Algorithm Scheduler...")
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """Stops the scheduler gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[Scheduler] Market Hours Scheduler stopped.")

    async def _run_loop(self):
        loop = asyncio.get_event_loop()
        # Avoid circular imports at module load
        from backend.services.market_service import market_service

        while self._running:
            try:
                in_market, reason = self.is_market_hours()

                if not in_market:
                    if self._last_logged_state != reason:
                        logger.info(f"[Scheduler] Standing by: {reason}. Algorithm paused to allow container sleep.")
                        self._last_logged_state = reason
                    # Sleep 30s before re-checking market hours
                    await asyncio.sleep(30.0)
                    continue

                if self._last_logged_state != "ACTIVE":
                    logger.info(f"[Scheduler] Market open ({reason}). Algorithm executing autonomously.")
                    self._last_logged_state = "ACTIVE"

                # Run cycle snapshot in threadpool executor so async event loop remains unblocked
                snapshot = await loop.run_in_executor(None, market_service.generate_cycle_snapshot)
                self._cycle_count += 1
                self._last_cycle_timestamp = datetime.now(timezone.utc).isoformat()
                self._last_error = None

                # Broadcast to any active client WebSockets
                if self._connected_clients:
                    await self._broadcast({"type": "BOARD_UPDATE", "data": snapshot})

                await asyncio.sleep(self.cycle_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_error = str(e)
                logger.error(f"[Scheduler] Error during execution cycle: {e}", exc_info=True)
                await asyncio.sleep(5.0)

    async def _broadcast(self, message: Dict[str, Any]):
        """Broadcasts payload to connected WebSocket terminals."""
        payload = json.dumps(message)
        dead_clients = []
        for ws in list(self._connected_clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead_clients.append(ws)

        for dead in dead_clients:
            if dead in self._connected_clients:
                self._connected_clients.remove(dead)

    def register_client(self, ws: Any):
        if ws not in self._connected_clients:
            self._connected_clients.append(ws)

    def unregister_client(self, ws: Any):
        if ws in self._connected_clients:
            self._connected_clients.remove(ws)

    def get_status(self) -> Dict[str, Any]:
        """Provides diagnostic telemetry for /api/health and observability."""
        in_market, reason = self.is_market_hours()
        return {
            "running": self._running,
            "in_market_hours": in_market,
            "market_state": reason,
            "cycle_count": self._cycle_count,
            "last_cycle_timestamp": self._last_cycle_timestamp,
            "last_error": self._last_error,
            "connected_ws_clients": len(self._connected_clients),
        }


# Singleton scheduler instance
market_scheduler = MarketHoursScheduler()
