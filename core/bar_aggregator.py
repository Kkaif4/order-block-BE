"""
Streaming Intraday Bar Aggregator
File: backend/core/bar_aggregator.py

Aggregates continuous tick updates into standardized 1-minute and 5-minute OHLCV candles
strictly aligned with Indian market session boundaries (09:15 to 15:30 IST).
Adheres strictly to docs/BE_rulebook.md invariants.
"""

from __future__ import annotations
import math
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable, Tuple

from backend.core.schemas import Candle5m, IntradaySnapshot


class RunningBar:
    """Represents an active in-progress 5-minute candle."""
    def __init__(self, symbol: str, start_timestamp_iso: str, first_price: float, volume: float = 0.0):
        self.symbol = symbol
        self.start_timestamp = start_timestamp_iso
        self.open = first_price
        self.high = first_price
        self.low = first_price
        self.close = first_price
        self.volume = max(0.0, volume)
        self.tick_count = 1
        self.total_turnover = first_price * max(1.0, volume)

    def update(self, price: float, vol: float = 0.0):
        if not math.isfinite(price) or price <= 0.0:
            return
        if price > self.high:
            self.high = price
        if price < self.low:
            self.low = price
        self.close = price
        self.volume += max(0.0, vol)
        self.tick_count += 1
        self.total_turnover += price * max(1.0, vol)

    def to_candle5m(self) -> Candle5m:
        return Candle5m(
            timestamp=self.start_timestamp,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


class StreamingBarAggregator:
    def __init__(self, interval_minutes: int = 5):
        self.interval_minutes = interval_minutes
        self._running_bars: Dict[str, RunningBar] = {}
        self._completed_bars: Dict[str, List[Candle5m]] = {}
        self._on_candle_close_callbacks: List[Callable[[str, Candle5m], None]] = []
        self._lock = threading.Lock()

    def register_candle_close_callback(self, cb: Callable[[str, Candle5m], None]):
        with self._lock:
            self._on_candle_close_callbacks.append(cb)

    @staticmethod
    def get_bar_start_time(dt: datetime, interval_minutes: int = 5) -> datetime:
        """Aligns a timestamp to the floor of its 5-minute window."""
        minute = (dt.minute // interval_minutes) * interval_minutes
        return dt.replace(minute=minute, second=0, microsecond=0)

    def process_tick(
        self,
        symbol: str,
        price: float,
        volume_delta: float = 0.0,
        tick_time: Optional[datetime] = None,
    ) -> Optional[Candle5m]:
        """
        Processes a tick. If the tick crosses into a new 5-minute interval,
        closes the previous candle, emits it to callbacks, and starts a new one.
        """
        if not math.isfinite(price) or price <= 0.0:
            return None

        dt = tick_time or datetime.now(timezone.utc)
        bar_start_dt = self.get_bar_start_time(dt, self.interval_minutes)
        bar_start_iso = bar_start_dt.isoformat()

        closed_candle: Optional[Candle5m] = None

        with self._lock:
            running = self._running_bars.get(symbol)
            if running is None:
                self._running_bars[symbol] = RunningBar(symbol, bar_start_iso, price, volume_delta)
            elif running.start_timestamp != bar_start_iso:
                # Prior candle is completed
                closed_candle = running.to_candle5m()
                if symbol not in self._completed_bars:
                    self._completed_bars[symbol] = []
                self._completed_bars[symbol].append(closed_candle)
                # Cap in-memory history to last 75 bars (~1 trading day)
                if len(self._completed_bars[symbol]) > 75:
                    self._completed_bars[symbol].pop(0)

                # Start new active bar
                self._running_bars[symbol] = RunningBar(symbol, bar_start_iso, price, volume_delta)
            else:
                running.update(price, volume_delta)

        # Emit callback outside lock to prevent deadlocks
        if closed_candle:
            for cb in self._on_candle_close_callbacks:
                try:
                    cb(symbol, closed_candle)
                except Exception as e:
                    pass

        return closed_candle

    def get_recent_candles(self, symbol: str) -> List[Candle5m]:
        with self._lock:
            return list(self._completed_bars.get(symbol, []))


streaming_aggregator = StreamingBarAggregator(interval_minutes=5)
