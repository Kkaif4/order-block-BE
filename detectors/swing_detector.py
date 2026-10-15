"""
OrderBlock Structural Analysis: Swing Point Detector
File: backend/detectors/swing_detector.py
Identifies confirmed Swing Highs and Swing Lows on completed 5-minute OHLCV bars.
Prevents look-ahead bias by requiring N confirmation bars before publishing.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from backend.core.schemas import Candle5m


@dataclass(frozen=True)
class SwingPoint:
    """Confirmed structural swing point."""
    timestamp: str             # Confirmation candle timestamp
    price: float               # Swing level price
    is_high: bool              # True for Swing High, False for Swing Low
    bar_index: int             # Bar index in session sequence


class SwingDetector:
    """Configurable swing high/low detector with strict confirmation window."""

    def __init__(self, left_bars: int = 2, right_bars: int = 2):
        if left_bars < 1 or right_bars < 1:
            raise ValueError("left_bars and right_bars must be at least 1.")
        self.left_bars = left_bars
        self.right_bars = right_bars

    def detect_swings(self, candles: List[Candle5m]) -> List[SwingPoint]:
        """Detect confirmed swing points up to the latest completed candle."""
        swings: List[SwingPoint] = []
        n = len(candles)
        window = self.left_bars + self.right_bars + 1
        if n < window:
            return swings

        # Only evaluate bars where right_bars are completed (no look-ahead bias)
        for i in range(self.left_bars, n - self.right_bars):
            candidate = candles[i]

            # Check Swing High
            is_high = True
            for j in range(i - self.left_bars, i + self.right_bars + 1):
                if j != i and candles[j].high >= candidate.high:
                    is_high = False
                    break
            if is_high:
                swings.append(SwingPoint(
                    timestamp=candles[i + self.right_bars].timestamp,
                    price=candidate.high,
                    is_high=True,
                    bar_index=i
                ))

            # Check Swing Low
            is_low = True
            for j in range(i - self.left_bars, i + self.right_bars + 1):
                if j != i and candles[j].low <= candidate.low:
                    is_low = False
                    break
            if is_low:
                swings.append(SwingPoint(
                    timestamp=candles[i + self.right_bars].timestamp,
                    price=candidate.low,
                    is_high=False,
                    bar_index=i
                ))

        return swings
